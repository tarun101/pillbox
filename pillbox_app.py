#!/usr/bin/env python3
"""Pillbox camera web app.

One process owns the camera and serves everything on port 8000:

  /            live preview + a capture button (full-res stills)
  /gallery     thumbnails of every photo taken; download one, all as zip, delete,
               or run all three detectors on a single photo (the Analyze button)
  /status      which of the 21 pillbox cells contain a pill (latest photo),
               compared across the DoG, CNN and YOLO detectors
  /stream.mjpg the MJPEG feed used by the preview page

Captures are full sensor resolution (4608x2592 on the imx708). The camera has
to switch out of video mode for each still, so the preview freezes for a
moment per shot — the page shows a "Capturing…" overlay while that happens.

Photos land in ~/photos with thumbnails in ~/photos/.thumbs.
"""
import io
import json
import os
import secrets
import shutil
import socketserver
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from http import server
from pathlib import Path
from threading import Condition, Lock
from urllib.parse import parse_qs, unquote

from PIL import Image
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

PHOTO_DIR = Path.home() / "photos"
THUMB_DIR = PHOTO_DIR / ".thumbs"
STREAM_SIZE = (1024, 576)
STILL_SIZE = (4608, 2592)
THUMB_MAX = (400, 400)
LOW_SPACE_WARN = 1024 * 1024 * 1024  # gallery shows a warning below 1GB free
CAPTURE_MIN_FREE = 200 * 1024 * 1024  # refuse captures below 200MB free

# Access code lives outside the repo (this file is public on GitHub).
# Put the code in ~/.pillbox_pin on the Pi, e.g.:  echo 1234 > ~/.pillbox_pin
PIN_FILE = Path.home() / ".pillbox_pin"
PIN = PIN_FILE.read_text().strip() if PIN_FILE.is_file() else None
SESSION_TOKEN = secrets.token_hex(16)  # new token per app start
SESSION_COOKIE = "pillbox_session"

LOGIN_PAGE = """\
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pillbox — enter code</title>
<style>
  body { margin:0; background:#111; color:#eee; font-family:-apple-system,sans-serif;
         display:flex; align-items:center; justify-content:center; height:100vh; }
  form { text-align:center; }
  h1 { font-size:20px; margin-bottom:18px; }
  input { font-size:32px; letter-spacing:12px; text-align:center; width:200px;
          padding:10px; background:#1c1c1c; color:#eee; border:1px solid #444;
          border-radius:8px; }
  button { display:block; margin:18px auto 0; font-size:17px; padding:10px 34px;
           background:#c33; color:#fff; border:none; border-radius:8px; cursor:pointer; }
  .err { color:#e66; margin-top:12px; min-height:1.2em; }
</style>
</head>
<body>
<form method="POST" action="/login">
  <h1>Enter access code</h1>
  <input name="code" type="password" inputmode="numeric" autocomplete="one-time-code"
         maxlength="8" autofocus>
  <button type="submit">Unlock</button>
  <div class="err">{error}</div>
</form>
</body>
</html>
"""

