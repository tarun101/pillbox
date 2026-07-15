# YOLO pill detector (experiment)

A "for fun" alternative to the shipped classifier (`../`): instead of asking
per cell *is there a pill?*, this trains a real **YOLO object detector** that
draws a box around each pill it sees. It is **not** wired into the app and is
not needed on the Pi — the classifier remains the production path (see the
[detection README](../README.md) for why detection is the heavier tool for a
presence/absence question).

## Two datasets

- **`make_dataset.py` → `dataset/yolo/`** — the original, fully auto
  pseudo-labels (per individual pill). Fast, but inherits the blob detector's
  blind spots. Described in "How it works (auto pseudo-labels)" below.
- **`build_handset.py` → `dataset/yolo_hand/`** — a **hand-labelled** dataset
  where every box was human-verified and the camouflaged compartments were
  drawn by hand. This is the better dataset; see "Hand-labelled dataset".

## Hand-labelled dataset (`build_handset.py`)

Labelling philosophy: **one box per occupied compartment**, covering the pill
cluster — *not* one box per individual pill. That matches the goal (detect
whether a compartment holds a pill, not count them), stays complete when a
compartment is packed with overlapping pills, and is what can be labelled
reliably by eye.

Each box is built like this, for every compartment `../labels.json` marks
`pill`:

- compare the compartment to the same compartment of the empty-box reference
  photo and take the bounding box of the **changed region** (printed day/slot
  text masked out) — small for one pill, large for a packed compartment;
- compartments where the pill is the **same colour as its lid** change almost
  nothing, so those use **hand-drawn boxes** I placed by eye at high zoom,
  frozen in `camo_boxes.json` (36 boxes across 15 compartments).

The pill/empty status of every compartment comes from the hand-reviewed
`../labels.json`, so no occupied compartment is missed and no empty one is
boxed. `handset_labels.json` holds every resulting box for inspection.

```bash
pip install ultralytics
python3 detect/crop_cells.py               # cell crops (also builds the reference)
python3 detect/yolo/build_handset.py       # -> dataset/yolo_hand/  (418 boxes, 46 imgs)
python3 detect/yolo/train.py --data dataset/yolo_hand/pillbox.yaml \
    --project dataset/yolo_hand/runs
python3 detect/yolo/detect.py images/photo_20260713_145101.jpg \
    --weights dataset/yolo_hand/runs/pill/weights/best.pt --out demo.jpg
```

Honest limits: boxes were placed by visual review, not a pixel-drag tool, so
they're compartment-accurate rather than pixel-perfect; a box occasionally
includes a sliver of printed text where a pill sits on it. The truly
same-colour-as-lid pills are faint even at high zoom, so those ~15 hand boxes
are best-effort.

### Results

`yolov8n` from scratch, 200 epochs on CPU — clearly better than the auto
pseudo-label model, and the gain is exactly where it should be (recall):

| metric | auto pseudo-labels (#5) | hand-labelled |
|---|---|---|
| precision | 0.46 | 0.41 |
| recall | 0.21 | **0.30** |
| mAP@0.5 | 0.18 | **0.27** |
| mAP@0.5:0.95 | 0.06 | **0.11** |

On **held-out** photos it now boxes occupied compartments including the
packed box that the per-pill model handled worst — all three SAT
compartments, all three THU, the FRI/WED clusters:

![hand-labelled detector on held-out photos](demo/handset_detections.jpg)

It still misses some compartments (recall ~0.3) — ~38 scenes / 46 photos is
a small dataset and the from-scratch init (no COCO warm-start, GitHub blocked
in the sandbox) caps accuracy. More photos is the lever, as everywhere else
in this repo.

## How it works (auto pseudo-labels)

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

## Results

Trained from scratch (no COCO warm-start — see below), 200 epochs on CPU:

| metric | value |
|---|---|
| precision | 0.46 |
| recall | 0.21 |
| mAP@0.5 | 0.18 |
| mAP@0.5:0.95 | 0.06 |

Low by benchmark standards but it does what it says — here it is on a
**held-out** photo (`images/photo_20260713_145101.jpg`, a scene the model
never trained on), boxing the white pills through the purple/teal/blue lids:

![YOLO detections on a held-out photo](demo/demo_white_pills_heldout.jpg)

The recall of ~0.2 is visible too: pills in the yellow/green (WED/TUE) lids
are mostly missed — same-colour-as-lid pills make no blob, so the
pseudo-labeller never boxed them and the detector never learned them.

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
