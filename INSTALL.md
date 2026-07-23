# Install & technical notes

On Raspberry Pi, the camera app (`pillbox_app.py`) uses `picamera2` and
Pillow, both preinstalled on Raspberry Pi OS (Bookworm and later). On
Jetson/Linux, its V4L2/UVC backend additionally uses OpenCV. The `/status`
pill-detection page needs OpenCV, NumPy, and ONNX Runtime on every platform.
Detection also reads the model and reference images from the repo (`detect/`,
`images/`), so clone the whole repo rather than copying the single file.

Raspberry Pi OS Bookworm blocks system-wide `pip` (PEP 668), so the pip
packages go in a virtualenv. Create it with `--system-site-packages` — that
keeps the apt-installed `picamera2` importable from inside the venv:

```
cd ~ && git clone https://github.com/tarun101/pillbox.git
python3 -m venv --system-site-packages ~/pillbox/venv
~/pillbox/venv/bin/pip install opencv-python-headless numpy onnxruntime
```

(The install pulls prebuilt wheels from piwheels; a few minutes on a Pi 4 is
normal.)

## Jetson Orin Nano + Wyze Cam v2

The Wyze Cam v2 (model `WYZEC2`, including UPC `859696007004`) can expose a
USB video device after installing Wyze's special webcam firmware:

<https://support.wyze.com/hc/en-us/articles/360041605111-Webcam-Firmware-Instructions>

Wyze only confirms that firmware on Windows and macOS, so verify it on the
Jetson before configuring PillWatch:

```bash
sudo apt update
sudo apt install -y v4l-utils python3-venv python3-opencv python3-pil
v4l2-ctl --list-devices
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

The camera should appear as `HD USB Camera` with one or more `/dev/video*`
nodes. Use the node that offers an MJPEG or YUYV capture format.

Create the application environment and install inference dependencies:

```bash
cd ~ && git clone https://github.com/tarun101/pillbox.git
python3 -m venv --system-site-packages ~/pillbox/venv
~/pillbox/venv/bin/pip install numpy onnxruntime
```

Configure the V4L2 backend through the environment file read by
`pillbox.service`:

```bash
mkdir -p ~/.config
cat > ~/.config/pillbox.env <<'EOF'
PILLBOX_CAMERA_BACKEND=v4l2
PILLBOX_VIDEO_DEVICE=/dev/video0
PILLBOX_USB_WIDTH=1920
PILLBOX_USB_HEIGHT=1080
PILLBOX_USB_FPS=15
PILLBOX_USB_FOURCC=MJPG
EOF
```

If `--list-formats-ext` shows only YUYV at the desired resolution, set
`PILLBOX_USB_FOURCC=YUYV`. The supported camera settings are:

| variable | default | purpose |
|---|---|---|
| `PILLBOX_CAMERA_BACKEND` | `auto` | `picamera2`, `v4l2`, `esp32`, or automatic fallback |
| `PILLBOX_VIDEO_DEVICE` | `/dev/video0` | Wyze/UVC video node |
| `PILLBOX_USB_WIDTH` | `1920` | requested capture width |
| `PILLBOX_USB_HEIGHT` | `1080` | requested capture height |
| `PILLBOX_USB_FPS` | `15` | requested frame rate |
| `PILLBOX_USB_FOURCC` | `MJPG` | requested four-character V4L2 format |

The UVC backend uses the latest full camera frame for still capture while a
background thread supplies the MJPEG preview. Raspberry Pi systems continue to
use Picamera2 and its separate full-resolution still mode.

**Detection calibration:** the current alignment coordinates, empty-box
reference image, and reference cell crops were made with the 4608×2592 Pi
Camera Module 3. Wyze images are 1920×1080 with a different field of view.
Streaming, capture, gallery, and downloads work immediately, but pill detection
must be recalibrated and its reference images regenerated from the final Wyze
mount before its results are valid.

## ESP32-CAM over Wi-Fi

For the Waveshare ESP32-S3-CAM-OV5640, this repository includes a separate,
ready-to-flash firmware project in
[`firmware/waveshare_esp32s3_ov5640`](firmware/waveshare_esp32s3_ov5640/README.md).

PillWatch supports the JPEG endpoints used by Espressif's standard
`CameraWebServer` example:

- control and still capture on `http://<camera-ip>/capture`
- multipart MJPEG on `http://<camera-ip>:81/stream`

Flash and configure the official example, reserve a stable IP address for the
camera in the router, and verify both endpoints from the PillWatch device:

```bash
curl --fail http://192.168.1.50/capture -o esp32-test.jpg
curl --fail --max-time 3 http://192.168.1.50:81/stream -o /dev/null
```

Then select the network backend:

