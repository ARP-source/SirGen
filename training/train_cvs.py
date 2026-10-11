"""Train a Critical View of Safety (CVS) frame classifier on Endoscapes2023 CVS201.

Three outputs, one per Strasberg criterion, trained on the mean of the three
annotating surgeons (soft labels). Thresholds are picked on the validation
split and reported once on the held-out test split.

Usage (inside ~/sentinel-train/.venv):
    python train_cvs.py --epochs 20 --name cvs_convnext_t
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import torch
import torch.nn as nn
import torchvision.transforms.v2 as T
from torch.utils.data import DataLoader, Dataset
from torchvision.io import ImageReadMode, read_image
from torchvision.models import ConvNeXt_Tiny_Weights, convnext_tiny

ROOT = Path.home() / "datasets/endoscapes_small/cvs"
CRITERIA = ("c1_two_structures", "c2_hepatocystic_triangle", "c3_cystic_plate")
SIZE = (224, 400)
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)


class CVSFrames(Dataset):
    def __init__(self, split: str, train: bool):
        rows = [r for r in csv.DictReader(open(ROOT / "labels.csv")) if r["split"] == split]
        self.files = [ROOT / r["file"] for r in rows]
        self.y = torch.tensor([[float(r[c]) for c in CRITERIA] for r in rows])
        aug = [
            T.RandomResizedCrop(SIZE, scale=(0.75, 1.0), ratio=(1.6, 2.0), antialias=True),
            T.RandomHorizontalFlip(),
            T.ColorJitter(0.2, 0.2, 0.15, 0.02),
            T.RandomRotation(10),
        ] if train else [T.Resize(SIZE, antialias=True)]
        self.tf = T.Compose(aug + [T.ToDtype(torch.float32, scale=True), T.Normalize(MEAN, STD)])

    def __len__(self) -> int:
        return len(self.files)

    def __getitem__(self, i: int):
        return self.tf(read_image(str(self.files[i]), ImageReadMode.RGB)), self.y[i]


def _one_thread(_: int) -> None:
    torch.set_num_threads(1)


def build_model() -> nn.Module:
    m = convnext_tiny(weights=ConvNeXt_Tiny_Weights.IMAGENET1K_V1)
    m.classifier[2] = nn.Linear(m.classifier[2].in_features, len(CRITERIA))
    return m


def average_precision(scores: torch.Tensor, labels: torch.Tensor) -> float:
    order = torch.argsort(scores, descending=True)
    l = labels[order].float()
    if l.sum() == 0:
        return float("nan")
    tp = torch.cumsum(l, 0)
    precision = tp / torch.arange(1, len(l) + 1)
    return float((precision * l).sum() / l.sum())


def binary_stats(pred: torch.Tensor, lab: torch.Tensor) -> dict:
    tp = int((pred & lab).sum()); fp = int((pred & ~lab).sum())
    fn = int((~pred & lab).sum()); tn = int((~pred & ~lab).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    spec = tn / (tn + fp) if tn + fp else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4),
            "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
            "balanced_acc": round((r + spec) / 2, 4), "positives": tp + fn, "n": len(lab)}


@torch.no_grad()
def predict(model: nn.Module, loader: DataLoader, dev: str):
    model.eval()
    ps, ys = [], []
    for x, y in loader:
        with torch.autocast("cuda", dtype=torch.bfloat16):
            ps.append(torch.sigmoid(model(x.to(dev, non_blocking=True)).float()).cpu())
        ys.append(y)
    return torch.cat(ps), torch.cat(ys)


def best_threshold(p: torch.Tensor, lab: torch.Tensor) -> float:
    best, bt = -1.0, 0.5
    for t in torch.arange(0.05, 0.96, 0.01):
        f = binary_stats(p >= t, lab)["f1"]
        if f > best:
            best, bt = f, float(t)
    return round(bt, 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--name", default="cvs_convnext_t")
    a = ap.parse_args()
    dev = "cuda"
    out = Path("runs") / a.name
    out.mkdir(parents=True, exist_ok=True)

    tr = CVSFrames("train", True)
    kw = dict(batch_size=a.batch, num_workers=8, pin_memory=True, persistent_workers=True,
              multiprocessing_context="forkserver", worker_init_fn=_one_thread)
    tl = DataLoader(tr, shuffle=True, drop_last=True, **kw)
    vl = DataLoader(CVSFrames("val", False), **kw)
    sl = DataLoader(CVSFrames("test", False), **kw)

    model = build_model().to(dev).to(memory_format=torch.channels_last)
    pos = (tr.y >= 0.5).float().mean(0)
    crit = nn.BCEWithLogitsLoss(pos_weight=((1 - pos) / pos).clamp(max=6).to(dev))
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.05)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=a.epochs * len(tl), pct_start=0.1)

    best_map, history = -1.0, []
    for ep in range(a.epochs):
        model.train()
        t0, tot = time.time(), 0.0
        for x, y in tl:
            x = x.to(dev, non_blocking=True).to(memory_format=torch.channels_last)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = crit(model(x).float(), y.to(dev))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step(); sched.step()
            tot += loss.item()
        p, y = predict(model, vl, dev)
        aps = [average_precision(p[:, k], y[:, k] >= 0.5) for k in range(3)]
        m = sum(aps) / 3
        history.append({"epoch": ep + 1, "loss": tot / len(tl), "val_ap": aps, "val_map": m})
        print(f"epoch {ep+1}/{a.epochs} loss {tot/len(tl):.4f} val_ap {[round(v,3) for v in aps]} "
              f"mAP {m:.3f} {time.time()-t0:.0f}s", flush=True)
        if m > best_map:
            best_map = m
            torch.save(model.state_dict(), out / "best.pt")

    model.load_state_dict(torch.load(out / "best.pt", map_location=dev))
    pv, yv = predict(model, vl, dev)
    thr = [best_threshold(pv[:, k], yv[:, k] >= 0.5) for k in range(3)]
    pt, yt = predict(model, sl, dev)
    res = {"split": "test (Endoscapes2023 official, 40 held-out videos)", "thresholds_from_val": thr,
           "criteria": {}, "history": history}
    for k, c in enumerate(CRITERIA):
        lab = yt[:, k] >= 0.5
        res["criteria"][c] = {"ap": round(average_precision(pt[:, k], lab), 4),
                              **binary_stats(pt[:, k] >= thr[k], lab)}
    res["test_map"] = round(sum(v["ap"] for v in res["criteria"].values()) / 3, 4)
    all_lab = (yt >= 0.5).all(1)
    all_pred = torch.stack([pt[:, k] >= thr[k] for k in range(3)], 1).all(1)
    res["cvs_achieved_all_three"] = binary_stats(all_pred, all_lab)
    (out / "test_metrics.json").write_text(json.dumps(res, indent=2))
    (out / "meta.json").write_text(json.dumps({"arch": "convnext_tiny", "input_hw": SIZE, "mean": MEAN,
                                               "std": STD, "criteria": CRITERIA, "thresholds": thr}, indent=2))
    print("TEST_METRICS", json.dumps({k: v for k, v in res.items() if k != "history"}))


if __name__ == "__main__":
    main()
