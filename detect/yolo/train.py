#!/usr/bin/env python3
"""Train a YOLO pill detector on the pseudo-labelled grid dataset.

Run detect/yolo/make_dataset.py first. This is a thin Ultralytics wrapper:
it fine-tunes yolov8n (nano — smallest, CPU-friendly) on the one "pill" class
and exports the best weights to ONNX next to the .pt.

By default this trains yolov8n from scratch (`yolov8n.yaml`) rather than
fine-tuning COCO weights — the pretrained `.pt` is fetched from GitHub, which
some sandboxes block. Pass `--weights yolov8n.pt` if you have the file and
want a warm start (better with this little data).

Usage:
    python3 detect/yolo/train.py [--epochs 200] [--imgsz 768] [--data ...]

Needs `pip install ultralytics` (heavy; not required on the Pi — this is the
"for fun" detector, separate from the shipped classifier).
"""
import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default="dataset/yolo/pillbox.yaml")
    ap.add_argument("--weights", default="yolov8n.yaml",
                    help="yolov8n.yaml (from scratch) or yolov8n.pt (warm start)")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--imgsz", type=int, default=768)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--project", default="dataset/yolo/runs")
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    model.train(
        data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
        project=args.project, name="pill", exist_ok=True, seed=0, device="cpu",
        # small dataset -> lean on augmentation, disable mosaic near the end
        mosaic=1.0, close_mosaic=15, hsv_h=0.02, hsv_s=0.5, hsv_v=0.4,
        degrees=5, translate=0.08, scale=0.3, fliplr=0.5, patience=40,
        plots=True, verbose=True,
    )
    best = Path(args.project) / "pill" / "weights" / "best.pt"
    YOLO(str(best)).export(format="onnx", imgsz=args.imgsz, opset=12)
    print(f"done: {best} (+ .onnx)")


if __name__ == "__main__":
    main()
