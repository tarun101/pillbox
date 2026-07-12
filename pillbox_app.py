#!/usr/bin/env python3
"""Pillbox camera web app.

One process owns the camera and serves everything on port 8000:

  /            live preview + a capture button (full-res stills)
  /gallery     thumbnails of every photo taken; download one, all as zip, or delete
  /stream.mjpg the MJPEG feed used by the preview page

Captures are full sensor resolution (4608x2592 on the imx708). The camera has
to switch out of video mode for each still, so the preview freezes for a
moment per shot — the page shows a "Capturing…" overlay while that happens.

Photos land in ~/photos with thumbnails in ~/photos/.thumbs.
"""
import io
import json
import os
import socketserver
import tempfile
import zipfile
from datetime import datetime
from http import server
from pathlib import Path
from threading import Condition, Lock
from urllib.parse import unquote

from PIL import Image
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

PHOTO_DIR = Path.home() / "photos"
THUMB_DIR = PHOTO_DIR / ".thumbs"
STREAM_SIZE = (1024, 576)
STILL_SIZE = (4608, 2592)
THUMB_MAX = (400, 400)

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
  .stage { position:relative; }
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
<header><b>pillbox camera</b><a href="/gallery">Gallery &rarr;</a></header>
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
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr));
          gap:12px; padding:12px; }
  .card { background:#1c1c1c; border-radius:8px; overflow:hidden; }
  .card img { width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }
  .meta { padding:8px 10px; font-size:13px; }
  .meta .row { display:flex; justify-content:space-between; margin-top:6px; }
  .meta a { color:#8bf; text-decoration:none; }
  .meta button { background:none; border:none; color:#e66; cursor:pointer;
                 font-size:13px; padding:0; }
  .empty { padding:40px; text-align:center; color:#888; }
</style>
</head>
<body>
"""


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


def safe_photo_path(base_dir, name):
    """Resolve name inside base_dir, rejecting traversal attempts."""
    if name != os.path.basename(name):
        return None
    path = base_dir / name
    return path if path.is_file() else None


class Handler(server.BaseHTTPRequestHandler):
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
        if path == "/":
            self.send_html(CAPTURE_PAGE)
        elif path == "/gallery":
            self.send_html(self.render_gallery())
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
        if self.path == "/capture":
            try:
                name = camera.capture_still()
                self.send_json({"file": name})
            except Exception as e:  # report capture failures to the page
                self.send_json({"error": str(e)}, status=500)
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
        parts = [GALLERY_PAGE_TOP]
        parts.append(
            f'<header><b>pillbox gallery ({len(photos)})</b><span>'
            '<a href="/all.zip">Download all (zip)</a>'
            '<a href="/">&larr; Camera</a></span></header>'
        )
        if not photos:
            parts.append('<div class="empty">No photos yet — go take some.</div>')
        parts.append('<div class="grid">')
        for name in photos:
            parts.append(f"""
<div class="card" id="card-{name}">
  <a href="/photos/{name}" target="_blank"><img loading="lazy" src="/thumbs/{name}"></a>
  <div class="meta">{name}
    <div class="row">
      <a href="/photos/{name}?download">Download</a>
      <button onclick="del('{name}')">Delete</button>
    </div>
  </div>
</div>""")
        parts.append("""
</div>
<script>
async function del(name) {
  if (!confirm('Delete ' + name + '?')) return;
  const r = await fetch('/delete/' + name, {method: 'POST'});
  if (r.ok) document.getElementById('card-' + name).remove();
}
</script>
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

    def send_zip(self):
        photos = list_photos()
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
