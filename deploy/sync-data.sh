#!/usr/bin/env bash
# Sync captured photos + reviewed labels from the Pi into the pillbox-data
# repo, so the training/testing dataset survives gallery deletions, SD-card
# failures and app deploys. Run from cron (see INSTALL.md).
#
# Expects a clone of the data repo at $DATA_REPO (default ~/pillbox-data) with
# push access. Layout it maintains:
#   raw/YYYY-MM-DD/photo_*.jpg   every capture, filed by date
#   labels/labels.json           per-cell ground truth (merged, app wins)
# References and train/val/test splits are curated by hand in the same repo.
set -euo pipefail

DATA_REPO="${DATA_REPO:-$HOME/pillbox-data}"
PHOTOS="${PHOTOS:-$HOME/photos}"

if [ ! -d "$DATA_REPO/.git" ]; then
    echo "error: no data repo at $DATA_REPO — create an empty GitHub repo and:" >&2
    echo "  git clone git@github.com:<you>/pillbox-data \"$DATA_REPO\"" >&2
    exit 1
fi

cd "$DATA_REPO"
git pull -q --rebase

# File every photo under raw/YYYY-MM-DD/ (from the photo_YYYYMMDD_* filename);
# copy only what's new so reruns are cheap.
shopt -s nullglob
for p in "$PHOTOS"/photo_*.jpg; do
    f=$(basename "$p")
    d="${f:6:4}-${f:10:2}-${f:12:2}"
    mkdir -p "raw/$d"
    [ -f "raw/$d/$f" ] || cp "$p" "raw/$d/$f"
done

# Merge the labels the app's Analyze review UI wrote (newer review wins).
python3 - "$PHOTOS/labels.json" labels/labels.json <<'PY'
import json, os, sys
src, dst = sys.argv[1], sys.argv[2]
if os.path.exists(src):
    merged = json.load(open(dst)) if os.path.exists(dst) else {}
    merged.update(json.load(open(src)))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(merged, open(dst, "w"), indent=1, sort_keys=True)
PY

git add -A
if ! git diff --cached --quiet; then
    git commit -qm "sync from pi $(date '+%Y-%m-%d %H:%M')"
    git push -q
    echo "$(date '+%Y-%m-%d %H:%M:%S') pushed $(git rev-parse --short HEAD)"
fi
