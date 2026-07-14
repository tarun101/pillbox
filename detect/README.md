# Pill detection

Answers "does each of the 21 pillbox cells contain a pill?" for the photos
the camera app captures into `images/`.

There are two classifiers:

- a **CNN** (`pill_classifier.onnx`, used by the app's `/status` page and by
  `pipeline.analyze()`) — the accurate one; needs `onnxruntime` to run and
  `torch` only to retrain;
- a **DoG baseline** (`classify_cells.py`) — no model file needed; used to
  bootstrap the training labels and still handy as a sanity check.

## Quick start (inference)

```bash
pip install opencv-python-headless numpy onnxruntime
python3 -c "
from detect import pipeline
print(pipeline.analyze('images/photo_20260713_145101.jpg'))"
```

The web app's `/status` page calls the same `pipeline.analyze()` on the
latest capture and renders the 7×3 grid.

## Dataset pipeline

Two independent stages, run from the repo root:

```bash
pip install opencv-python-headless numpy

# 1. locate the box and cut each photo into 21 aligned cell crops
python3 detect/crop_cells.py --debug

# 2. score each cell for pill presence (DoG baseline)
python3 detect/classify_cells.py --annotate dataset/annotated
```

Results land in `dataset/results.json`:

```json
{
  "photo_20260713_145101": {
    "SAT_NIGHT": {"pill": true, "score": 0.081},
    "...": {}
  }
}
```

`--debug` / `--annotate` write visual QA images (grid overlays on the
originals, and per-cell green/red verdicts) so misfires are easy to spot.

### Stage 1 — `crop_cells.py`

The camera and box sit in a fixed jig, so the cell grid was calibrated once
against a reference photo (`REF_QUAD` in the script). Each new photo is
aligned to the reference by template-matching two anchor patches (left and
right halves of the box), which recovers the small translation/rotation from
re-placing the box; the calibrated quad is then warped to a canonical
top-down grid and sliced into 7×3 cells named `<DAY>_<SLOT>.jpg`.
Columns are SAT→SUN left-to-right (the printed labels face away from the
camera); rows are NIGHT / NOON / MORN top-to-bottom.

On the current 46 photos this aligns 46/46 with match confidence ≥ 0.84,
including shots with a rotated box, heavy table clutter and a hand in frame.

### Stage 2 — `classify_cells.py`

Pills seen through the tinted lids are compact local blobs; the confounders
(auto-exposure drift, white-balance shifts, specular highlights sliding
across the glossy lids) are smooth and low-frequency. Each cell is therefore
scored by band-pass blob energy: |difference-of-Gaussians| per Lab channel
over the cell interior, printed text masked out, minus the same cell's
response in a reference photo of the empty box. A cell is called "pill" when
more than `AREA_THRESHOLD` of its area has residual blob energy.

If you re-shoot the reference/empty photo, update `REF_IMAGE` in
`crop_cells.py` (recalibrate `REF_QUAD` if the jig moved) and `REF_STEM` in
`classify_cells.py`.

### Baseline failure modes (spot-checked against the current photo set)

- **Pill the same colour as its lid** (yellow pill behind the yellow TUE
  lid): near-zero contrast through the plastic → missed (score ≈ 0.003 vs
  threshold 0.008).
- **Strong glare** on a lid occasionally scores just above threshold →
  false alarm (≈ 0.011–0.018).

Against the hand-reviewed labels the baseline gets 88.3% of cells right;
almost all of its misses are camouflaged same-colour pills.

## CNN classifier

`train_classifier.py` trains a small from-scratch CNN (~300 KB as ONNX) on
the labelled crops and exports `pill_classifier.onnx` plus the 21
`reference_cells/` crops needed at inference:

```bash
pip install torch          # training only; inference needs onnxruntime
python3 detect/crop_cells.py
python3 detect/train_classifier.py
```

Design notes:

- The network sees **six channels**: the cell crop *and* the same cell from
  an empty-box reference photo. That comparison is what makes camouflaged
  pills separable — they are exactly the cells where a lone crop carries
  almost no signal.
- Augmentation applies identical geometric jitter to both halves but
  independent photometric jitter (auto-exposure really does differ between
  shots).
- Train/val split is by capture *scene* (photos seconds apart are
  near-duplicates and must not straddle the split).

`labels.json` holds the 946 hand-reviewed cell labels (bootstrapped from the
baseline scores; ~20 genuinely ambiguous cells were dropped). If you add new
photos: run `crop_cells.py`, extend `labels.json` (the baseline's verdicts
are right ~90% of the time, so it's a review job, not a labelling job),
retrain, and commit the new ONNX.

### Accuracy (against the hand-reviewed labels)

| | scene-held-out val (224 cells) | all 946 cells |
|---|---|---|
| DoG baseline | — | 88.3% (78 FN / 33 FP) |
| CNN (shipped) | **87.9%** | 87.7% (56 FN / 60 FP) |

Overall accuracy is comparable, but the error *profiles* differ: the
baseline's misses are systematic (camouflaged same-colour pills — 33 of its
78 misses are TUE cells), while the CNN's errors are spread out and its val
number is measured on scenes it never saw. Larger models / higher input
resolution did not help (86.2% val) — with only ~18 distinct capture scenes,
**more photos is the lever that will improve this**, not architecture.
Expect roughly 2–3 wrong cells per 21-cell photo for now; the `/status`
tooltips show per-cell confidence.