CAPTURE_PAGE = """\
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pillbox — camera</title>
<style>
  body { margin:0; background:#111; color:#eee; font-family:-apple-system,sans-serif; }
  header { display:flex; justify-content:space-between; align-items:center; padding:10px 16px; }
  header a { color:#8bf; text-decoration:none; font-size:16px; }
  /* Cap the stage so preview + shutter fit in the viewport on desktop;
     phones stay edge-to-edge. 190px ~= header + controls height. */
  .stage { position:relative; margin:0 auto; width:100%;
           max-width:min(1100px, calc((100vh - 190px) * 16 / 9)); }
  .stage img { width:100%; display:block; }
  #overlay { position:absolute; inset:0; background:rgba(0,0,0,.65); display:none;
             align-items:center; justify-content:center; font-size:22px; }
  #overlay.show { display:flex; }
  .controls { display:flex; justify-content:center; padding:18px; }
  #shutter { width:76px; height:76px; border-radius:50%; border:5px solid #eee;
             background:#c33; cursor:pointer; }
  #shutter:active { background:#a11; }
  #shutter:disabled { background:#555; }
  #toast { position:fixed; bottom:110px; left:50%; transform:translateX(-50%);
           background:#2a2; color:#fff; padding:8px 18px; border-radius:20px;
           opacity:0; transition:opacity .3s; font-size:15px; }
  #toast.show { opacity:1; }
</style>
</head>
<body>
<header><b>pillbox camera</b><span><a href="/status" style="margin-right:14px">Status</a><a href="/gallery">Gallery &rarr;</a></span></header>
<div class="stage">
  <img src="/stream.mjpg" alt="live preview">
  <div id="overlay">Capturing&hellip;</div>
</div>
<div class="controls"><button id="shutter" title="Take photo"></button></div>
<div id="toast"></div>
<script>
const shutter = document.getElementById('shutter');
const overlay = document.getElementById('overlay');
const toast = document.getElementById('toast');
shutter.onclick = async () => {
  shutter.disabled = true;
  overlay.classList.add('show');
  try {
    const r = await fetch('/capture', {method: 'POST'});
    const data = await r.json();
    if (r.status === 507) {
      overlay.classList.remove('show');
      shutter.disabled = false;
      alert(data.error);
      return;
    }
    toast.textContent = r.ok ? 'Saved ' + data.file : 'Error: ' + data.error;
    toast.style.background = r.ok ? '#2a2' : '#c33';
  } catch (e) {
    toast.textContent = 'Error: ' + e;
    toast.style.background = '#c33';
  }
  overlay.classList.remove('show');
  shutter.disabled = false;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2500);
};
</script>
</body>
</html>
"""

GALLERY_PAGE_TOP = """\
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pillbox — gallery</title>
<style>
  body { margin:0; background:#111; color:#eee; font-family:-apple-system,sans-serif; }
  header { display:flex; justify-content:space-between; align-items:center;
           padding:10px 16px; flex-wrap:wrap; gap:8px; }
  header a { color:#8bf; text-decoration:none; font-size:15px; margin-left:14px; }
  .stats { padding:0 16px 8px; color:#999; font-size:13px; }
  .banner { background:#653; color:#fda; padding:10px 16px; font-size:14px; }
  .toolbar { display:flex; gap:14px; padding:4px 16px 8px; align-items:center; }
  .toolbar button { background:#333; color:#eee; border:1px solid #555;
                    border-radius:6px; padding:8px 14px; font-size:14px; cursor:pointer; }
  .toolbar button.danger { color:#e66; border-color:#844; }
  .toolbar button:disabled { opacity:.4; }
  .card input[type=checkbox] { position:absolute; top:8px; left:8px; z-index:2;
                               width:26px; height:26px; accent-color:#c33; }
  .card { position:relative; }
  /* In select mode the whole thumbnail toggles selection (see imgClick). */
  .card.selecting a > img { cursor:pointer; }
  .card.selected { outline:3px solid #c33; outline-offset:-3px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));
          gap:12px; padding:12px; }
  .card { background:#1c1c1c; border-radius:8px; overflow:hidden; }
  .card img { width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }
  .meta { padding:8px 10px; font-size:13px; }
  .meta .row { display:flex; justify-content:space-between; margin-top:6px; }
  .meta a { color:#8bf; text-decoration:none; }
  .meta button { background:none; border:none; color:#e66; cursor:pointer;
                 font-size:13px; padding:0; }
  .analyze-btn { display:block; width:100%; margin-top:8px; background:#243; color:#8e8;
                 border:1px solid #2a7a2a; border-radius:6px; padding:7px 0;
                 font-size:13px; cursor:pointer; }
  .analyze-btn:disabled { opacity:.5; cursor:default; }
  .empty { padding:40px; text-align:center; color:#888; }
  /* Analyze modal: shows the DoG/CNN/YOLO grids for one photo. */
  #amodal { position:fixed; inset:0; background:rgba(0,0,0,.7); display:none;
            align-items:flex-start; justify-content:center; overflow-y:auto;
            padding:24px 8px; z-index:10; }
  #amodal.show { display:flex; }
  .abox { background:#161616; border:1px solid #333; border-radius:12px;
          max-width:760px; width:100%; }
  .ahead { display:flex; justify-content:space-between; align-items:center;
           padding:14px 18px; border-bottom:1px solid #2a2a2a; }
  .ahead b { font-size:15px; word-break:break-all; }
  .ahead button { background:none; border:none; color:#aaa; font-size:22px;
                  cursor:pointer; line-height:1; padding:0 4px; }
  #abody { padding:6px 6px 18px; }
  #abody .method { margin:0 0 6px; }
  #abody .method h3 { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;
                      margin:16px 14px 2px; font-size:15px; font-weight:700; }
  #abody .method h3 .count { color:#999; font-size:12px; font-weight:600; }
  #abody .method h3 .desc { color:#777; font-size:11px; font-weight:400; }
  #abody .wrap { overflow-x:auto; padding:0 10px 6px; }
  #abody table { border-collapse:separate; border-spacing:5px; margin:0 auto; }
  #abody th { color:#999; font-size:11px; font-weight:600; padding:2px 4px; }
  #abody td { width:52px; height:44px; border-radius:7px; text-align:center;
              font-size:11px; font-weight:600; }
  #abody td.pill { background:#1d4d1d; color:#8e8; border:1px solid #2a7a2a; }
  #abody td.empty { background:#222; color:#777; border:1px solid #333; }
  .aloading { padding:34px; text-align:center; color:#aaa; font-size:14px; }
  .aerr { margin:12px 14px; background:#432; color:#fda; padding:12px 16px;
          border-radius:8px; font-size:13px; line-height:1.5; }
</style>
</head>
<body>
"""


