# pillbox

A camera web app for the Raspberry Pi Camera Module 3 (imx708), using `picamera2`.
One process (`pillbox_app.py`) owns the camera and serves everything on port 8000.

## Usage

Open `http://<pi-ip>:8000/` in any browser (desktop or phone):

- **`/`** — live preview (1024x576 MJPEG) with a shutter button. Each shot captures at
  the full 4608x2592 (12MP) sensor resolution; a "Capturing…" overlay covers the brief
  preview freeze while the camera switches modes (~0.5s on a Pi 5).
- **`/gallery`** — thumbnail grid of every photo taken, newest first. Per-photo download
  and delete, bulk select-and-delete, plus a download-everything-as-zip link.

Photos are stored on the Pi in `~/photos` (thumbnails in `~/photos/.thumbs`).

Storage management: the gallery shows space used by photos and free space on the SD
card, warns when the card drops below 1GB free, and captures are refused (with a
dialog) below 200MB free. Nothing is ever auto-deleted.

## Install & run

Dependencies are just `picamera2` and Pillow, both preinstalled on Raspberry Pi OS
(Bookworm and later) — no pip installs needed.

Copy `pillbox_app.py` to the Pi and run it:

```
python3 pillbox_app.py
```

Or run it persistently in the background (survives the SSH session ending):

```
systemd-run --user --unit=camera-stream --collect python3 /home/upr/pillbox_app.py
```

Stop it with:

```
systemctl --user stop camera-stream
```

## Hardware notes

- Camera: Raspberry Pi Camera Module 3 (imx708 sensor, 4608x2592 max / 12MP)
- Tested on both a Raspberry Pi 4 and a Raspberry Pi 5 (same SD card, moved between boards)

The two boards behave differently for **live video/MJPEG streaming** because of a hardware
difference, not a software one:

| | Pi 4 | Pi 5 |
|---|---|---|
| Hardware video encoder | Yes (`bcm2835-codec-encode`) | No — removed in the BCM2712 SoC |
| Max live-stream resolution | 1920x1920 (hardware ceiling) | Full sensor res (software-encoded, CPU-bound) |
| Measured full-res (4608x2592) fps | N/A (fails — exceeds encoder limit) | ~10.4 fps |

Still-image capture always gets the full 4608x2592 sensor resolution on either board,
since it doesn't go through the video encoder at all.

- MJPEG streaming and still capture can't run at the same time (both need the camera);
  the app serializes them internally.
- Wi-Fi connectivity on these boards has been flaky in testing — if the stream seems dead,
  check the Pi is still reachable (`ping`) before assuming the app crashed.

## utils/ — standalone test scripts

Simpler single-purpose scripts that predate the web app, kept for testing:

- **`camera_stream.py`** — live MJPEG preview at 1024x576. Lightweight, works on any board.
  Default / recommended for just checking the camera is working.
- **`camera_stream_1080p_pi4max.py`** — live MJPEG preview at 1920x1080, the max resolution
  the Pi 4's hardware encoder supports. Also works fine on a Pi 5.
- **`camera_stream_fullres_pi5only.py`** — live MJPEG preview at the full 4608x2592 sensor
  resolution. **Pi 5 only** — fails on a Pi 4 (exceeds its hardware encoder's 1920x1920 limit).
- **`capture_still_fullres.py`** — captures a single still photo at full 4608x2592 resolution.
  Works on either board. Usage: `python3 capture_still_fullres.py [filename.jpg]`.

Each streaming script serves its MJPEG feed on port 8000 (`python3 utils/camera_stream.py`,
then open `http://<pi-ip>:8000/`). Only one process can hold the camera at a time — stop
the web app first (`systemctl --user stop camera-stream`) before running any of these.
