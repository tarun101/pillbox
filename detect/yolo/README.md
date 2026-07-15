# YOLO pill detector (experiment)

A "for fun" alternative to the shipped classifier (`../`): instead of asking
per cell *is there a pill?*, this trains a real **YOLO object detector** that
draws a box around each pill it sees. It is **not** wired into the app and is
not needed on the Pi — the classifier remains the production path (see the
[detection README](../README.md) for why detection is the heavier tool for a
presence/absence question).

## How it works

Detectors need bounding-box labels, which this dataset never had. So the
boxes are **generated, not hand-drawn**:

1. `make_dataset.py` warps each photo to the canonical top-down 7×3 grid
   (reusing `../crop_cells.py`) and, inside every cell that `../labels.json`
   marks `pill`, runs the classifier's DoG blob detector to find the pill
   blob(s) and writes YOLO boxes for them. Empty cells get no boxes.
   Training on the **warped grid** (not the raw photo) keeps the loose pills
   scattered across the table — which were never labelled — out of frame, so
   they can't act as unlabelled positives.
2. `train.py` fine-tunes / trains `yolov8n` on the single `pill` class.
3. `detect.py` runs the trained model on a new photo (locate → warp → YOLO)
   and reports which of the 21 cells have a detected pill (presence by
   detection centre — not a count).

```bash
pip install ultralytics
python3 detect/crop_cells.py            # need the cell crops first
python3 detect/yolo/make_dataset.py     # -> dataset/yolo/ (~1200 pseudo boxes)
python3 detect/yolo/train.py            # -> dataset/yolo/runs/pill/weights/best.pt
python3 detect/yolo/detect.py images/photo_20260713_145101.jpg --out demo.jpg
```

## Caveats (it's a toy)

- **Pseudo-labels inherit the baseline's blind spots**: a pill the same
  colour as its lid produces no blob, so it's unlabelled and the detector
  never learns to see it — the exact case the 6-channel classifier was built
  to fix.
- **Trained from scratch.** The COCO-pretrained `yolov8n.pt` is downloaded
  from GitHub, which was blocked in the build sandbox, so `train.py` defaults
  to `yolov8n.yaml` (random init). With only ~35 training grids that limits
  accuracy; pass `--weights yolov8n.pt` for a warm start if you have the file.
- **Only ~18 distinct capture scenes** back the whole thing, split by scene
  into train/val — so treat the metrics as a demo, not a benchmark.

For real per-cell status, use the classifier and the app's `/status` page.
This folder is here because a YOLO version is a fun thing to see draw boxes.
