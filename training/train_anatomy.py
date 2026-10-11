"""Train the Endoscapes2023 anatomy detector (6 classes) on the GX10.

Usage (inside ~/sentinel-train/.venv):
    python train_anatomy.py --model weights/yolo26m.pt --epochs 150 --name anat_m
    python train_anatomy.py --smoke          # 1 short epoch, checks the GPU path

Evaluation on the held-out test split runs automatically at the end and is
written to runs/<name>/test_metrics.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ultralytics import YOLO

DATA = Path.home() / "datasets/endoscapes_small/anatomy_yolo/data.yaml"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="weights/yolo26m.pt")
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--name", default="anat_m")
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()

    model = YOLO(a.model)
    extra = dict(epochs=1, fraction=0.1, name="smoke", plots=False) if a.smoke else dict(
        epochs=a.epochs, name=a.name, patience=40
    )
    model.train(
        data=str(DATA),
        imgsz=a.imgsz,
        batch=a.batch,
        project=str(Path("runs").resolve()),
        exist_ok=True,
        workers=8,
        cos_lr=True,
        close_mosaic=15,
        degrees=10.0,
        hsv_h=0.01,
        fliplr=0.5,
        mixup=0.1,
        **extra,
    )
    if a.smoke:
        return

    best = Path("runs") / a.name / "weights/best.pt"
    m = YOLO(str(best))
    r = m.val(data=str(DATA), split="test", imgsz=a.imgsz, plots=True,
              project=str(Path("runs").resolve()), name=f"{a.name}_test", exist_ok=True)
    names = r.names
    per_class = {names[i]: {"ap50": float(r.box.ap50[j]), "ap50_95": float(r.box.ap[j])}
                 for j, i in enumerate(r.box.ap_class_index)}
    out = {
        "split": "test (Endoscapes2023 official, held-out videos)",
        "map50": float(r.box.map50),
        "map50_95": float(r.box.map),
        "per_class": per_class,
        "weights": str(best),
    }
    (Path("runs") / a.name / "test_metrics.json").write_text(json.dumps(out, indent=2))
    print("TEST_METRICS", json.dumps(out))


if __name__ == "__main__":
    main()
