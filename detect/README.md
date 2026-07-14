# Pill detection

Answers "does each of the 21 pillbox cells contain a pill?" for the photos
the camera app captures into `images/`.

## Pipeline

Two independent stages, run from the repo root:

```bash
pip install opencv-python-headless numpy

# 1. locate the box and cut each photo into 21 aligned cell crops
python3 detect/crop_cells.py --debug

# 2. score each cell for pill presence
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

## Known failure modes (spot-checked against the current photo set)

- **Pill the same colour as its lid** (yellow pill behind the yellow TUE
  lid): near-zero contrast through the plastic → missed (score ≈ 0.003 vs
  threshold 0.008).
- **Strong glare** on a lid occasionally scores just above threshold →
  false alarm (≈ 0.011–0.018).

Everything else — white/coloured pills behind purple, teal, blue, green,
orange and pink lids — separates cleanly (pill cells score 3–20× the
threshold, empty cells ≈ 0.000).

## Next step: small CNN classifier

The scores above make labelling cheap: sort the crops in `dataset/cells/`
into `pill/` and `empty/` folders (the baseline's verdicts are right ~90–95%
of the time, so it's a review job, not a labelling job), then fine-tune a
small image classifier (MobileNetV3 / ResNet-18 / `yolov8n-cls`) on the
crops. That learns per-column lid tints and glare patterns, which is exactly
what kills the two failure modes. Full-image object detection (YOLO-detect)
is *not* recommended here: the table is covered in loose pills and other
pillboxes, so localizing the box first is mandatory anyway, and per-cell
classification needs far less labelling effort than bounding boxes.
