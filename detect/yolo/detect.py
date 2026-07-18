#!/usr/bin/env python3
"""Classify every compartment pill / no-pill with the trained YOLO model.

The shipped model (`best.onnx`) is a YOLO *classification* net with two
classes, ``Empty`` / ``Full``. It is run on each of the 21 warped cell crops
(same front-end as the CNN pipeline: locate the box, warp to the canonical
7x3 grid, crop each cell), so this behaves like a per-cell presence detector.

It runs on **onnxruntime** — the same lightweight runtime the CNN uses — so
no PyTorch / ultralytics is needed at inference time. `best.onnx` (and its
`best.pt` source, kept alongside) were trained by Dylan P and brought into the
repo. To replace it, drop in a new ONNX classify model, or re-export a `.pt`
with ``yolo export model=best.pt format=onnx imgsz=640``.

Usage:
    python3 detect/yolo/detect.py PHOTO.jpg --out out.jpg
    python3 detect/yolo/detect.py PHOTO.jpg --classifier   # use the CNN instead
"""
import argparse
import ast
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from detect import crop_cells  # noqa: E402

# Runtime weights: the ONNX classifier shipped next to this module. (train.py
# writes a .pt under the gitignored dataset/; export it to ONNX to use it here.)
DEFAULT_WEIGHTS = Path(__file__).parent / "best.onnx"

GREEN, GREY = (0, 200, 0), (150, 150, 150)


def is_pill_class(name):
    """True for the class that means "a pill is present" (Full / pill)."""
    n = name.lower()
    if "empty" in n or "no" in n:
        return False
    return "full" in n or "pill" in n


class AnalysisError(Exception):
    """Photo could not be analysed; str(exc) explains why."""


# Lazy onnxruntime singleton, loaded on first use.
_session = None
_names = None       # {idx: class_name}
_pill_idx = None    # index of the "pill present" class
_imgsz = 640        # model's square input size, read from the graph


def _load_session(weights=DEFAULT_WEIGHTS):
    """Load the ONNX classifier once and cache it with its class metadata."""
    global _session, _names, _pill_idx, _imgsz
    if _session is not None:
        return _session
    try:
        import onnxruntime
    except ImportError:
        raise AnalysisError(
            "onnxruntime is not installed — run: pip install onnxruntime")
    if not Path(weights).is_file():
        raise AnalysisError(
            f"YOLO weights not found at {weights} — a trained best.onnx ships "
            "at detect/yolo/best.onnx; export your own with "
            "'yolo export model=best.pt format=onnx imgsz=640'")
    so = onnxruntime.SessionOptions()
    so.intra_op_num_threads = crop_cells.CPU_THREADS  # 0 -> onnxruntime default
    so.inter_op_num_threads = 1
    _session = onnxruntime.InferenceSession(
        str(weights), sess_options=so, providers=["CPUExecutionProvider"])
    meta = _session.get_modelmeta().custom_metadata_map
    _names = ast.literal_eval(meta["names"]) if "names" in meta else {0: "Empty", 1: "Full"}
    _pill_idx = next((i for i, n in _names.items() if is_pill_class(n)), max(_names))
    shape = _session.get_inputs()[0].shape  # [1, 3, H, W]
    if isinstance(shape[-1], int):
        _imgsz = shape[-1]
    return _session


