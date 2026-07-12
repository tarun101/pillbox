#!/usr/bin/env python3
"""Full sensor resolution (4608x2592) live stream. Pi 5 only.

The Pi 4's hardware MJPEG/H.264 encoder caps out at 1920x1920 (see
camera_stream_full.py), so this resolution isn't reachable there. The Pi 5
dropped that hardware encoder block entirely, so picamera2 falls back to
software JPEG encoding (libavcodec) with no fixed resolution ceiling.
Measured ~10.4 fps at this resolution on a Pi 5, close to the imx708
sensor's own 14.35 fps ceiling at full res.
"""
import io
import socketserver
from http import server
from threading import Condition

from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

PAGE = """\
<html>
<head><title>picam live preview (full sensor res)</title></head>
<body style="margin:0;background:#111">
<img src="stream.mjpg" style="width:100%;height:auto;display:block" />
</body>
</html>
"""


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            content = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Age", 0)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b"--FRAME\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_error(404)


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (4608, 2592)}))
output = StreamingOutput()
picam2.start_recording(MJPEGEncoder(), FileOutput(output))

try:
    StreamingServer(("", 8000), StreamingHandler).serve_forever()
finally:
    picam2.stop_recording()
