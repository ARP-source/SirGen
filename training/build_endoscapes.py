"""Build compact training sets from Endoscapes2023 (CAMMA, CC BY-NC-SA 4.0).

Reads endoscapes.zip directly (no full extraction) and writes:
  <out>/anatomy_yolo/   YOLO detection set: cystic_plate, calot_triangle, cystic_artery,
                        cystic_duct, gallbladder, tool. Official train/val/test video splits.
  <out>/cvs/            Critical View of Safety frames + labels.csv (3 criteria, mean of 3 surgeons).

Images are re-encoded smaller so the set can be copied onto the GX10 over a slow link.

  python training/build_endoscapes.py --zip /tmp/endo/endoscapes.zip --out /tmp/endo/out
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from pathlib import Path

from PIL import Image

SPLITS = ("train", "val", "test")
DET_WIDTH = 640
CVS_WIDTH = 400


def resize(data: bytes, width: int) -> Image.Image:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.width != width:
        im = im.resize((width, round(im.height * width / im.width)), Image.BILINEAR)
    return im


def build_detection(z: zipfile.ZipFile, out: Path) -> None:
    root = out / "anatomy_yolo"
    names: list[str] = []
    for split in SPLITS:
        coco = json.loads(z.read(f"endoscapes/{split}/annotation_coco.json"))
        cats = sorted(coco["categories"], key=lambda c: c["id"])
        names = [c["name"] for c in cats]
        cat_index = {c["id"]: i for i, c in enumerate(cats)}
        by_img: dict[int, list[dict]] = {}
        for a in coco["annotations"]:
            by_img.setdefault(a["image_id"], []).append(a)
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)
        for img in coco["images"]:
            w, h = img["width"], img["height"]
            resize(z.read(f"endoscapes/{split}/{img['file_name']}"), DET_WIDTH).save(
                root / "images" / split / img["file_name"], quality=90)
            lines = []
            for a in by_img.get(img["id"], []):
                x, y, bw, bh = a["bbox"]
                if bw <= 1 or bh <= 1:
                    continue
                cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
                lines.append(f"{cat_index[a['category_id']]} {cx:.6f} {cy:.6f} {bw / w:.6f} {bh / h:.6f}")
            (root / "labels" / split / (Path(img["file_name"]).stem + ".txt")).write_text("\n".join(lines))
        print(f"detection {split}: {len(coco['images'])} images, {len(coco['annotations'])} boxes")
    (root / "data.yaml").write_text(
        "path: .\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n"
        + "".join(f"  {i}: {n}\n" for i, n in enumerate(names)))


def build_cvs(z: zipfile.ZipFile, out: Path) -> None:
    root = out / "cvs"
    rows = []
    for split in SPLITS:
        ds = json.loads(z.read(f"endoscapes/{split}/annotation_ds_coco.json"))
        (root / split).mkdir(parents=True, exist_ok=True)
        for img in ds["images"]:
            if not img.get("is_ds_keyframe"):
                continue
            resize(z.read(f"endoscapes/{split}/{img['file_name']}"), CVS_WIDTH).save(
                root / split / img["file_name"], quality=88)
            c1, c2, c3 = img["ds"]
            rows.append({"split": split, "file": f"{split}/{img['file_name']}", "video_id": img["video_id"],
                         "c1_two_structures": round(c1, 4), "c2_hepatocystic_triangle": round(c2, 4),
                         "c3_cystic_plate": round(c3, 4)})
        print(f"cvs {split}: {sum(r['split'] == split for r in rows)} frames")
    with open(root / "labels.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip) as z:
        build_detection(z, out)
        build_cvs(z, out)
        for name in ("LICENSE", "README.md"):
            (out / name).write_bytes(z.read(f"endoscapes/{name}"))


if __name__ == "__main__":
    main()