def _classify(crop):
    """Return the softmax [P(class0), P(class1), ...] for one cell crop.

    Replicates ultralytics' classify transform: resize the shorter edge to
    the model size (bilinear), centre-crop to a square, scale to [0,1], RGB,
    CHW. The YOLO classify head already applies softmax, so the ONNX output
    is a probability vector.
    """
    h, w = crop.shape[:2]
    scale = _imgsz / min(h, w)
    r = cv2.resize(crop, (round(w * scale), round(h * scale)),
                   interpolation=cv2.INTER_LINEAR)
    top, left = (r.shape[0] - _imgsz) // 2, (r.shape[1] - _imgsz) // 2
    sq = r[top:top + _imgsz, left:left + _imgsz]
    blob = cv2.cvtColor(sq, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    blob = blob.transpose(2, 0, 1)[None]
    out = _session.run(None, {_session.get_inputs()[0].name: blob})[0]
    return out[0]


def _grid_from_photo(photo_path):
    """Locate the box and warp it to the canonical 7x3 grid, or raise."""
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
    return crop_cells.warp_grid(img, quad)


def analyze(photo_path):
    """Analyse one photo with the trained YOLO Empty/Full classifier.

    Locates and warps the box (same front-end as the CNN pipeline), then
    classifies each of the 21 cell crops. Returns
    {DAY_SLOT: {"pill": bool, "conf": float}}, where conf is P(Full).
    """
    _load_session()
    grid = _grid_from_photo(photo_path)
    CW, CH = crop_cells.CELL_W, crop_cells.CELL_H
    out = {}
    for r, slot in enumerate(crop_cells.SLOTS):
        for c, day in enumerate(crop_cells.DAYS):
            crop = grid[r * CH:(r + 1) * CH, c * CW:(c + 1) * CW]
            probs = _classify(crop)
            out[f"{day}_{slot}"] = {
                "pill": bool(int(probs.argmax()) == _pill_idx),
                "conf": round(float(probs[_pill_idx]), 3),
            }
    return out


def verdict_from_classifier(photo):
    """(is_pill, prob) per (row, col) from the shipped 6-channel CNN."""
    from detect import pipeline
    result = pipeline.analyze(photo)   # {DAY_SLOT: {"pill": bool, "prob": float}}
    v = {}
    for r, slot in enumerate(crop_cells.SLOTS):
        for c, day in enumerate(crop_cells.DAYS):
            cell = result[f"{day}_{slot}"]
            v[(r, c)] = (cell["pill"], cell["prob"])
    return v


def verdict_from_yolo(grid, weights=DEFAULT_WEIGHTS):
    """(is_pill, prob) per (row, col) from the YOLO Empty/Full classifier."""
    _load_session(weights)
    CW, CH = crop_cells.CELL_W, crop_cells.CELL_H
    v = {}
    for r in range(3):
        for c in range(7):
            crop = grid[r * CH:(r + 1) * CH, c * CW:(c + 1) * CW]
            probs = _classify(crop)
            v[(r, c)] = (int(probs.argmax()) == _pill_idx, float(probs[_pill_idx]))
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("photo")
    ap.add_argument("--classifier", action="store_true",
                    help="use the shipped 6-channel CNN for verdicts instead of YOLO")
    ap.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
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
        verdict = verdict_from_classifier(args.photo)
    else:
        verdict = verdict_from_yolo(grid, args.weights)

    CW, CH = crop_cells.CELL_W, crop_cells.CELL_H
    vis = grid.copy()
    for r in range(3):
        for c in range(7):
            is_pill, conf = verdict.get((r, c), (False, 0.0))
            color = GREEN if is_pill else GREY
            x0, y0 = c * CW, r * CH
            cv2.rectangle(vis, (x0 + 4, y0 + 4), (x0 + CW - 4, y0 + CH - 4), color, 4)
            label = ("pill" if is_pill else "no pill") + (f" {conf:.2f}" if conf else "")
            cv2.putText(vis, label, (x0 + 10, y0 + 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    cv2.imwrite(args.out, vis)

    npill = sum(1 for v in verdict.values() if v[0])
    print(f"{npill}/21 compartments classified pill "
          f"({'CNN' if args.classifier else 'YOLO'})")
    for r, slot in enumerate(crop_cells.SLOTS):
        row = " ".join(f"{day}:{'#' if verdict.get((r, c), (False,))[0] else '.'}"
                       for c, day in enumerate(crop_cells.DAYS))
        print(f"  {slot:5s} {row}")
    print(f"annotated image -> {args.out}")


if __name__ == "__main__":
    main()
