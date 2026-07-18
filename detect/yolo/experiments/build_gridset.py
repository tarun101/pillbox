#!/usr/bin/env python3
"""Build the grid-classification YOLO dataset.

Framing: instead of localising pills, every one of the 21 compartments is a
**fixed full-cell box**, and YOLO's only job is to classify each box as
`pill` or `no_pill`. Because the box is always the whole compartment, there is
nothing to localise — this is per-compartment classification done with a
detector, which is robust (no dependence on how the pills sit) and needs no
hand-drawn boxes at all: the label of every cell comes straight from the
hand-reviewed ../labels.json.

Two classes:  0 = no_pill,  1 = pill.
Every image gets exactly 21 boxes (7 days x 3 slots), tiling the warped grid.

Outputs dataset/yolo_grid/ (git-ignored). Train/val split is by capture scene.

Usage:  python3 detect/yolo/build_gridset.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))
from detect import crop_cells  # noqa: E402

DAYS, SLOTS = crop_cells.DAYS, crop_cells.SLOTS
# each cell box slightly inset so neighbouring boxes don't share an edge
INSET = 0.98


def scene_ids(stems, gap_s=3):
    ids, scenes = {}, []
    for stem in sorted(stems):
        t = datetime.strptime(stem.replace(" copy", ""), "photo_%Y%m%d_%H%M%S")
        sid = next((s for s, last in scenes if abs((t - last).total_seconds()) <= gap_s), None)
        if sid is None:
            sid = len(scenes); scenes.append((sid, t))
        else:
            scenes = [(s, t if s == sid else lt) for s, lt in scenes]
        ids[stem] = sid
    return ids


def main():
    labels = json.load(open(ROOT / "detect/labels.json"))
    templates = crop_cells.build_matcher(ROOT / "images" / crop_cells.REF_IMAGE)
    stems = sorted({k.split("/")[0] for k in labels})
    sid = scene_ids(stems)
    n_scenes = max(sid.values()) + 1
    perm = np.random.RandomState(0).permutation(n_scenes)
    val = set(perm[:max(1, round(n_scenes * 0.25))].tolist())

    out = ROOT / "dataset/yolo_grid"
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    npill = nempty = nimg = 0
    for stem in stems:
        img = cv2.imread(str(ROOT / "images" / f"{stem}.jpg"))
        quad, _ = crop_cells.align_quad(img, templates)
        if quad is None:
            print(f"skip {stem}: box not found"); continue
        grid = crop_cells.warp_grid(img, quad)
        lines = []
        for r, slot in enumerate(SLOTS):
            for c, day in enumerate(DAYS):
                cls = 1 if labels.get(f"{stem}/{day}_{slot}") == "pill" else 0
                cx = (c + 0.5) / 7
                cy = (r + 0.5) / 3
                w = INSET / 7
                h = INSET / 3
                lines.append(f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
                npill += cls
                nempty += (1 - cls)
        split = "val" if sid[stem] in val else "train"
        name = stem.replace(" ", "_")
        cv2.imwrite(str(out / f"images/{split}/{name}.jpg"), grid)
        (out / f"labels/{split}/{name}.txt").write_text("\n".join(lines))
        nimg += 1

    (out / "pillbox.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\n"
        f"names:\n  0: no_pill\n  1: pill\n")
    ntr = len(list((out / "images/train").glob("*.jpg")))
    print(f"{nimg} images ({ntr} train / {nimg - ntr} val), "
          f"{npill} pill cells / {nempty} no_pill cells (21 boxes each)")
    print(f"dataset -> {out}")


if __name__ == "__main__":
    main()
