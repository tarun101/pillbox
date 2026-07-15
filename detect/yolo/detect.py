#!/usr/bin/env python3
"""Run the trained YOLO pill detector on a photo and draw the boxes.

Locates the box and warps to the canonical grid (same front-end as the
classifier), runs YOLO on the grid, draws the detections, and reports which
of the 21 cells contain a detected pill (a cell is "pill" if any detection's
centre falls inside it — presence, not count).

Usage:
    python3 detect/yolo/detect.py PHOTO.jpg [--weights ...] [--out out.jpg]
                                  [--conf 0.25]
"""
import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from detect import crop_cells  # noqa: E402

DEFAULT_WEIGHTS = Path(__file__).parent.parent.parent / \
    "dataset/yolo/runs/pill/weights/best.pt"


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
    ap.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--out", default="yolo_detect.jpg")
    args = ap.parse_args()

    from ultralytics import YOLO

    img = cv2.imread(args.photo)
    if img is None:
        sys.exit(f"cannot read {args.photo}")
    templates = crop_cells.build_matcher(
        Path("images") / crop_cells.REF_IMAGE)
    quad, confs = crop_cells.align_quad(img, templates)
    if quad is None:
        sys.exit(f"pillbox not found (anchor conf "
                 f"{'/'.join(f'{c:.2f}' for c in confs)})")
    grid = crop_cells.warp_grid(img, quad)

    model = YOLO(args.weights)
    res = model.predict(grid, conf=args.conf, verbose=False)[0]

    CW, CH = crop_cells.CELL_W, crop_cells.CELL_H
    occupied = set()
    vis = grid.copy()
    for box in res.boxes:
        x0, y0, x1, y1 = box.xyxy[0].tolist()
        conf = float(box.conf[0])
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        col, row = int(cx // CW), int(cy // CH)
        if 0 <= col < 7 and 0 <= row < 3:
            occupied.add((row, col))
        cv2.rectangle(vis, (int(x0), int(y0)), (int(x1), int(y1)), (0, 0, 255), 3)
        cv2.putText(vis, f"{conf:.2f}", (int(x0), int(y0) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.imwrite(args.out, vis)

    print(f"{len(res.boxes)} pill detections; "
          f"{len(occupied)}/21 cells occupied")
    for r, slot in enumerate(crop_cells.SLOTS):
        row = " ".join(f"{day}:{'#' if (r, c) in occupied else '.'}"
                       for c, day in enumerate(crop_cells.DAYS))
        print(f"  {slot:5s} {row}")
    print(f"annotated image -> {args.out}")


if __name__ == "__main__":
    main()
