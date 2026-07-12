# Install & technical notes

The app is a single file (`pillbox_app.py`) using only `picamera2` and Pillow — both
preinstalled on Raspberry Pi OS (Bookworm and later). No pip installs needed.

## Try it out

Copy `pillbox_app.py` to the Pi home directory and run:

```
python3 pillbox_app.py
```

Then open `http://<pi-ip>:8000/`.

## Install as a daemon (auto-start on boot)

Copy `pillbox.service` to the Pi and run:

```
mkdir -p ~/.config/systemd/user
cp pillbox.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pillbox
loginctl enable-linger $USER   # start at boot without a login session
```

The service restarts automatically on crashes (`Restart=always`).

Manage it with:

```
systemctl --user status pillbox    # check it's running
systemctl --user stop pillbox      # stop (e.g. to run a utils/ script)
systemctl --user start pillbox     # start again
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
the web app first (`systemctl --user stop pillbox`) before running any of these.