STATUS_PAGE_TOP = """\
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pillbox — status</title>
<style>
  body { margin:0; background:#111; color:#eee; font-family:-apple-system,sans-serif; }
  header { display:flex; justify-content:space-between; align-items:center;
           padding:10px 16px; flex-wrap:wrap; gap:8px; }
  header a { color:#8bf; text-decoration:none; font-size:15px; margin-left:14px; }
  .sub { padding:0 16px 10px; color:#999; font-size:13px; }
  .sub a { color:#8bf; text-decoration:none; }
  .method { margin:0 0 8px; }
  .method h2 { display:flex; align-items:baseline; gap:10px; margin:18px 16px 2px;
               font-size:16px; font-weight:700; }
  .method h2 .count { color:#999; font-size:13px; font-weight:600; }
  .method h2 .desc { color:#777; font-size:12px; font-weight:400; }
  .wrap { overflow-x:auto; padding:0 12px 20px; }
  table { border-collapse:separate; border-spacing:6px; margin:0 auto; }
  th { color:#999; font-size:12px; font-weight:600; padding:2px 4px; }
  td { width:64px; height:52px; border-radius:8px; text-align:center;
       font-size:12px; font-weight:600; }
  td.pill { background:#1d4d1d; color:#8e8; border:1px solid #2a7a2a; }
  td.empty { background:#222; color:#777; border:1px solid #333; }
  .err { margin:30px auto; max-width:560px; background:#432; color:#fda;
         padding:16px 20px; border-radius:10px; font-size:15px; line-height:1.5; }
  .empty-msg { padding:40px; text-align:center; color:#888; }
</style>
</head>
<body>
"""

STATUS_DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
STATUS_SLOTS = ["MORN", "NOON", "NIGHT"]


def _cell_title(r, metric):
    """Tooltip text for one cell, based on the detector's score field."""
    val = r.get(metric)
    if not isinstance(val, (int, float)):
        return ""
    if metric in ("prob", "conf"):  # 0..1 probabilities read best as percent
        return f"{val:.0%}"
    return f"{val:.3f}"  # DoG blob-area score


def _render_grid(results, metric):
    """Render the 7x3 day/slot table for one detector's results."""
    parts = ['<div class="wrap"><table><tr><th></th>']
    parts.extend(f"<th>{d}</th>" for d in STATUS_DAYS)
    parts.append("</tr>")
    for slot in STATUS_SLOTS:
        parts.append(f"<tr><th>{slot}</th>")
        for day in STATUS_DAYS:
            r = results[f"{day}_{slot}"]
            cls = "pill" if r["pill"] else "empty"
            label = "pill" if r["pill"] else "&mdash;"
            parts.append(f'<td class="{cls}" title="{_cell_title(r, metric)}">{label}</td>')
        parts.append("</tr>")
    parts.append("</table></div>")
    return "".join(parts)


