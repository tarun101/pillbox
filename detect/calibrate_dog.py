#!/usr/bin/env python3
r"""Calibrate the DoG baseline's decision threshold on labeled data.

The DoG detector (classify_cells.py) has no trainable weights — a cell is
called "pill" when its difference-of-Gaussians blob score exceeds
AREA_THRESHOLD. "Training" DoG therefore means *fitting that threshold* to the
data. This scores every labeled cell, sweeps the threshold on the **train**
split (picking the value that maximizes macro-F1), and reports how that choice
does on **valid** and the held-out **test** split — so the number is chosen
without ever looking at the test set.

It reads cell crops produced by crop_cells.py and reuses the exact scoring path
classify_cells.analyze() uses (same reference cells, same margin).

Usage:
    python3 -m detect.calibrate_dog --cells dataset/cells \
        --labels ../pillbox-data/labels/labels.json \
        --splits-dir ../pillbox-data/splits [--apply]

--apply rewrites AREA_THRESHOLD in detect/classify_cells.py to the fitted value.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

from . import classify_cells

HERE = Path(__file__).resolve().parent


def macro_f1(y, pred):
    y, pred = np.asarray(y), np.asarray(pred)

    def f1(pos):
        tp = int(((pred == pos) & (y == pos)).sum())
        fp = int(((pred == pos) & (y != pos)).sum())
        fn = int(((pred != pos) & (y == pos)).sum())
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        return 2 * p * r / (p + r) if p + r else 0.0
    return (f1(1) + f1(0)) / 2


def acc(y, pred):
    return float((np.asarray(y) == np.asarray(pred)).mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cells", default="dataset/cells")
    ap.add_argument("--labels", default="detect/labels.json")
    ap.add_argument("--splits-dir", help="pillbox-data splits/ (train fits, "
                    "test held out); without it, fits on all data")
    ap.add_argument("--apply", action="store_true",
                    help="write the fitted threshold into classify_cells.py")
    args = ap.parse_args()

    cells_dir = Path(args.cells)
    labels = json.load(open(args.labels))
    split_of = {}
    if args.splits_dir:
        for name in ("train", "valid", "test"):
            f = Path(args.splits_dir) / f"{name}.txt"
            if f.is_file():
                for s in f.read_text().split():
                    if s.strip():
                        split_of[s.strip()] = name

    refs = classify_cells._load_refs()  # per-cell empty-box DoG response
    rows = []  # (split, score, y)
    missing = 0
    for key, lab in labels.items():
        stem, cell = key.split("/")
        crop = classify_cells.load_cell(cells_dir / stem / f"{cell}.jpg")
        if crop is None:
            missing += 1
            continue
        score = classify_cells.cell_score(crop, refs[cell])
        split = split_of.get(stem, "all")
        rows.append((split, score, 1 if lab == "pill" else 0))
    if not rows:
        sys.exit("error: no scored cells — did crop_cells.py run on these photos?")
    if missing:
        print(f"note: {missing} labeled cells had no crop on disk (skipped)")

    def part(name):
        s = np.array([r[1] for r in rows if r[0] == name])
        y = np.array([r[2] for r in rows if r[0] == name])
        return s, y

    fit_name = "train" if args.splits_dir else "all"
    s_fit, y_fit = part(fit_name)
    print(f"scored {len(rows)} cells; fitting on '{fit_name}' ({len(y_fit)} cells, "
          f"{int(y_fit.sum())} pill)")

    # sweep candidate thresholds (midpoints between sorted unique scores)
    uniq = np.unique(s_fit)
    cands = np.concatenate([[0.0], (uniq[:-1] + uniq[1:]) / 2, [uniq[-1] + 1e-6]])
    best_t = max(cands, key=lambda t: macro_f1(y_fit, s_fit > t))
    cur = classify_cells.AREA_THRESHOLD

    print(f"\n{'split':>7} {'n':>5} {'macroF1@cur':>11} {'macroF1@fit':>11} "
          f"{'acc@fit':>8}")
    for name in (["train", "valid", "test"] if args.splits_dir else ["all"]):
        s, y = part(name)
        if not len(y):
            continue
        print(f"{name:>7} {len(y):>5} {macro_f1(y, s > cur):>11.4f} "
              f"{macro_f1(y, s > best_t):>11.4f} {acc(y, s > best_t):>8.4f}")
    print(f"\ncurrent AREA_THRESHOLD = {cur}")
    print(f"fitted  AREA_THRESHOLD = {best_t:.5f}")

    if args.apply:
        f = HERE / "classify_cells.py"
        txt = f.read_text()
        new = re.sub(r"AREA_THRESHOLD = [0-9.]+",
                     f"AREA_THRESHOLD = {best_t:.5f}", txt, count=1)
        if new == txt:
            sys.exit("error: could not find AREA_THRESHOLD to update")
        f.write_text(new)
        print(f"\napplied: classify_cells.py AREA_THRESHOLD -> {best_t:.5f}")


if __name__ == "__main__":
    main()