```bash
cat > ~/.config/pillbox.env <<'EOF'
PILLBOX_CAMERA_BACKEND=esp32
PILLBOX_ESP32_BASE_URL=http://192.168.1.50
EOF
```

`PILLBOX_ESP32_BASE_URL` automatically supplies the standard `/capture` and
port-81 `/stream` URLs. Firmware with a different layout can specify both URLs
directly:

```ini
PILLBOX_CAMERA_BACKEND=esp32
PILLBOX_ESP32_CAPTURE_URL=http://esp-cam.local/jpg
PILLBOX_ESP32_STREAM_URL=http://esp-cam.local/mjpeg
```

Optional HTTP Basic Authentication credentials are
`PILLBOX_ESP32_USERNAME` and `PILLBOX_ESP32_PASSWORD`.

The backend reconnects automatically when Wi-Fi or the MJPEG response drops.
Still capture requests a fresh JPEG from the capture endpoint and falls back
to the latest stream frame if that request fails.

Use the ESP32 camera's web UI to select its highest stable JPEG resolution
before calibrating PillWatch. Common OV2640 modules top out at 1600×1200 and
the standard firmware may initially stream at a much smaller size. As with the
Wyze camera, pill detection requires a new reference photo, cell references,
and alignment calibration after the ESP32-CAM is mounted.

## Try it out

```
~/pillbox/venv/bin/python3 ~/pillbox/pillbox_app.py
```

Then open `http://<pi-ip>:8000/`.

## Install as a daemon (auto-start on boot)

```
mkdir -p ~/.config/systemd/user
cp ~/pillbox/pillbox.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pillbox
loginctl enable-linger $USER   # start at boot without a login session
```

The unit runs the app from the repo clone via the venv
(`~/pillbox/venv/bin/python3 ~/pillbox/pillbox_app.py`). To deploy a new
version later: `cd ~/pillbox && git pull && systemctl --user restart pillbox`.

The service restarts automatically on crashes (`Restart=always`).

Manage it with:

```
systemctl --user status pillbox    # check it's running
systemctl --user stop pillbox      # stop (e.g. to run a utils/ script)
systemctl --user start pillbox     # start again
```

## Continuous deployment (auto-deploy on merge)