def render_status_page(photo, analysis, error=None):
    """Render /status HTML comparing every detector's per-cell results.

    `analysis` is the mapping returned by analyze_photo(): method key ->
    {"label", "desc", "metric", "results", "error"}.
    """
    parts = [STATUS_PAGE_TOP]
    parts.append(
        '<header><b>pillbox status</b><span>'
        '<a href="/gallery">Gallery</a><a href="/">&larr; Camera</a></span></header>'
    )
    if error is not None:
        parts.append(f'<div class="err">&#9888; {error}</div></body></html>')
        return "".join(parts)
    if photo is None:
        parts.append('<div class="empty-msg">No photos yet — go take some.'
                     '</div></body></html>')
        return "".join(parts)
    parts.append(
        f'<div class="sub">Detector comparison &middot; from '
        f'<a href="/photos/{photo}" target="_blank">{photo}</a></div>'
    )
    for m in analysis.values():
        if m["error"] is not None:
            parts.append(
                f'<div class="method"><h2>{m["label"]} '
                f'<span class="desc">{m["desc"]}</span></h2>'
                f'<div class="err">&#9888; {m["error"]}</div></div>'
            )
            continue
        results = m["results"]
        n = sum(1 for r in results.values() if r["pill"])
        parts.append(
            f'<div class="method"><h2>{m["label"]} '
            f'<span class="count">{n}/21 cells</span>'
            f'<span class="desc">{m["desc"]}</span></h2>'
        )
        parts.append(_render_grid(results, m["metric"]))
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class Camera:
    """Owns the Picamera2 instance; serializes captures against streaming."""

    def __init__(self):
        self.picam2 = Picamera2()
        self.video_config = self.picam2.create_video_configuration(main={"size": STREAM_SIZE})
        self.still_config = self.picam2.create_still_configuration(main={"size": STILL_SIZE})
        self.output = StreamingOutput()
        self.lock = Lock()
        self.picam2.configure(self.video_config)
        self.picam2.start_recording(MJPEGEncoder(), FileOutput(self.output))

    def capture_still(self):
        name = datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")
        path = PHOTO_DIR / name
        with self.lock:
            self.picam2.stop_recording()
            try:
                self.picam2.configure(self.still_config)
                self.picam2.start()
                self.picam2.capture_file(str(path))
                self.picam2.stop()
            finally:
                self.picam2.configure(self.video_config)
                self.picam2.start_recording(MJPEGEncoder(), FileOutput(self.output))
        with Image.open(path) as im:
            im.thumbnail(THUMB_MAX)
            im.save(THUMB_DIR / name, quality=70)
        return name


def list_photos():
    return sorted((p.name for p in PHOTO_DIR.glob("photo_*.jpg")), reverse=True)


STATUS_CACHE = {}  # photo name -> {method: {...}} combined detector results
STATUS_LOCK = Lock()  # one analysis at a time; they're CPU-heavy on the Pi

# Detectors compared on /status. Each module exposes analyze(photo_path) ->
# {DAY_SLOT: {"pill": bool, <metric>: float}} and raises AnalysisError.
#   (key, label, one-line description, module, per-cell score field)
DETECTORS = [
    ("dog", "DoG",
     "classical difference-of-Gaussians blob energy vs. the empty box",
     "detect.classify_cells", "score"),
    ("cnn", "CNN",
     "trained reference-differencing classifier (pill_classifier.onnx)",
     "detect.pipeline", "prob"),
    ("yolo", "YOLO",
     "trained YOLO object detector on the warped grid",
     "detect.yolo.detect", "conf"),
]


def _run_detector(module_name, photo):
    """Run one detector on a photo. Returns (results, error_message)."""
    import importlib
    try:
        mod = importlib.import_module(module_name)
    except ImportError as e:
        return None, (f"detection needs extra packages ({e}) — run: "
                      "pip install opencv-python-headless numpy onnxruntime")
    try:
        return mod.analyze(PHOTO_DIR / photo), None
    except Exception as e:  # each detector's AnalysisError is human-readable
        return None, str(e)


