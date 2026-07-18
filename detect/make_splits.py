#!/usr/bin/env python3
"""Assign photos to Train/Valid/Test splits, by capture scene, append-only.

Reads every photo under <data>/raw/, groups shots taken within --gap seconds
into one *scene* (a burst of near-duplicate frames must never straddle a
split, or accuracy numbers get quietly inflated), and files each NEW scene
into splits/train.txt, valid.txt or test.txt so the requested proportions are
approached over time. Photos already listed in a split file are NEVER moved —
in particular the test split stays frozen, so model comparisons remain honest
across months. A "<name> copy" file shares its original's timestamp and so
lands in the same scene automatically.

Run it after new captures land (deploy/sync-data.sh does this on the Pi), or
by hand:

    python3 detect/make_splits.py --data ~/pillbox-data
        [--gap 3] [--frac 0.7 0.15 0.15] [--seed 0]
"""
import argparse
import random
import re
from datetime import datetime
from pathlib import Path

SPLITS = ["train", "valid", "test"]


def stem_time(stem):
    m = re.match(r"photo_(\d{8}_\d{6})", stem.replace(" copy", ""))
    return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S") if m else None


def scenes_of(stems, gap_s):
    """Group stems into scenes: chronological runs with gaps <= gap_s."""
    timed = sorted((stem_time(s), s) for s in stems if stem_time(s))
    scenes, last_t = [], None
    for t, s in timed:
        if last_t is None or (t - last_t).total_seconds() > gap_s:
            scenes.append([])
        scenes[-1].append(s)
        last_t = t
    return scenes


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="pillbox-data checkout")
    ap.add_argument("--gap", type=float, default=3,
                    help="seconds between shots that separates scenes")
    ap.add_argument("--frac", type=float, nargs=3, default=[0.7, 0.15, 0.15],
                    metavar=("TRAIN", "VALID", "TEST"))
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    data = Path(args.data).expanduser()
    stems = sorted({p.stem for p in (data / "raw").rglob("photo_*.jpg")})
    if not stems:
        raise SystemExit(f"no photos under {data / 'raw'}")

    splits_dir = data / "splits"
    splits_dir.mkdir(exist_ok=True)
    final = {}  # stem -> split name; existing assignments are immutable
    for name in SPLITS:
        f = splits_dir / f"{name}.txt"
        if f.is_file():
            for s in f.read_text().splitlines():  # stems may contain spaces
                if s.strip():
                    final[s.strip()] = name

    counts = {n: 0 for n in SPLITS}
    for name in final.values():
        counts[name] += 1

    new_scenes = []
    for scene in scenes_of(stems, args.gap):
        pre = {final[s] for s in scene if s in final}
        if len(pre) > 1:  # damage from before this tool existed; don't touch
            print(f"warning: scene starting {scene[0]} already straddles "
                  f"{sorted(pre)}; leaving its photos where they are")
            continue
        if pre:  # scene already has a home -> new members join it
            name = pre.pop()
            for s in scene:
                if s not in final:
                    final[s] = name
                    counts[name] += 1
        else:
            new_scenes.append(scene)

    fr = dict(zip(SPLITS, args.frac))
    rng = random.Random(args.seed)
    rng.shuffle(new_scenes)
    n_new = sum(len(sc) for sc in new_scenes)
    for scene in new_scenes:
        total = sum(counts.values()) + len(scene)
        # the split currently furthest below its target share gets the scene
        name = max(SPLITS, key=lambda n: fr[n] - counts[n] / total)
        for s in scene:
            final[s] = name
            counts[name] += 1

    for name in SPLITS:
        members = sorted(s for s, n in final.items() if n == name)
        (splits_dir / f"{name}.txt").write_text("\n".join(members) + "\n")

    total = sum(counts.values())
    shares = "  ".join(f"{n} {counts[n]} ({counts[n]/total:.0%})" for n in SPLITS)
    print(f"{total} photos in splits ({n_new} newly assigned): {shares}")


if __name__ == "__main__":
    main()
