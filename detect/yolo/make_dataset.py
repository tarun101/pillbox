#!/usr/bin/env python3
"""Build a YOLO detection dataset from the pillbox photos.

We have no hand-drawn boxes, so we generate them: each photo is warped to the
canonical top-down 7x3 grid (reusing detect/crop_cells), and inside every
cell that detect/labels.json marks as "pill" we run the same DoG blob detector
as the classifier baseline to find the pill blob(s) and emit YOLO boxes for
them. Cells labelled "empty" contribute no boxes. Training on the warped grid
(not the raw photo) keeps the loose pills scattered on the table — which we
never labelled — out of frame, so they can't poison training as unlabelled
positives.

One class: "pill". Split is by capture scene (photos within 3s share a scene)
so burst near-duplicates don't straddle train/val.

Usage:
    python3 detect/yolo/make_dataset.py [--out dataset/yolo] [--val-frac 0.25]

Writes an Ultralytics-style dataset: <out>/images/{train,val}/*.jpg,
<out>/labels/{train,val}/*.txt, and <out>/pillbox.yaml.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from detect import crop_cells  # noqa: E402

# DoG blob params (mirror classify_cells.py, tuned for the grid cell size)
MARGIN = 0.16
DOG_FINE, DOG_COARSE = 5, 21
TEXT_DARKNESS = 25
REF_DILATE, REF_GAIN = 15, 1.3
PIXEL_THRESHOLD = 12
MIN_BLOB_AREA = 60      # px in the CELL_W x CELL_H cell frame
CLOSE_K = 9             # merge blobs from one pill split by glare/text


def dog_response(bgr):
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[:, :, 0]
    text = (L < np.median(L) - TEXT_DARKNESS).astype(np.uint8)
    text = cv2.dilate(text, np.ones((9, 9), np.uint8))
    dog = np.zeros(L.shape, np.float32)
    for ch in range(3):
        c = lab[:, :, ch]
        dog += np.abs(cv2.GaussianBlur(c, (0, 0), DOG_FINE)
                      - cv2.GaussianBlur(c, (0, 0), DOG_COARSE))
    dog[text > 0] = 0
    return dog


def cell_boxes(cell_bgr, ref_bgr):
    """Return list of (x0,y0,x1,y1) pill boxes in cell-pixel coords."""
    h, w = cell_bgr.shape[:2]
    my, mx = int(h * MARGIN), int(w * MARGIN)
    inner = cell_bgr[my:h - my, mx:w - mx]
    rinner = ref_bgr[my:h - my, mx:w - mx]
    dog = dog_response(inner)
    ref = cv2.dilate(dog_response(rinner),
                     np.ones((REF_DILATE, REF_DILATE), np.uint8))
    mask = (np.maximum(0, dog - REF_GAIN * ref) > PIXEL_THRESHOLD).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            np.ones((CLOSE_K, CLOSE_K), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < MIN_BLOB_AREA:
            continue
        boxes.append((mx + x, my + y, mx + x + bw, my + y + bh))
    return boxes


def scene_ids(stems, gap_s=3):
    ids, scenes = {}, []
    for stem in sorted(stems):
        t = datetime.strptime(stem.replace(" copy", ""), "photo_%Y%m%d_%H%M%S")
        sid = next((s for s, last in scenes if abs((t - last).total_seconds()) <= gap_s), None)
        if sid is None:
            sid = len(scenes)
            scenes.append((sid, t))
        else:
            scenes = [(s, t if s == sid else lt) for s, lt in scenes]
        ids[stem] = sid
    return ids


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images", default="images")
    ap.add_argument("--cells", default="dataset/cells")
    ap.add_argument("--labels", default="detect/labels.json")
    ap.add_argument("--out", default="dataset/yolo")
    ap.add_argument("--val-frac", type=float, default=0.25)
    args = ap.parse_args()

    image_dir = Path(args.images)
    cells_dir = Path(args.cells)
    labels = json.load(open(args.labels))
    templates = crop_cells.build_matcher(image_dir / crop_cells.REF_IMAGE)

    # reference cells (empty box) for blob differencing
    ref_cells = {}
    for day in crop_cells.DAYS:
        for slot in crop_cells.SLOTS:
            p = cells_dir / Path(crop_cells.REF_IMAGE).stem / f"{day}_{slot}.jpg"
            ref_cells[f"{day}_{slot}"] = cv2.imread(str(p))

    stems = sorted({k.split("/")[0] for k in labels})
    sid = scene_ids(stems)
    n_scenes = max(sid.values()) + 1
    rng = np.random.RandomState(0)
    order = rng.permutation(n_scenes)
    n_val = max(1, round(n_scenes * args.val_frac))
    val_scenes = set(order[:n_val].tolist())

    out = Path(args.out)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    CW, CH = crop_cells.CELL_W, crop_cells.CELL_H
    GW, GH = crop_cells.GRID_W, crop_cells.GRID_H
    n_img = n_box = 0
    for stem in stems:
        img = cv2.imread(str(image_dir / f"{stem}.jpg"))
        quad, _ = crop_cells.align_quad(img, templates)
        if quad is None:
            print(f"skip {stem}: box not found")
            continue
        grid = crop_cells.warp_grid(img, quad)
        lines = []
        for r, slot in enumerate(crop_cells.SLOTS):
            for c, day in enumerate(crop_cells.DAYS):
                if labels.get(f"{stem}/{day}_{slot}") != "pill":
                    continue
                cell = grid[r * CH:(r + 1) * CH, c * CW:(c + 1) * CW]
                for x0, y0, x1, y1 in cell_boxes(cell, ref_cells[f"{day}_{slot}"]):
                    gx0, gy0 = c * CW + x0, r * CH + y0
                    gx1, gy1 = c * CW + x1, r * CH + y1
                    cx, cy = (gx0 + gx1) / 2 / GW, (gy0 + gy1) / 2 / GH
                    bw, bh = (gx1 - gx0) / GW, (gy1 - gy0) / GH
                    lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
                    n_box += 1
        split = "val" if sid[stem] in val_scenes else "train"
        name = stem.replace(" ", "_")
        cv2.imwrite(str(out / f"images/{split}/{name}.jpg"), grid)
        (out / f"labels/{split}/{name}.txt").write_text("\n".join(lines))
        n_img += 1

    yaml = (f"path: {out.resolve()}\n"
            f"train: images/train\nval: images/val\n"
            f"names:\n  0: pill\n")
    (out / "pillbox.yaml").write_text(yaml)
    n_tr = len(list((out / "images/train").glob("*.jpg")))
    print(f"{n_img} grid images ({n_tr} train / {n_img-n_tr} val), "
          f"{n_box} pseudo boxes -> {out}")


if __name__ == "__main__":
    main()
