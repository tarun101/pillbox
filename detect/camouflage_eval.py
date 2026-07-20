#!/usr/bin/env python3
r"""Camouflage analysis: pill-to-lid colour difference (ΔE) vs model accuracy.

This is the script behind the paper's "Effect of Camouflage" section
(Figure 5). It answers two questions the paper poses but does not yet
measure:

  1. HOW CAMOUFLAGED is each cell?  For every *Full* cell we compute the
     colour difference between the pill and the tinted lid above it, as a
     perceptual CIELAB ΔE (CIEDE2000), plus a 0-100 % normalised version.
     A pill the same colour as its lid gives ΔE ≈ 0 (fully camouflaged); a
     dark pill behind a pale lid gives a large ΔE (high contrast).

  2. HOW WELL does each model cope at each level of camouflage?  We bin the
     Full cells by ΔE and, in each bin, report every model's RECALL — the
     fraction of present pills it actually detects. (Camouflage only hides
     pills that are *there*, so recall on Full cells, not overall accuracy,
     is the metric that matters here.) The result is Figure 5: recall vs ΔE,
     one line per model.

How the two colours are measured
--------------------------------
The lid colour is read from the empty-box reference crop for that same
compartment (detect/reference_cells/<DAY>_<SLOT>.jpg) — i.e. what that lid
looks like with nothing under it. The pill colour is read from the filled
cell, but the cell also shows a lot of lid around the pill, so we isolate the
pill pixels with a *reference-difference mask*: exposure-match the cell to its
empty reference (cancelling the auto-exposure / white-balance drift that the
DoG baseline also fights), then keep the pixels that still differ — that blob
is the pill. The pill colour is the median CIELAB of those pixels. This is the
same "compare against the empty box" idea the DoG baseline and the
reference-conditioned CNN both rely on, reused here purely to *measure* how
hidden each pill is.

Usage
-----
    # from the repo root
    python3 -m detect.camouflage_eval \
        --images images --labels detect/labels.json --out dataset/camouflage

Outputs (under --out):
    cells.csv      one row per Full cell: ΔE00, ΔE76, pct, per-model hit/miss
    bins.json      per-ΔE-bin recall for each model (the Figure 5 data)
    figure5.png    line plot of recall vs ΔE bin, one line per model
    delta_e.json   {stem/DAY_SLOT: {"dE2000":.., "dE76":.., "pct":..}} for reuse

With --annotate DIR, also writes a per-photo 7x3 QA grid to DIR, each Full
cell showing the detected pill-mask outline and its ΔE / pixel count — the
counterpart to classify_cells.py --annotate for eyeballing colour extraction.

Only OpenCV, NumPy, scikit-image and matplotlib are required for the ΔE part;
the CNN and YOLO models additionally need onnxruntime. Any model that fails to
load (missing weights or onnxruntime) is skipped with a warning, so the DoG
baseline column always fills in.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from skimage.color import rgb2lab, deltaE_ciede2000

from . import crop_cells

# ---- pill/lid colour extraction knobs -------------------------------------
MARGIN = 0.16          # drop this border strip of each crop (walls + glare)
TEXT_L = 25            # L* below the cell median that counts as printed text
EXPOSURE_BLUR = 2.0    # px; smooth before diff to tolerate ~px misalignment
PILL_TOP_PCT = 12.0    # keep this % most-different pixels as pill candidates
PILL_FLOOR_DE = 3.0    # ΔE below this is lid noise, not pill (unless fallback)
SPECULAR_L = 96.0      # exclude near-blown highlights (glare) from the pill
MIN_PILL_PIX = 40      # below this the mask is untrustworthy -> percentile only

# ---- ΔE binning (perceptual CIEDE2000 units) ------------------------------
# 2.3 ~ a "just noticeable difference"; the first bin is effective camouflage.
BIN_EDGES = [0.0, 2.3, 5.0, 10.0, 20.0, 35.0, np.inf]
BIN_LABELS = ["0-2.3\n(camo)", "2.3-5", "5-10", "10-20", "20-35", "35+"]
# For the normalised "percent colour difference": 100 % is pinned to this ΔE.
# Default is data-driven (the dataset's own max), overridable with --de-max.
DEFAULT_DE_MAX = None


def _crop_margin(img):
    h, w = img.shape[:2]
    my, mx = int(h * MARGIN), int(w * MARGIN)
    return img[my:h - my, mx:w - mx]


def _to_lab(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return rgb2lab(rgb)  # real CIELAB: L in [0,100], a,b ~[-128,127]


def _largest_component(mask):
    """Keep only the biggest connected blob (drops scattered glare specks)."""
    n, lbl = cv2.connectedComponents(mask.astype(np.uint8))
    if n <= 1:
        return mask
    sizes = [(lbl == i).sum() for i in range(1, n)]
    keep = int(np.argmax(sizes)) + 1
    return lbl == keep


def pill_lid_colours(cell_bgr, ref_bgr):
    """Measure (lid_lab, pill_lab, pill_mask) for one Full cell.

    ``pill_mask`` is a boolean array over the MARGIN-cropped cell marking the
    pixels taken as the pill (``pill_mask.sum()`` is the pixel count). The pill
    is isolated from the surrounding lid via a reference-difference mask after
    cancelling global exposure/white-balance drift.
    """
    cell = _crop_margin(cell_bgr)
    ref = _crop_margin(ref_bgr)
    if ref.shape[:2] != cell.shape[:2]:
        ref = cv2.resize(ref, (cell.shape[1], cell.shape[0]),
                         interpolation=cv2.INTER_AREA)

    cell_lab = _to_lab(cell)
    ref_lab = _to_lab(ref)
    if EXPOSURE_BLUR > 0:  # tolerate a few px of residual misalignment
        cell_lab = cv2.GaussianBlur(cell_lab, (0, 0), EXPOSURE_BLUR)
        ref_lab = cv2.GaussianBlur(ref_lab, (0, 0), EXPOSURE_BLUR)

    # lid area = everything on the reference that isn't dark printed text
    Lref = ref_lab[:, :, 0]
    lid_area = Lref > (np.median(Lref) - TEXT_L)

    # lid colour: robust central colour of the empty lid
    lid_lab = np.median(ref_lab[lid_area], axis=0)

    # cancel global exposure / white-balance: the lid dominates the cell area,
    # so the median cell-vs-ref offset over the lid area is the drift term.
    offset = (np.median(cell_lab[lid_area], axis=0)
              - np.median(ref_lab[lid_area], axis=0))
    cell_matched = cell_lab - offset

    # per-pixel perceptual difference from the (exposure-matched) empty lid
    dE = deltaE_ciede2000(cell_matched.reshape(-1, 3),
                          ref_lab.reshape(-1, 3)).reshape(Lref.shape)
    dE[~lid_area] = 0.0
    dE[cell_matched[:, :, 0] > SPECULAR_L] = 0.0  # ignore blown-out glare

    valid = dE[lid_area]
    cut = np.percentile(valid, 100.0 - PILL_TOP_PCT) if valid.size else 0.0
    mask = dE > max(PILL_FLOOR_DE, cut)
    if mask.sum() >= MIN_PILL_PIX:
        mask = _largest_component(mask)
    else:
        # near-perfect camouflage: nothing clears the floor. Fall back to the
        # most-different pixels so we still estimate the pill's (faint) colour.
        mask = _largest_component(dE >= cut) if valid.size else mask

    if mask.sum() == 0:  # degenerate; treat as fully camouflaged
        return lid_lab, lid_lab.copy(), mask

    pill_lab = np.median(cell_matched[mask], axis=0)
    return lid_lab, pill_lab, mask


def delta_e(lab_a, lab_b):
    """(CIEDE2000, CIE76) colour difference between two Lab colours."""
    d2000 = float(deltaE_ciede2000(lab_a[None, :], lab_b[None, :])[0])
    d76 = float(np.linalg.norm(lab_a - lab_b))
    return d2000, d76


# ---------------------------------------------------------------------------
# Per-photo geometry: reuse the exact crop_cells front-end the models use, so
# ΔE is measured on the identical aligned crop each classifier sees.
# ---------------------------------------------------------------------------
def load_reference_cells(ref_dir):
    refs = {}
    for day in crop_cells.DAYS:
        for slot in crop_cells.SLOTS:
            p = ref_dir / f"{day}_{slot}.jpg"
            img = cv2.imread(str(p))
            if img is None:
                sys.exit(f"error: missing reference crop {p}")
            refs[f"{day}_{slot}"] = img
    return refs


def annotate_photo(cells, masks, de_records, stem, out_path):
    """Write a 7x3 QA grid: each Full cell with its pill-mask outline and ΔE.

    Lets you eyeball the colour extraction the same way classify_cells.py
    --annotate lets you QA the detector. Full cells get the detected pill
    contour (cyan) and "ΔE / pct" text; other cells are shown dimmed.
    """
    my_frac = mx_frac = MARGIN
    rows = []
    for slot in crop_cells.SLOTS:
        row = []
        for day in crop_cells.DAYS:
            key = f"{day}_{slot}"
            img = cells[key].copy()
            gk = f"{stem}/{key}"
            if key in masks and gk in de_records:
                h, w = img.shape[:2]
                my, mx = int(h * my_frac), int(w * mx_frac)
                full = np.zeros((h, w), np.uint8)
                full[my:h - my, mx:w - mx] = masks[key].astype(np.uint8) * 255
                cnts, _ = cv2.findContours(full, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(img, cnts, -1, (255, 255, 0), 3)
                r = de_records[gk]
                cv2.putText(img, f"dE{r['dE2000']:.1f} {r['npix']}px",
                            (10, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                            (255, 255, 0), 2)
            else:  # empty / unlabelled cell: dim it
                img = (img * 0.45).astype(np.uint8)
            cv2.rectangle(img, (2, 2), (img.shape[1] - 3, img.shape[0] - 3),
                          (90, 90, 90), 2)
            row.append(img)
        rows.append(np.hstack(row))
    grid = np.vstack(rows)
    cv2.imwrite(str(out_path), cv2.resize(grid, None, fx=0.5, fy=0.5))


def warp_photo(photo_path, templates):
    img = cv2.imread(str(photo_path))
    if img is None:
        return None
    quad, _ = crop_cells.align_quad(img, templates)
    if quad is None:
        return None
    return crop_cells.warp_grid(img, quad)


# ---------------------------------------------------------------------------
# Model predictions. Each analyze() returns {DAY_SLOT: {"pill": bool, ...}}.
# ---------------------------------------------------------------------------
def build_model_runners(models):
    """Return {name: analyze_fn} for the requested, importable models."""
    runners = {}
    if "dog" in models:
        from . import classify_cells
        runners["DoG"] = classify_cells.analyze
    if "cnn" in models:
        try:
            from . import pipeline
            pipeline._load()  # surface missing weights / onnxruntime now
            runners["Ref-CNN"] = pipeline.analyze
        except Exception as exc:  # noqa: BLE001
            print(f"warning: skipping reference-conditioned CNN — {exc}",
                  file=sys.stderr)
    if "yolo" in models:
        try:
            from .yolo import detect as yolo_detect
            yolo_detect._load_session()
            runners["YOLO"] = yolo_detect.analyze
        except Exception as exc:  # noqa: BLE001
            print(f"warning: skipping YOLO model — {exc}", file=sys.stderr)
    return runners


def bin_index(dE):
    for i in range(len(BIN_EDGES) - 1):
        if BIN_EDGES[i] <= dE < BIN_EDGES[i + 1]:
            return i
    return len(BIN_EDGES) - 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images", default="images", help="input photo directory")
    ap.add_argument("--labels", default="detect/labels.json")
    ap.add_argument("--ref-cells", default="detect/reference_cells",
                    help="empty-box reference cell crops (the lid colours)")
    ap.add_argument("--out", default="dataset/camouflage")
    ap.add_argument("--models", default="dog,cnn,yolo",
                    help="comma list from: dog, cnn, yolo")
    ap.add_argument("--de-max", type=float, default=DEFAULT_DE_MAX,
                    help="ΔE pinned to 100%% (default: dataset max)")
    ap.add_argument("--annotate", metavar="DIR",
                    help="also write per-photo QA grids (pill mask + ΔE) to DIR")
    args = ap.parse_args()

    image_dir = Path(args.images)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    labels = json.load(open(args.labels))
    full_keys = sorted(k for k, v in labels.items() if v == "pill")
    stems = sorted({k.split("/")[0] for k in full_keys})
    refs = load_reference_cells(Path(args.ref_cells))
    templates = crop_cells.build_matcher(image_dir / crop_cells.REF_IMAGE)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    runners = build_model_runners(models)
    if not runners:
        sys.exit("error: no models could be loaded")
    print(f"models: {', '.join(runners)}")

    # --- 1. ΔE per Full cell -------------------------------------------------
    de_records = {}       # "stem/KEY" -> {dE2000, dE76, npix}
    per_photo_full = {}   # stem -> [KEY, ...]
    for stem in stems:
        keys = [k.split("/")[1] for k in full_keys if k.startswith(stem + "/")]
        per_photo_full[stem] = keys
        grid = warp_photo(image_dir / f"{stem}.jpg", templates)
        if grid is None:
            print(f"  {stem}: box not found, skipped", file=sys.stderr)
            continue
        cells = {f"{d}_{s}": c for d, s, c in crop_cells.cell_crops(grid)}
        masks = {}
        for key in keys:
            lid, pill, mask = pill_lid_colours(cells[key], refs[key])
            d2000, d76 = delta_e(pill, lid)
            de_records[f"{stem}/{key}"] = {
                "dE2000": round(d2000, 3), "dE76": round(d76, 3),
                "npix": int(mask.sum())}
            masks[key] = mask
        if args.annotate:
            ann_dir = Path(args.annotate)
            ann_dir.mkdir(parents=True, exist_ok=True)
            annotate_photo(cells, masks, de_records, stem,
                           ann_dir / f"{stem}.jpg")
        print(f"  {stem}: ΔE for {len(keys)} Full cells")

    if not de_records:
        sys.exit("error: no Full cells could be measured (box alignment failed)")

    de_max = args.de_max or max(r["dE2000"] for r in de_records.values())
    for r in de_records.values():
        r["pct"] = round(100.0 * min(r["dE2000"], de_max) / de_max, 1)
    json.dump(de_records, open(out_dir / "delta_e.json", "w"), indent=2)

    # --- 2. model predictions on the same photos ----------------------------
    preds = {name: {} for name in runners}  # name -> {"stem/KEY": bool}
    for stem in stems:
        photo = image_dir / f"{stem}.jpg"
        for name, fn in runners.items():
            try:
                res = fn(photo)
            except Exception as exc:  # noqa: BLE001
                print(f"  {name} failed on {stem}: {exc}", file=sys.stderr)
                continue
            for key in per_photo_full[stem]:
                if key in res:
                    preds[name][f"{stem}/{key}"] = bool(res[key]["pill"])

    # --- 3. join, write per-cell CSV ----------------------------------------
    model_names = list(runners)
    with open(out_dir / "cells.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["photo", "cell", "dE2000", "dE76", "pct", "pill_px", "bin"]
                   + [f"hit_{m}" for m in model_names])
        for gk in sorted(de_records):
            stem, key = gk.split("/")
            r = de_records[gk]
            b = BIN_LABELS[bin_index(r["dE2000"])]
            hits = [int(preds[m].get(gk, False)) for m in model_names]
            w.writerow([stem, key, r["dE2000"], r["dE76"], r["pct"],
                        r["npix"], b] + hits)

    # --- 4. per-bin recall (the Figure 5 data) ------------------------------
    n_bins = len(BIN_LABELS)
    counts = [0] * n_bins
    hit = {m: [0] * n_bins for m in model_names}
    for gk, r in de_records.items():
        bi = bin_index(r["dE2000"])
        counts[bi] += 1
        for m in model_names:
            if preds[m].get(gk, False):
                hit[m][bi] += 1

    bins_out = {"bin_labels": BIN_LABELS, "bin_edges": [float(e) for e in BIN_EDGES],
                "n_full_cells": counts, "de_max_for_pct": round(de_max, 2),
                "recall": {}}
    print("\nPer-ΔE-bin recall on Full cells (detection rate of present pills):")
    header = f"{'ΔE bin':>14} {'nFull':>6} " + " ".join(f"{m:>8}" for m in model_names)
    print(header)
    for bi in range(n_bins):
        row = f"{BIN_LABELS[bi].splitlines()[0]:>14} {counts[bi]:>6} "
        for m in model_names:
            rec = hit[m][bi] / counts[bi] if counts[bi] else float("nan")
            bins_out["recall"].setdefault(m, []).append(
                round(rec, 4) if counts[bi] else None)
            row += f"{(f'{rec:.3f}' if counts[bi] else '  -   '):>8} "
        print(row)
    json.dump(bins_out, open(out_dir / "bins.json", "w"), indent=2)

    # overall recall per model, for reference
    print("\nOverall recall on Full cells:")
    for m in model_names:
        tot = sum(hit[m]); n = sum(counts)
        print(f"  {m:>8}: {tot}/{n} = {tot / n:.3f}")

    # --- 5. Figure 5 ---------------------------------------------------------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        print(f"\n(matplotlib unavailable, skipping figure5.png — {exc})")
        return
    x = np.arange(n_bins)
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    for m in model_names:
        ys = [hit[m][bi] / counts[bi] if counts[bi] else np.nan
              for bi in range(n_bins)]
        ax.plot(x, ys, marker="o", label=m)
    ax.set_xticks(x)
    ax.set_xticklabels(BIN_LABELS)
    ax.set_xlabel("pill-to-lid colour difference  ΔE (CIEDE2000)  —  low = camouflaged")
    ax.set_ylabel("recall on Full cells")
    ax.set_ylim(0, 1.02)
    ax.grid(True, alpha=0.3)
    ax.set_title("Effect of camouflage: recall vs pill-to-lid colour difference")
    ax.legend()
    for bi in range(n_bins):  # annotate cell counts under each bin
        ax.annotate(f"n={counts[bi]}", (x[bi], 0), textcoords="offset points",
                    xytext=(0, -30), ha="center", fontsize=8, color="gray")
    fig.tight_layout()
    fig.savefig(out_dir / "figure5.png", dpi=150)
    print(f"\nwrote {out_dir/'cells.csv'}, {out_dir/'bins.json'}, "
          f"{out_dir/'figure5.png'}")


if __name__ == "__main__":
    main()