`.github/workflows/deploy.yml` deploys `main` to the Pi automatically: on every
push to `main` it does `git reset --hard origin/main` in `~/pillbox` and restarts
the service. It runs on a **self-hosted GitHub Actions runner on the Pi**, so it
reuses the same clone and user service described above — no inbound access or
secrets needed (the repo is public, so the runner's `git fetch` needs no auth).

One-time setup on the Pi:

```
# On GitHub: repo → Settings → Actions → Runners → New self-hosted runner → Linux / ARM64.
# That page shows a download block with a token; run it on the Pi, then configure
# with the "pillbox" label the workflow targets (runs-on: [self-hosted, pillbox]):
mkdir -p ~/actions-runner && cd ~/actions-runner
# ...curl the runner tarball shown on that page, then:
./config.sh --url https://github.com/tarun101/pillbox --token <TOKEN> --labels pillbox --unattended

# Install it as a service so it survives reboots and picks up jobs on boot:
sudo ./svc.sh install $USER
sudo ./svc.sh start
```

The runner must run as the **same user** that owns `~/pillbox` and the `pillbox`
user service (the workflow calls `systemctl --user restart pillbox`, and linger is
already enabled from the daemon step above so the user manager is up at boot).

After it's registered, merging any PR into `main` redeploys the Pi within a few
seconds. You can also trigger it by hand from the repo's **Actions** tab
("Deploy to Pi" → *Run workflow*). The manual `git pull && systemctl --user
restart pillbox` still works any time as a fallback.

### Lightweight alternative: poll-based auto-deploy (no runner)

If the self-hosted runner is inconvenient (the runner tarball can be a slow
download on a Pi), `deploy/poll-deploy.sh` does the same job from cron with no
extra software — it checks `origin/main` every couple of minutes and redeploys
only when it has moved:

```
sudo loginctl enable-linger $USER   # so the service restarts without a login session
( crontab -l 2>/dev/null; \
  echo "*/2 * * * * $HOME/pillbox/deploy/poll-deploy.sh >> $HOME/deploy-pillbox.log 2>&1" ) | crontab -
```

Deploys land within ~2 minutes of a merge; each one is logged to
`~/deploy-pillbox.log`. Use either this or the GitHub Actions runner above, not
both.

## Dataset sync (photos + labels → pillbox-data repo)

Training/testing images and ground-truth labels live in a **separate
`pillbox-data` GitHub repo**, not on the Pi (the gallery can delete photos and
SD cards die) and not in this app repo (it would bloat it). Layout:

```
pillbox-data/
  raw/YYYY-MM-DD/photo_*.jpg      every capture, filed by date (source of truth)
  references/<set-id>/            empty-box reference sets, versioned
  labels/labels.json              per-cell ground truth
  splits/{train,valid,test}.txt   split BY SCENE; test frozen, never trained on
  models/<detector>/<version>/    model registry: weights + card.json
                                  (who trained it, data commit, test metrics)
  export/                         generated Train|Valid|Test / Full|Empty tree
                                  (gitignore it — regenerate, don't commit)
```

Only sources of truth are stored — cropped cells are derived data and go stale
whenever the warp calibration changes, so they are **generated on demand**:

- `detect/make_splits.py --data ~/pillbox-data` groups photos into capture
  scenes (shots seconds apart are near-duplicates) and assigns each NEW scene
  to train/valid/test, append-only — already-assigned photos, the test set
  above all, are never moved. The sync script runs this automatically.
- `detect/export_dataset.py --data ~/pillbox-data` regenerates the
  Ultralytics-classify folder tree (`Train/Full`, `Train/Empty`, `Valid/…`,
  `Test/…`, filenames `<photo stem>_<DAY>_<SLOT>.jpg`) from raw + labels +
  splits — ready for YOLO training, always in sync with label corrections.

To promote a model: evaluate candidates from `models/` against the frozen test
split, copy the winner into this repo (`detect/pill_classifier.onnx` or
`detect/yolo/best.onnx`) via a PR, and merging deploys it — the PR diff is the
audit log and rollback is `git revert`.

Labels come from the app itself: the gallery's **Analyze** modal has a
"Your labels" review grid (prefilled with the detectors' majority vote,
disagreements ringed amber) — tap cells to correct, save, and the labels land
in `~/photos/labels.json` (outside the repo clone, so deploys never wipe
them).

One-time setup on the Pi:

```
# create an empty pillbox-data repo on GitHub first, then:
git clone git@github.com:<you>/pillbox-data ~/pillbox-data
( crontab -l 2>/dev/null; \
  echo "17 * * * * $HOME/pillbox/deploy/sync-data.sh >> $HOME/sync-data.log 2>&1" ) | crontab -
```

`deploy/sync-data.sh` then files new photos under `raw/` and merges the
reviewed labels into `labels/labels.json` hourly, committing only when
something changed. Training elsewhere is just: clone both repos, generate
crops from `pillbox-data/raw/`, and point `detect/train_classifier.py
--labels` at `pillbox-data/labels/labels.json`.

## Public access via Cloudflare Tunnel

The app is reachable from anywhere at **https://pi.uprobotics.tech** through a
Cloudflare Tunnel — no port forwarding, and it works from whichever Wi-Fi network
the Pi is on (the tunnel dials out from the Pi).

How it was set up (for reference, or to redo on a fresh SD card):

```
# 1. Install cloudflared (arm64)
curl -sL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o /tmp/cloudflared.deb
sudo dpkg -i /tmp/cloudflared.deb

# 2. Authenticate (opens a browser URL — pick the uprobotics.tech zone)
cloudflared tunnel login

# 3. Create the tunnel and DNS route
cloudflared tunnel create pillbox
cloudflared tunnel route dns pillbox pi.uprobotics.tech

# 4. Config lives in /etc/cloudflared/config.yml:
#      tunnel: <tunnel-id>
#      credentials-file: /etc/cloudflared/<tunnel-id>.json
#      ingress:
#        - hostname: pi.uprobotics.tech
#          service: http://localhost:8000
#        - service: http_status:404
#    (copy the credentials .json from ~/.cloudflared/ to /etc/cloudflared/ too)

# 5. Install as a system service (auto-starts on boot)
sudo cloudflared service install
```

## Access code

The app is gated by a numeric access code. Set it on the Pi (not in the repo — this
repo is public):

```
echo 1234 > ~/.pillbox_pin    # pick your own code
chmod 600 ~/.pillbox_pin
systemctl --user restart pillbox
```

Every page and API endpoint returns the code prompt until the right code is entered;
a correct entry sets a session cookie good for 30 days (sessions also reset when the
app restarts). Wrong attempts are slowed to ~1 every 2 seconds. If `~/.pillbox_pin`
doesn't exist, the app runs open with no code — fine for a LAN-only setup, not
recommended with the public tunnel hostname.

A 4-digit code plus Cloudflare's bot challenge is reasonable protection for a hobby
camera, but it's not strong auth — use a longer code (the field takes up to 8 digits)
or a Cloudflare Access policy if you need better.

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

## utils/

Standalone test scripts (simple streams, still capture) live in `utils/` — see
[utils/README.md](utils/README.md).
