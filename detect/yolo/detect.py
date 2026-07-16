#!/usr/bin/env python3
"""Draw a box around every compartment and label it pill / no-pill.

Locates the box, warps to the canonical 7x3 grid, decides pill/no-pill for
each of the 21 compartments, draws each cell green (pill) or grey (no pill),
and prints the grid.

Two backends:
  * default — a trained YOLO grid-classification model (`build_gridset.py`).
    Honest heads-up: this from-scratch model classifies poorly (it collapses
    to "no_pill"); telling a pill from an empty tinted lid needs the
    empty-box reference, which plain RGB YOLO never sees. See the README.
  * --classifier — the shipped 6-channel classifier (../pipeline.py), which
    DOES compare against the empty reference and is ~88% accurate. Same
    visual, trustworthy verdicts. Recommended.

It also still handles the single-class pill detectors
(`make_dataset.py` / `build_handset.py`): those draw only the pill cells.

Usage:
    python3 detect/yolo/detect.py PHOTO.jpg --classifier --out out.jpg
    python3 detect/yolo/detect.py PHOTO.jpg [--weights ...] [--conf 0.25]
"""
import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from detect import crop_cells  # noqa: E402

DEFAULT_WEIGHTS = Path(__file__).parent.parent.parent / \
    "dataset/yolo_grid/runs/pill/weights/best.pt"

GREEN, GREY = (0, 200, 0), (150, 150, 150)


def is_pill_class(name):
    n = name.lower()
    return "pill" in n and "no" not in n and "empty" not in n


def verdict_from_classifier(photo):
    """(is_pill, prob) per (row, col) from the shipped 6-channel classifier."""
    from detect import pipeline
    result = pipeline.analyze(photo)   # {DAY_SLOT: {"pill": bool, "prob": float}}
    v = {}
    for r, slot in enumerate(crop_cells.SLOTS):
        for c, day in enumerate(crop_cells.DAYS):
            cell = result[f"{day}_{slot}"]
            v[(r, c)] = (cell["pill"], cell["prob"])
    return v, True   # multiclass=True -> draw every compartment