def analyze_photo(photo):
    """Run every detector on one photo (cached).

    Returns {method_key: {"label", "desc", "metric", "results", "error"}}.
    """
    if photo in STATUS_CACHE:
        return STATUS_CACHE[photo]
    repo = str(Path(__file__).resolve().parent)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    with STATUS_LOCK:
        if photo not in STATUS_CACHE:  # may have been computed while waiting
            combined = {}
            for key, label, desc, module_name, metric in DETECTORS:
                results, error = _run_detector(module_name, photo)
                combined[key] = {"label": label, "desc": desc, "metric": metric,
                                 "results": results, "error": error}
            STATUS_CACHE[photo] = combined
    return STATUS_CACHE[photo]


def analyze_photo_json(photo):
    """Shape analyze_photo() output for the gallery's Analyze button.

    Returns {"photo", "methods": {key: {"label", "desc", "metric",
    "error", "results"?, "count"?}}} — ready to JSON-encode. `count` is the
    number of cells called "pill"; it and `results` are omitted when the
    detector errored.
    """
    methods = {}
    for key, m in analyze_photo(photo).items():
        entry = {"label": m["label"], "desc": m["desc"],
                 "metric": m["metric"], "error": m["error"]}
        if m["results"] is not None:
            entry["results"] = m["results"]
            entry["count"] = sum(1 for r in m["results"].values() if r["pill"])
        methods[key] = entry
    return {"photo": photo, "methods": methods}


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{n:.0f} {unit}"
        n /= 1024


def storage_stats():
    photos = list_photos()
    used = sum((PHOTO_DIR / name).stat().st_size for name in photos)
    free = shutil.disk_usage(PHOTO_DIR).free
    return {"count": len(photos), "photos_bytes": used, "free_bytes": free}


def safe_photo_path(base_dir, name):
    """Resolve name inside base_dir, rejecting traversal attempts."""
    if name != os.path.basename(name):
        return None
    path = base_dir / name
    return path if path.is_file() else None


