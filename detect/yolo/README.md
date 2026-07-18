# YOLO detector

The app's third per-cell detector, next to DoG and the CNN. Despite the name,
the shipped model is a YOLO **classification** net — not an object detector:
two classes, `Empty` / `Full`, run once per compartment.

**Model credit:** `best.onnx` (and its `best.pt` source) were **trained by
Dylan P** and brought into this repo.

## What ships

| file | role |
|---|---|
| **`best.onnx`** | the runtime model — the ONNX export used at inference |
| `best.pt` | the source it was exported from; kept for provenance / re-export (not loaded at runtime) |
| `detect.py` | loads `best.onnx` with **onnxruntime** and exposes `analyze()` |

`detect.py` runs on **onnxruntime**, the same lightweight runtime the CNN
uses, so **no PyTorch / ultralytics is needed on the Pi**. `analyze(photo)`
locates the box, warps it to the canonical 7×3 grid, and classifies each of
the 21 cell crops `Empty` / `Full`, returning
`{DAY_SLOT: {"pill": bool, "conf": P(Full)}}`. The app calls it for the
**YOLO** column on `/status` and the gallery **Analyze** view. The class names
and input size are read from the ONNX graph, so nothing about the grid or
classes is hard-coded.

## Run it

```bash
# per-cell verdicts drawn on the grid (uses best.onnx via onnxruntime)
python3 detect/yolo/detect.py images/photo_20260713_145101.jpg --out demo.jpg
# same visual, but take the verdicts from the 6-channel CNN instead
python3 detect/yolo/detect.py images/photo_20260713_145101.jpg --classifier --out demo.jpg
```

Runtime deps: `opencv-python(-headless)`, `numpy`, `onnxruntime` (all already
needed by the CNN).

## Replace the model

Drop in a new `best.onnx` — any Ultralytics classify export whose two classes
name the "pill present" one with `full` or `pill` (e.g. `Empty` / `Full`).
From a `.pt`:

```bash
yolo export model=best.pt format=onnx imgsz=640
```
