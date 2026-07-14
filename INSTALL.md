# Install & technical notes

The camera app (`pillbox_app.py`) uses only `picamera2` and Pillow — both
preinstalled on Raspberry Pi OS (Bookworm and later). The `/status` pill-detection
page additionally needs `opencv-python-headless`, `numpy` and `onnxruntime`.
Without those the camera and gallery still work; `/status` shows what to install.
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