class Handler(server.BaseHTTPRequestHandler):
    def is_authed(self):
        if PIN is None:  # no pin file -> no gate (e.g. fresh install)
            return True
        cookies = self.headers.get("Cookie", "")
        return f"{SESSION_COOKIE}={SESSION_TOKEN}" in cookies

    def require_auth(self):
        """Returns True if the request may proceed; otherwise serves the login page."""
        if self.is_authed():
            return True
        self.send_html(LOGIN_PAGE.replace("{error}", ""), status=401)
        return False

    def handle_login(self):
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        code = (form.get("code") or [""])[0].strip()
        if PIN is not None and secrets.compare_digest(code, PIN):
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={SESSION_TOKEN}; Max-Age=2592000; Path=/; HttpOnly; SameSite=Lax",
            )
            self.send_header("Content-Length", 0)
            self.end_headers()
        else:
            time.sleep(2)  # slow down guessing
            self.send_html(LOGIN_PAGE.replace("{error}", "Wrong code, try again."), status=401)

    def send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type, download_name=None):
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", size)
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with open(path, "rb") as f:
            while chunk := f.read(64 * 1024):
                self.wfile.write(chunk)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if not self.require_auth():
            return
        if path == "/":
            self.send_html(CAPTURE_PAGE)
        elif path == "/gallery":
            self.send_html(self.render_gallery())
        elif path == "/status":
            q = parse_qs(query)
            photos = list_photos()
            name = (q.get("photo") or [None])[0]
            if name is not None and safe_photo_path(PHOTO_DIR, name) is None:
                self.send_error(404)
                return
            if name is None:
                name = photos[0] if photos else None
            if name is None:
                self.send_html(render_status_page(None, None))
                return
            analysis = analyze_photo(name)
            self.send_html(render_status_page(name, analysis))
        elif path == "/analyze":
            q = parse_qs(query)
            name = (q.get("photo") or [None])[0]
            if name is None or safe_photo_path(PHOTO_DIR, name) is None:
                self.send_json({"error": "Unknown photo."}, status=404)
                return
            self.send_json(analyze_photo_json(name))
        elif path == "/stream.mjpg":
            self.stream_mjpeg()
        elif path == "/all.zip":
            self.send_zip()
        elif path.startswith("/photos/"):
            name = unquote(path[len("/photos/"):])
            p = safe_photo_path(PHOTO_DIR, name)
            if p:
                dl = name if query == "download" else None
                self.send_file(p, "image/jpeg", download_name=dl)
            else:
                self.send_error(404)
        elif path.startswith("/thumbs/"):
            name = unquote(path[len("/thumbs/"):])
            p = safe_photo_path(THUMB_DIR, name)
            if p:
                self.send_file(p, "image/jpeg")
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/login":
            self.handle_login()
            return
        if not self.require_auth():
            return
        if self.path == "/capture":
            free = shutil.disk_usage(PHOTO_DIR).free
            if free < CAPTURE_MIN_FREE:
                self.send_json(
                    {"error": f"Storage almost full ({fmt_bytes(free)} free). "
                              "Delete some photos from the gallery first."},
                    status=507,
                )
                return
            try:
                name = camera.capture_still()
                self.send_json({"file": name})
            except Exception as e:  # report capture failures to the page
                self.send_json({"error": str(e)}, status=500)
        elif self.path == "/delete-many":
            length = int(self.headers.get("Content-Length", 0))
            try:
                names = json.loads(self.rfile.read(length)).get("names", [])
            except (json.JSONDecodeError, AttributeError):
                self.send_error(400)
                return
            deleted, failed = [], []
            for name in names:
                p = safe_photo_path(PHOTO_DIR, name)
                if not p:
                    failed.append(name)
                    continue
                try:  # don't let one bad file abort the whole batch
                    p.unlink(missing_ok=True)
                    (THUMB_DIR / name).unlink(missing_ok=True)
                    deleted.append(name)
                except OSError:
                    failed.append(name)
            self.send_json({"deleted": deleted, "failed": failed})
        elif self.path == "/selected.zip":
            length = int(self.headers.get("Content-Length", 0))
            try:
                names = json.loads(self.rfile.read(length)).get("names", [])
            except (json.JSONDecodeError, AttributeError):
                self.send_error(400)
                return
            self.send_zip(names)
        elif self.path.startswith("/delete/"):
            name = unquote(self.path[len("/delete/"):])
            p = safe_photo_path(PHOTO_DIR, name)
            if p:
                p.unlink()
                thumb = THUMB_DIR / name
                thumb.unlink(missing_ok=True)
                self.send_json({"deleted": name})
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def render_gallery(self):
        photos = list_photos()
        stats = storage_stats()
        parts = [GALLERY_PAGE_TOP]
        parts.append(
            f'<header><b>pillbox gallery ({len(photos)})</b><span>'
            '<a href="/status">Status</a>'
            '<a href="/all.zip">Download all (zip)</a>'
            '<a href="/">&larr; Camera</a></span></header>'
        )
        parts.append(
            f'<div class="stats">{fmt_bytes(stats["photos_bytes"])} in photos &middot; '
            f'{fmt_bytes(stats["free_bytes"])} free on card</div>'
        )
        if stats["free_bytes"] < LOW_SPACE_WARN:
            parts.append(
                '<div class="banner">&#9888; SD card is getting full — captures stop '
                f'below {fmt_bytes(CAPTURE_MIN_FREE)} free. Delete or download photos.</div>'
            )
        if not photos:
            parts.append('<div class="empty">No photos yet — go take some.</div>')
        else:
            parts.append(
                '<div class="toolbar">'
                '<button id="selmode" onclick="toggleSelect()">Select</button>'
                '<button id="dlsel" onclick="dlSelected()" '
                'style="display:none" disabled>Download selected</button>'
                '<button id="delsel" class="danger" onclick="delSelected()" '
                'style="display:none" disabled>Delete selected</button></div>'
            )
        parts.append('<div class="grid">')
        for name in photos:
            parts.append(f"""
<div class="card" id="card-{name}">
  <input type="checkbox" class="sel" value="{name}" style="display:none" onchange="selChanged()">
  <a href="/photos/{name}" target="_blank" onclick="return imgClick(event, this)"><img loading="lazy" src="/thumbs/{name}"></a>
  <div class="meta">{name}
    <div class="row">
      <a href="/photos/{name}?download">Download</a>
      <button onclick="del('{name}')">Delete</button>
    </div>
    <button class="analyze-btn" onclick="analyze('{name}', this)">Analyze</button>
  </div>
</div>""")
        parts.append("""
</div>
<script>
async function del(name) {
  if (!confirm('Delete ' + name + '?')) return;
  try {
    const r = await fetch('/delete/' + encodeURIComponent(name), {method: 'POST'});
    if (r.ok) document.getElementById('card-' + name).remove();
    else alert('Could not delete ' + name + ' (server error).');
  } catch (e) {
    alert('Could not delete ' + name + ': ' + e);
  }
}
let selecting = false;
function toggleSelect() {
  selecting = !selecting;
  document.getElementById('selmode').textContent = selecting ? 'Cancel' : 'Select';
  const disp = selecting ? '' : 'none';
  document.getElementById('dlsel').style.display = disp;
  document.getElementById('delsel').style.display = disp;
  document.querySelectorAll('.card').forEach(c => c.classList.toggle('selecting', selecting));
  document.querySelectorAll('.sel').forEach(cb => {
    cb.style.display = selecting ? '' : 'none';
    if (!selecting) cb.checked = false;
  });
  selChanged();
}
// While selecting, tapping the thumbnail toggles selection instead of opening
// the photo — the tiny corner checkbox is too easy to miss, especially on phones.
function imgClick(e, link) {
  if (!selecting) return true;  // normal mode: follow the link to the full photo
  e.preventDefault();
  const cb = link.closest('.card').querySelector('.sel');
  cb.checked = !cb.checked;
  selChanged();
  return false;
}
function selChanged() {
  document.querySelectorAll('.sel').forEach(cb =>
    cb.closest('.card').classList.toggle('selected', cb.checked));
  const n = document.querySelectorAll('.sel:checked').length;
  const del = document.getElementById('delsel');
  del.disabled = n === 0;
  del.textContent = n ? `Delete selected (${n})` : 'Delete selected';
  const dl = document.getElementById('dlsel');
  dl.disabled = n === 0;
  dl.textContent = n ? `Download selected (${n})` : 'Download selected';
}
async function dlSelected() {
  const names = [...document.querySelectorAll('.sel:checked')].map(cb => cb.value);
  if (!names.length) return;
  if (names.length === 1) {  // a single photo downloads directly, no zip to unpack
    window.location = '/photos/' + encodeURIComponent(names[0]) + '?download';
    return;
  }
  const btn = document.getElementById('dlsel');
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Preparing zip…';
  try {
    const r = await fetch('/selected.zip', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({names}),
    });
    if (!r.ok) { alert('Download failed (server error). Please try again.'); return; }
    const blob = await r.blob();
    const cd = r.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename="([^"]+)"/);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = m ? m[1] : 'pillbox_photos.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert('Download failed: ' + e);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}
async function delSelected() {
  const names = [...document.querySelectorAll('.sel:checked')].map(cb => cb.value);
  if (!names.length || !confirm(`Delete ${names.length} photo(s)?`)) return;
  try {
    const r = await fetch('/delete-many', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({names}),
    });
    if (!r.ok) { alert('Delete failed (server error). Please try again.'); return; }
    const data = await r.json();
    const failed = (data.failed || []).length;
    if (failed) alert(`Deleted ${data.deleted.length}; ${failed} could not be deleted.`);
    location.reload();
  } catch (e) {
    alert('Delete failed: ' + e);
  }
}
// --- Per-photo analysis (DoG + CNN + YOLO), same detectors as /status ---
const A_DAYS = ["SUN","MON","TUE","WED","THU","FRI","SAT"];
const A_SLOTS = ["MORN","NOON","NIGHT"];
function esc(s) {
  return String(s).replace(/[&<>"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
function cellTitle(r, metric) {
  const v = r[metric];
  if (typeof v !== 'number') return '';
  if (metric === 'prob' || metric === 'conf') return Math.round(v * 100) + '%';
  return v.toFixed(3);
}
function renderGrid(results, metric) {
  let h = '<div class="wrap"><table><tr><th></th>';
  for (const d of A_DAYS) h += '<th>' + d + '</th>';
  h += '</tr>';
  for (const slot of A_SLOTS) {
    h += '<tr><th>' + slot + '</th>';
    for (const d of A_DAYS) {
      const r = results[d + '_' + slot];
      const cls = r.pill ? 'pill' : 'empty';
      const label = r.pill ? 'pill' : '—';
      h += '<td class="' + cls + '" title="' + esc(cellTitle(r, metric)) + '">' + label + '</td>';
    }
    h += '</tr>';
  }
  return h + '</table></div>';
}
function renderAnalysis(data) {
  let h = '';
  for (const key of Object.keys(data.methods)) {
    const m = data.methods[key];
    h += '<div class="method">';
    if (m.error) {
      h += '<h3>' + esc(m.label) + ' <span class="desc">' + esc(m.desc) + '</span></h3>';
      h += '<div class="aerr">⚠ ' + esc(m.error) + '</div>';
    } else {
      h += '<h3>' + esc(m.label) + ' <span class="count">' + m.count +
           '/21 cells</span> <span class="desc">' + esc(m.desc) + '</span></h3>';
      h += renderGrid(m.results, m.metric);
    }
    h += '</div>';
  }
  return h;
}
function openAnalyze() { document.getElementById('amodal').classList.add('show'); }
function closeAnalyze(e) {
  // Backdrop click closes; clicks inside the box (e.currentTarget !== target) don't.
  if (e === true || e.target === e.currentTarget) {
    document.getElementById('amodal').classList.remove('show');
  }
}
async function analyze(name, btn) {
  const body = document.getElementById('abody');
  document.getElementById('atitle').textContent = name;
  body.innerHTML = '<div class="aloading">Running DoG, CNN and YOLO on ' +
    esc(name) + '&hellip;<br>this can take a moment on the Pi.</div>';
  openAnalyze();
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/analyze?photo=' + encodeURIComponent(name));
    const data = await r.json();
    if (!r.ok) {
      body.innerHTML = '<div class="aerr">⚠ ' + esc(data.error || 'Analysis failed.') + '</div>';
    } else {
      body.innerHTML = renderAnalysis(data);
    }
  } catch (e) {
    body.innerHTML = '<div class="aerr">⚠ ' + esc(e) + '</div>';
  } finally {
    if (btn) btn.disabled = false;
  }
}
</script>
<div id="amodal" onclick="closeAnalyze(event)">
  <div class="abox">
    <div class="ahead"><b id="atitle"></b><button onclick="closeAnalyze(true)" title="Close">&times;</button></div>
    <div id="abody"></div>
  </div>
</div>
</body>
</html>""")
        return "".join(parts)

    def stream_mjpeg(self):
        self.send_response(200)
        self.send_header("Age", 0)
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
        self.end_headers()
        try:
            while True:
                with camera.output.condition:
                    camera.output.condition.wait()
                    frame = camera.output.frame
                self.wfile.write(b"--FRAME\r\n")
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", len(frame))
                self.end_headers()
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_zip(self, names=None):
        # names=None zips the whole gallery; otherwise just the ones given
        # (skipping any that don't resolve to a real photo).
        if names is None:
            photos = list_photos()
        else:
            photos = [n for n in names if safe_photo_path(PHOTO_DIR, n)]
        if not photos:
            self.send_error(404)
            return
        # JPEGs don't recompress, so store rather than deflate; build on disk
        # to keep memory flat regardless of how many photos exist.
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
                for name in photos:
                    zf.write(PHOTO_DIR / name, arcname=name)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.send_file(tmp_path, "application/zip", download_name=f"pillbox_photos_{stamp}.zip")
        finally:
            tmp_path.unlink(missing_ok=True)


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


PHOTO_DIR.mkdir(exist_ok=True)
THUMB_DIR.mkdir(exist_ok=True)
camera = Camera()

try:
    StreamingServer(("", 8000), Handler).serve_forever()
finally:
    camera.picam2.stop_recording()