def verdict_from_yolo(grid, weights, conf):
    from ultralytics import YOLO
    model = YOLO(weights)
    names = model.names
    multiclass = any(not is_pill_class(n) for n in names.values())
    res = model.predict(grid, conf=conf, verbose=False)[0]
    CW, CH = crop_cells.CELL_W, crop_cells.CELL_H
    v = {}
    for box in res.boxes:
        x0, y0, x1, y1 = box.xyxy[0].tolist()
        bconf = float(box.conf[0])
        pill = is_pill_class(names[int(box.cls[0])])
        col, row = int((x0 + x1) / 2 // CW), int((y0 + y1) / 2 // CH)
        if not (0 <= col < 7 and 0 <= row < 3):
            continue
        if multiclass:
            if (row, col) not in v or bconf > v[(row, col)][1]:
                v[(row, col)] = (pill, bconf)
        elif pill:
            v[(row, col)] = (True, max(bconf, v.get((row, col), (0, 0))[1]))
    return v, multiclass


class AnalysisError(Exception):
    """Photo could not be analysed; str(exc) explains why."""


_model = None  # lazy YOLO singleton, loaded on first analyze()


def _load_model(weights=DEFAULT_WEIGHTS):
    global _model
    if _model is not None:
        return _model
    try:
        from ultralytics import YOLO
    except ImportError:
        raise AnalysisError(
            "ultralytics is not installed — run: pip install ultralytics")
    if not Path(weights).is_file():
        raise AnalysisError(
            f"YOLO weights not found at {weights} — train them with "
            "detect/yolo/train.py (they are not shipped in the repo)")
    _model = YOLO(str(weights))
    return _model


def analyze(photo_path, conf=0.25):
    """Analyse one photo with the trained YOLO pill detector.

    Locates and warps the box (same front-end as the CNN pipeline), runs
    YOLO on the grid, and marks a cell as "pill" if any detection's centre
    falls inside it (presence, not count). Returns
    {DAY_SLOT: {"pill": bool, "conf": float}}.
    """
    model = _load_model()
    img = cv2.imread(str(photo_path))
    if img is None:
        raise AnalysisError(f"cannot read image {photo_path}")
    ref_photo = Path(__file__).resolve().parent.parent.parent / \
        "images" / crop_cells.REF_IMAGE
    if not ref_photo.is_file():
        raise AnalysisError(f"reference photo not found at {ref_photo}")
    templates = crop_cells.build_matcher(ref_photo)
    quad, confs = crop_cells.align_quad(img, templates)
    if quad is None:
        conf_str = "/".join(f"{c:.2f}" for c in confs)
        raise AnalysisError(
            f"pillbox not found in photo (anchor confidence {conf_str}) — "
            "is the box in its usual spot?")
    grid = crop_cells.warp_grid(img, quad)
    res = model.predict(grid, conf=conf, verbose=False)[0]

    CW, CH = crop_cells.CELL_W, crop_cells.CELL_H
    out = {f"{day}_{slot}": {"pill": False, "conf": 0.0}
           for slot in crop_cells.SLOTS for day in crop_cells.DAYS}
    for box in res.boxes:
        x0, y0, x1, y1 = box.xyxy[0].tolist()
        c = float(box.conf[0])
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        col, row = int(cx // CW), int(cy // CH)
        if 0 <= col < 7 and 0 <= row < 3:
            key = f"{crop_cells.DAYS[col]}_{crop_cells.SLOTS[row]}"
            out[key]["pill"] = True
            out[key]["conf"] = max(out[key]["conf"], round(c, 3))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("photo")
    ap.add_argument("--classifier", action="store_true",
                    help="use the shipped classifier for verdicts (accurate)")
    ap.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--out", default="yolo_detect.jpg")
    args = ap.parse_args()

    img = cv2.imread(args.photo)
    if img is None:
        sys.exit(f"cannot read {args.photo}")
    templates = crop_cells.build_matcher(Path("images") / crop_cells.REF_IMAGE)
    quad, confs = crop_cells.align_quad(img, templates)
    if quad is None:
        sys.exit(f"pillbox not found (anchor conf "
                 f"{'/'.join(f'{c:.2f}' for c in confs)})")
    grid = crop_cells.warp_grid(img, quad)

    if args.classifier:
        verdict, multiclass = verdict_from_classifier(args.photo)
    else:
        verdict, multiclass = verdict_from_yolo(grid, args.weights, args.conf)

    CW, CH = crop_cells.CELL_W, crop_cells.CELL_H
    vis = grid.copy()
    for r in range(3):
        for c in range(7):
            is_pill, conf = verdict.get((r, c), (False, 0.0))
            if not multiclass and not is_pill:
                continue
            color = GREEN if is_pill else GREY
            x0, y0 = c * CW, r * CH
            cv2.rectangle(vis, (x0 + 4, y0 + 4), (x0 + CW - 4, y0 + CH - 4), color, 4)
            label = ("pill" if is_pill else "no pill") + (f" {conf:.2f}" if conf else "")
            cv2.putText(vis, label, (x0 + 10, y0 + 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    cv2.imwrite(args.out, vis)

    npill = sum(1 for v in verdict.values() if v[0])
    print(f"{npill}/21 compartments classified pill "
          f"({'classifier' if args.classifier else 'YOLO'})")
    for r, slot in enumerate(crop_cells.SLOTS):
        row = " ".join(f"{day}:{'#' if verdict.get((r, c), (False,))[0] else '.'}"
                       for c, day in enumerate(crop_cells.DAYS))
        print(f"  {slot:5s} {row}")
    print(f"annotated image -> {args.out}")


if __name__ == "__main__":
    main()
