#!/usr/bin/env python3
"""Materialise the hand-labelled YOLO dataset.

Labelling philosophy: **one box per occupied compartment**, covering the pill
cluster in that cell — not one box per individual pill. This matches the goal
(detect whether a compartment has a pill, not count them), stays complete even
when a compartment is packed with overlapping pills, and is what a human can
label reliably.

How each box is produced, per compartment that detect/labels.json marks
`pill`:
  * the compartment is compared to the same compartment of the empty-box
    reference photo; the bounding box of the region that changed (printed
    day/slot text masked out) is the pill-cluster box;
  * compartments where a pill is the same colour as its lid produce almost no
    measurable change, so those use hand-drawn boxes stored in
    detect/yolo/camo_boxes.json (I placed these by eye at high zoom).

Outputs the Ultralytics dataset to dataset/yolo_hand/ (git-ignored, ~46 warped
grid images + labels), and writes detect/yolo/handset_labels.json (every box,
inspectable) next to this script. Train/val split is by capture scene.

Usage:  python3 detect/yolo/build_handset.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from detect import crop_cells  # noqa: E402

DAYS, SLOTS = crop_cells.DAYS, crop_cells.SLOTS
gCW, gCH = crop_cells.CELL_W, crop_cells.CELL_H
MARGIN = 0.12
CHANGE_THRESHOLD = 26        # Lab distance vs. reference that counts as "changed"
MIN_COMPONENT_FRAC = 0.006   # ignore change blobs smaller than this frac of the cell


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


def text_mask(ref_inner):
    L = cv2.cvtColor(ref_inner, cv2.COLOR_BGR2LAB)[:, :, 0]
    return cv2.dilate((L < np.median(L) - 22).astype(np.uint8), np.ones((13, 13), np.uint8))


def cluster_box(cell, ref):
    """Bounding box (cell-fraction) of the changed-vs-reference region, or None."""
    h, w = cell.shape[:2]
    my, mx = int(h * MARGIN), int(w * MARGIN)
    ci = cv2.GaussianBlur(cell[my:h - my, mx:w - mx], (7, 7), 0)
    ri = cv2.GaussianBlur(ref[my:h - my, mx:w - mx], (7, 7), 0)
    lc = cv2.cvtColor(ci, cv2.COLOR_BGR2LAB).astype(np.float32)
    lr = cv2.cvtColor(ri, cv2.COLOR_BGR2LAB).astype(np.float32)
    lc += (np.median(lr, (0, 1)) - np.median(lc, (0, 1)))       # cancel exposure drift
    mask = (np.linalg.norm(lc - lr, axis=2) > CHANGE_THRESHOLD).astype(np.uint8)
    mask[text_mask(ref[my:h - my, mx:w - mx]) > 0] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(mask)
    ih, iw = mask.shape
    comps = [st[i] for i in range(1, n) if st[i][4] >= MIN_COMPONENT_FRAC * ih * iw]
    if not comps:
        return None
    x0 = min(c[0] for c in comps); y0 = min(c[1] for c in comps)
    x1 = max(c[0] + c[2] for c in comps); y1 = max(c[1] + c[3] for c in comps)
    return [(mx + x0) / w, (my + y0) / h, (mx + x1) / w, (my + y1) / h]


def main():
    labels = json.load(open(ROOT / "detect/labels.json"))
    camo = json.load(open(HERE / "camo_boxes.json"))   # scene idx -> {DAY_SLOT: [[fx0,fy0,fx1,fy1],..]}
    templates = crop_cells.build_matcher(ROOT / "images" / crop_cells.REF_IMAGE)
    ref_cells = {f"{d}_{s}": cv2.imread(str(ROOT / "dataset/cells" /
                 Path(crop_cells.REF_IMAGE).stem / f"{d}_{s}.jpg"))
                 for d in DAYS for s in SLOTS}
    if any(v is None for v in ref_cells.values()):
        sys.exit("reference cell crops missing — run detect/crop_cells.py first")

    stems = sorted({k.split("/")[0] for k in labels})
    sid = scene_ids(stems)
    n_scenes = max(sid.values()) + 1
    perm = np.random.RandomState(0).permutation(n_scenes)
    val = set(perm[:max(1, round(n_scenes * 0.25))].tolist())

    out = ROOT / "dataset/yolo_hand"
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    frozen = {}
    nbox = ncamo = nimg = 0
    for stem in stems:
        img = cv2.imread(str(ROOT / "images" / f"{stem}.jpg"))
        quad, _ = crop_cells.align_quad(img, templates)
        if quad is None:
            print(f"skip {stem}: box not found"); continue
        grid = crop_cells.warp_grid(img, quad)
        lines = []
        for r, slot in enumerate(SLOTS):
            for c, day in enumerate(DAYS):
                if labels.get(f"{stem}/{day}_{slot}") != "pill":
                    continue
                box = cluster_box(grid[r * gCH:(r + 1) * gCH, c * gCW:(c + 1) * gCW],
                                  ref_cells[f"{day}_{slot}"])
                if box is None:
                    hb = camo.get(str(sid[stem]), {}).get(f"{day}_{slot}")
                    if hb:
                        box = [min(b[0] for b in hb), min(b[1] for b in hb),
                               max(b[2] for b in hb), max(b[3] for b in hb)]
                        ncamo += 1
                    else:
                        box = [0.3, 0.35, 0.7, 0.75]
                fx0, fy0, fx1, fy1 = box
                cx = (c + (fx0 + fx1) / 2) / 7; cy = (r + (fy0 + fy1) / 2) / 3
                w = (fx1 - fx0) / 7; h = (fy1 - fy0) / 3
                lines.append(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"); nbox += 1
        split = "val" if sid[stem] in val else "train"
        name = stem.replace(" ", "_")
        cv2.imwrite(str(out / f"images/{split}/{name}.jpg"), grid)
        (out / f"labels/{split}/{name}.txt").write_text("\n".join(lines))
        frozen[name] = {"split": split, "boxes": lines}
        nimg += 1

    (out / "pillbox.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: pill\n")
    json.dump(frozen, open(HERE / "handset_labels.json", "w"), indent=1)
    ntr = len(list((out / "images/train").glob("*.jpg")))
    print(f"{nimg} images ({ntr} train / {nimg - ntr} val), {nbox} boxes "
          f"(1 per occupied compartment), {ncamo} camouflaged from hand boxes")
    print(f"dataset -> {out}\nlabels  -> {HERE/'handset_labels.json'}")


if __name__ == "__main__":
    main()
