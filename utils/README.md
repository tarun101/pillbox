# utils/ — standalone test scripts

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
the web app first (`systemctl --user stop pillbox`) before running any of these.

## Housekeeping

- **`pi_cleanup.sh`** — removes stray dev-iteration scripts and one-off test images
  (`test*.jpg`, old `camera_*.py`, `FINAL_RasPi_Code.py`, …) left loose in the Pi home
  directory. It never touches `photos/`, `pillbox_app.py`, `~/.pillbox_pin`,
  `~/.cloudflared`, `~/.config`, `~/.ssh`, or any dotfile. Run `bash utils/pi_cleanup.sh`
  to preview and confirm, or `bash utils/pi_cleanup.sh --yes` to skip the prompt.
