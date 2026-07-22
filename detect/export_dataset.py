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

Augmentation (--augment N) reproduces the Roboflow-style expansion the paper's
YOLO was trained on: the ~1k labelled cells become ~2.7k "training images" by
emitting N images per Train crop (the unmodified original plus N-1 augmented
copies: horizontal flip, +/-15 deg rotation, exposure and contrast jitter).
Augmentation is applied to the Train split ONLY — Valid and Test stay 1x, so
evaluation is never inflated — and every copy is seeded deterministically from
its filename, so the export is byte-reproducible run to run. This keeps the
augmented training set reproducible from the committed sources of truth without
storing thousands of derived crops in the data repo. --augment 3 reproduces
roughly the 2,700-image set (643 Train cells x 3 + Valid + Test).

Usage:
    python3 detect/export_dataset.py --data ~/pillbox-data
        [--out <data>/export] [--clean] [--augment 3]
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from detect import crop_cells  # noqa: E402

SPLIT_DIRS = {"train": "Train", "valid": "Valid", "test": "Test"}
CLASS_DIRS = {"pill": "Full", "empty": "Empty"}


def _seed(stem, cell, i):
    """Stable per-copy seed (Python's hash() is salted, so hash the name)."""
    return int(hashlib.md5(f"{stem}/{cell}/{i}".encode()).hexdigest()[:8], 16)


def augment_crop(crop, rng):
    """One Roboflow-style augmented copy: flip, rotate/scale, exposure, contrast."""
    out = crop.astype(np.float32)
    h, w = out.shape[:2]
    if rng.rand() < 0.5:
        out = out[:, ::-1].copy()                       # horizontal flip
    m = cv2.getRotationMatrix2D((w / 2, h / 2),
                                rng.uniform(-15, 15), rng.uniform(0.9, 1.1))
    out = cv2.warpAffine(out, m, (w, h), borderMode=cv2.BORDER_REFLECT)
    out *= rng.uniform(0.75, 1.25)                       # exposure/brightness
    out = (out - out.mean()) * rng.uniform(0.85, 1.15) + out.mean()  # contrast
    return np.clip(out, 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="pillbox-data checkout")
    ap.add_argument("--out", help="output root (default <data>/export)")
    ap.add_argument("--clean", action="store_true",
                    help="delete the output tree first (stale-file hygiene)")
    ap.add_argument("--augment", type=int, default=1, metavar="N",
                    help="emit N images per Train crop (original + N-1 augmented "
                         "copies); Valid/Test always 1x. Default 1 (no augment). "
                         "Use 3 to reproduce the paper's ~2,700-image train set.")
    args = ap.parse_args()
    n_aug = max(1, args.augment)

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
    src_counts = {sd: 0 for sd in SPLIT_DIRS.values()}  # unaugmented cells
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
            dst = out / split / cls
            cv2.imwrite(str(dst / f"{stem}_{cell}.jpg"), crop)
            counts[split][cls] += 1
            src_counts[split] += 1
            if split == "Train" and n_aug > 1:
                for i in range(1, n_aug):
                    rng = np.random.RandomState(_seed(stem, cell, i))
                    aug = augment_crop(crop, rng)
                    cv2.imwrite(str(dst / f"{stem}_{cell}_aug{i}.jpg"), aug)
                    counts[split][cls] += 1

    for stem, why in skipped:
        print(f"skipped {stem}: {why}")
    total = 0
    for sd in SPLIT_DIRS.values():
        row = "  ".join(f"{cd} {counts[sd][cd]}" for cd in CLASS_DIRS.values())
        n = sum(counts[sd].values())
        total += n
        aug_note = ""
        if sd == "Train" and n_aug > 1:
            aug_note = f"  [{src_counts[sd]} cells x{n_aug}]"
        print(f"{sd:5s}: {row}  (total {n}){aug_note}")
    if n_aug > 1:
        print(f"augmentation: Train x{n_aug} (Valid/Test 1x), deterministic per crop")
    print(f"exported {total} cell crops -> {out}")


if __name__ == "__main__":
    main()
