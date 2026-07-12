# pillbox

Live preview and still-capture scripts for a Raspberry Pi Camera Module 3 (imx708), using `picamera2`.

## The web app

**`pillbox_app.py`** is the main thing here — a single-process web app (stdlib + PIL only,
no Flask) that owns the camera and serves on port 8000:

- **`/`** — live preview (1024x576 MJPEG) with a shutter button. Each shot captures at
  the full 4608x2592 sensor resolution; a "Capturing…" overlay covers the brief preview
  freeze while the camera switches modes (~0.5s on a Pi 5).
- **`/gallery`** — thumbnail grid of every photo taken, newest first. Per-photo download
  and delete, bulk select-and-delete, plus a download-everything-as-zip link.

Photos are stored on the Pi in `~/photos` (thumbnails in `~/photos/.thumbs`).

Storage management: the gallery shows space used by photos and free space on the SD
card, warns when the card drops below 1GB free, and captures are refused (with a
dialog) below 200MB free. Nothing is ever auto-deleted.

Run it persistently:

```
systemd-run --user --unit=camera-stream --collect python3 /home/upr/pillbox_app.py
```

## Hardware

- Camera: Raspberry Pi Camera Module 3 (imx708 sensor, 4608x2592 max / 12MP)
- Tested on both a Raspberry Pi 4 and a Raspberry Pi 5 (same SD card, moved between boards)

The two boards behave differently for **live video/MJPEG streaming** because of a hardware
difference, not a software one:

| | Pi 4 | Pi 5 |
|---|---|---|
| Hardware video encoder | Yes (`bcm2835-codec-encode`) | No — removed in the BCM2712 SoC |
| Max live-stream resolution | 1920x1920 (hardware ceiling) | Full sensor res (software-encoded, CPU-bound) |
| Measured full-res (4608x2592) fps | N/A (fails — exceeds encoder limit) | ~10.4 fps |

Still-image capture (`capture_still_fullres.py`) always gets the full 4608x2592 sensor
resolution on either board, since it doesn't go through the video encoder at all.

## Standalone scripts

Simpler single-purpose scripts that predate the web app:

- **`camera_stream.py`** — live MJPEG preview at 1024x576. Lightweight, works on any board.
  Default / recommended for just checking the camera is working.
- **`camera_stream_1080p_pi4max.py`** — live MJPEG preview at 1920x1080, the max resolution
  the Pi 4's hardware encoder supports. Also works fine on a Pi 5.
- **`camera_stream_fullres_pi5only.py`** — live MJPEG preview at the full 4608x2592 sensor
  resolution. **Pi 5 only** — fails on a Pi 4 (exceeds its hardware encoder's 1920x1920 limit).
- **`capture_still_fullres.py`** — captures a single still photo at full 4608x2592 resolution.
  Works on either board. Usage: `python3 capture_still_fullres.py [filename.jpg]`.

## Running a stream

Each streaming script serves an MJPEG feed over HTTP on port 8000:

```
python3 camera_stream.py
```

Then open `http://<pi-ip>:8000/` in a browser.

To run it persistently in the background (survives the SSH session ending):

```
systemd-run --user --unit=camera-stream --collect python3 /home/upr/camera_stream.py
```

Only one script can hold the camera at a time — stop the running one first:

```
systemctl --user stop camera-stream
```

## Notes

- MJPEG streaming and still capture can't run at the same time (both need the camera).
- Wi-Fi connectivity on these boards has been flaky in testing — if the stream seems dead,
  check the Pi is still reachable (`ping`) before assuming the script crashed.
