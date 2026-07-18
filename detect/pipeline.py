"""End-to-end pill-presence analysis for a single pillbox photo.

Ties the two stages together for callers like the web app's /status page:
locate the box and cut the 21 aligned cell crops (crop_cells), then classify
each crop with the trained CNN (pill_classifier.onnx, see
train_classifier.py). The model compares each cell against the same cell
from the empty-box reference shipped in detect/reference_cells/.

Usage:
    from detect import pipeline
    result = pipeline.analyze("/path/to/photo.jpg")
    # {"SAT_NIGHT": {"pill": True, "prob": 0.93}, ...}

Requires opencv-python(-headless), numpy and onnxruntime. Raises
pipeline.AnalysisError with a human-readable message when the photo can't be
analysed (box not found) or dependencies/model files are missing.
"""
from pathlib import Path

import cv2
import numpy as np

from . import crop_cells

MODEL_PATH = Path(__file__).parent / "pill_classifier.onnx"
REF_DIR = Path(__file__).parent / "reference_cells"

_session = None       # lazy singletons, loaded on first analyze()
_templates = None
_refs = None


class AnalysisError(Exception):
    """Photo could not be analysed; str(exc) explains why."""


def _input_size(session):
    _, ch, h, w = session.get_inputs()[0].shape
    if ch != 6:
        raise AnalysisError(f"unexpected model input channels: {ch}")
    return h, w


def _load():
    global _session, _templates, _refs
    if _session is not None:
        return
    try:
        import onnxruntime
    except ImportError:
        raise AnalysisError(
            "onnxruntime is not installed — run: pip install onnxruntime")
    if not MODEL_PATH.is_file():
        raise AnalysisError(f"model not found at {MODEL_PATH} — "
                            "run detect/train_classifier.py or pull it from git")
    so = onnxruntime.SessionOptions()
    so.intra_op_num_threads = crop_cells.CPU_THREADS  # 0 -> onnxruntime default
    so.inter_op_num_threads = 1
    _session = onnxruntime.InferenceSession(
        str(MODEL_PATH), sess_options=so, providers=["CPUExecutionProvider"])
    h, w = _input_size(_session)
    refs = {}
    for day in crop_cells.DAYS:
        for slot in crop_cells.SLOTS:
            p = REF_DIR / f"{day}_{slot}.jpg"
            img = cv2.imread(str(p))
            if img is None:
                raise AnalysisError(f"missing reference crop {p}")
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            refs[f"{day}_{slot}"] = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    _refs = refs
    # anchor templates for locating the box come from the repo's reference
    # photo; fall back to rebuilding them from the reference cells' source
    ref_photo = Path(__file__).parent.parent / "images" / crop_cells.REF_IMAGE
    if not ref_photo.is_file():
        raise AnalysisError(f"reference photo not found at {ref_photo}")
    _templates = crop_cells.build_matcher(ref_photo)


def analyze(photo_path):
    """Analyse one photo; returns {DAY_SLOT: {"pill": bool, "prob": float}}."""
    _load()
    img = cv2.imread(str(photo_path))
    if img is None:
        raise AnalysisError(f"cannot read image {photo_path}")
    quad, confs = crop_cells.align_quad(img, _templates)
    if quad is None:
        conf_str = "/".join(f"{c:.2f}" for c in confs)
        raise AnalysisError(
            f"pillbox not found in photo (anchor confidence {conf_str}) — "
            "is the box in its usual spot?")
    grid = crop_cells.warp_grid(img, quad)
    h, w = _input_size(_session)
    out = {}
    for day, slot, crop in crop_cells.cell_crops(grid):
        key = f"{day}_{slot}"
        crop = cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA)
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        x = np.concatenate([crop, _refs[key]], axis=2)
        x = x.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        logits = _session.run(None, {"image": x})[0][0]
        # softmax over [empty, pill]
        e = np.exp(logits - logits.max())
        p = float(e[1] / e.sum())
        out[key] = {"pill": bool(p > 0.5), "prob": round(p, 3)}
    return out
