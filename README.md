# pillbox

Take photos with a Raspberry Pi camera from any browser — live preview, full-resolution
capture, and a gallery to browse and download what you've taken.

## Using it

Open **https://pi.uprobotics.tech** on your phone or computer — works from anywhere,
via a Cloudflare Tunnel. (On the home network you can also use `http://<pi-ip>:8000/`
directly.) You may see a brief Cloudflare "Just a moment…" check before the page loads.

**Camera page (`/`)**
- Live preview with a red shutter button — tap it to take a photo.
- Photos are captured at the camera's full 12MP resolution; a brief "Capturing…"
  overlay appears while the shot is taken.

**Gallery (`/gallery`)**
- Every photo you've taken, newest first.
- Download photos one at a time, or everything as a zip.
- Delete one photo, or use **Select** to delete many at once.
- Shows how much space photos use and how much is left on the SD card. If the card
  runs low you'll be warned; when critically full, capture is blocked until you
  delete or download some photos. Nothing is ever deleted automatically.

Photos live on the Pi in `~/photos`, named by date and time
(e.g. `photo_20260712_123030.jpg`).

## Setup

See [INSTALL.md](INSTALL.md) for installation, running it as an auto-starting daemon,
hardware notes (Pi 4 vs Pi 5), and the standalone test scripts in `utils/`.
