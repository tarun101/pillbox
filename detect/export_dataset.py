#!/usr/bin/env python3
"""Export labelled cell crops as Train|Valid|Test / Full|Empty folders.

The pillbox-data repo stores only sources of truth — raw photos,
labels/labels.json and splits/*.txt. This regenerates the
Ultralytics-classify folder tree (the Roboflow export format the YOLO model
was trained on) from them on demand:

    <out>/Train/Full/<photo stem>_<DAY>_<SLOT>.jpg
    <out>/Train/Empty/...   <out>/Valid/...   <out>/Test/...

Crops are derived data: regenerating them keeps every export in sync with
label corrections and the current warp calibration, so don't commit the
output — re-run this instead (add export/ to pillbox-data's .gitignore, or
commit a snapshot deliberately and pin its commit in a model's card.json).

Usage:
    python3 detect/export_dataset.py --data ~/pillbox-data
        [--out <data>/export] [--clean]
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from detect import crop_cells  # noqa: E402

SPLIT_DIRS = {"train": "Train", "valid": "Valid", "test": "Test"}
CLASS_DIRS = {"pill": "Full", "empty": "Empty"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="pillbox-data checkout")
    ap.add_argument("--out", help="output root (default <data>/export)")
    ap.add_argument("--clean", action="store_true",
                    help="delete the output tree first (stale-file hygiene)")
    args = ap.parse_args()

    data = Path(args.data).expanduser()
    out = Path(args.out).expanduser() if args.out else data / "export"

    labels = json.loads((data / "labels" / "labels.json").read_text())
    split_of = {}
    for name, dirname in SPLIT_DIRS.items():
        f = data / "splits" / f"{name}.txt"
        if f.is_file():
            for s in f.read_text().splitlines():
                if s.strip():
                    split_of[s.strip()] = dirname
    photo_of = {p.stem: p for p in (data / "raw").rglob("photo_*.jpg")}

    by_stem = {}
    for key, lab in labels.items():
        stem, cell = key.split("/", 1)
        by_stem.setdefault(stem, {})[cell] = lab

    if args.clean and out.is_dir():
        shutil.rmtree(out)
    for sd in SPLIT_DIRS.values():
        for cd in CLASS_DIRS.values():
            (out / sd / cd).mkdir(parents=True, exist_ok=True)

    templates = crop_cells.build_matcher(
        Path(__file__).resolve().parent.parent / "images" / crop_cells.REF_IMAGE)

    counts = {sd: {cd: 0 for cd in CLASS_DIRS.values()}
              for sd in SPLIT_DIRS.values()}
    skipped = []
    for stem, cells in sorted(by_stem.items()):
        split = split_of.get(stem)
        if split is None:
            skipped.append((stem, "not in any split — run make_splits.py"))
            continue
        photo = photo_of.get(stem)
        if photo is None:
            skipped.append((stem, "photo missing under raw/"))
            continue
        img = cv2.imread(str(photo))
        if img is None:
            skipped.append((stem, "unreadable image"))
            continue
        quad, confs = crop_cells.align_quad(img, templates)
        if quad is None:
            skipped.append((stem, "pillbox not found (anchor conf "
                            + "/".join(f"{c:.2f}" for c in confs) + ")"))
            continue
        grid = crop_cells.warp_grid(img, quad)
        for day, slot, crop in crop_cells.cell_crops(grid):
            cell = f"{day}_{slot}"
            lab = cells.get(cell)
            if lab not in CLASS_DIRS:
                continue
            cls = CLASS_DIRS[lab]
            cv2.imwrite(str(out / split / cls / f"{stem}_{cell}.jpg"), crop)
            counts[split][cls] += 1

    for stem, why in skipped:
        print(f"skipped {stem}: {why}")
    total = 0
    for sd in SPLIT_DIRS.values():
        row = "  ".join(f"{cd} {counts[sd][cd]}" for cd in CLASS_DIRS.values())
        n = sum(counts[sd].values())
        total += n
        print(f"{sd:5s}: {row}  (total {n})")
    print(f"exported {total} cell crops -> {out}")


if __name__ == "__main__":
    main()
