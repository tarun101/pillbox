"""Camera backends for Picamera2, Linux V4L2/UVC, and ESP32-CAM."""

import base64
import io
import os
import sys
import time
from datetime import datetime
from threading import Condition, Event, Lock, Thread
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from PIL import Image


class FrameOutput(io.BufferedIOBase):
    """Latest JPEG frame plus a condition used by the MJPEG HTTP handler."""

    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = bytes(buf)
            self.condition.notify_all()


def _thumbnail(photo_path, thumb_path, thumb_max):
    with Image.open(photo_path) as image:
        image.thumbnail(thumb_max)
        image.save(thumb_path, quality=70)


class Picamera2Camera:
    """Raspberry Pi camera backend preserving the original application flow."""

    backend_name = "picamera2"

    def __init__(self, photo_dir, thumb_dir, stream_size, still_size, thumb_max):
        try:
            from picamera2 import Picamera2
            from picamera2.encoders import MJPEGEncoder
            from picamera2.outputs import FileOutput
        except ImportError as exc:
            raise RuntimeError(
                "Picamera2 is unavailable; install python3-picamera2 or select "
                "PILLBOX_CAMERA_BACKEND=v4l2"
            ) from exc

        self._mjpeg_encoder = MJPEGEncoder
        self._file_output = FileOutput
        self.photo_dir = photo_dir
        self.thumb_dir = thumb_dir
        self.thumb_max = thumb_max
        self.output = FrameOutput()
        self.lock = Lock()
        self.picam2 = Picamera2()
        self.video_config = self.picam2.create_video_configuration(
            main={"size": stream_size}
        )
        self.still_config = self.picam2.create_still_configuration(
            main={"size": still_size}
        )
        self.picam2.configure(self.video_config)
        self.picam2.start_recording(
            self._mjpeg_encoder(), self._file_output(self.output)
        )

    def capture_still(self):
        name = datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")
        path = self.photo_dir / name
        with self.lock:
            self.picam2.stop_recording()
            try:
                self.picam2.configure(self.still_config)
                self.picam2.start()
                self.picam2.capture_file(str(path))
                self.picam2.stop()
            finally:
                self.picam2.configure(self.video_config)
                self.picam2.start_recording(
                    self._mjpeg_encoder(), self._file_output(self.output)
                )
        _thumbnail(path, self.thumb_dir / name, self.thumb_max)
        return name

    def close(self):
        self.picam2.stop_recording()


class V4L2Camera:
    """Linux UVC/V4L2 backend, including the Wyze Cam v2 webcam firmware."""

    backend_name = "v4l2"

    def __init__(
        self,
        photo_dir,
        thumb_dir,
        stream_size,
        still_size,
        thumb_max,
        device="/dev/video0",
        width=1920,
        height=1080,
        fps=15,
        fourcc="MJPG",
        startup_timeout=8.0,
    ):
        del stream_size, still_size  # V4L2 uses the camera's configured stream.
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError(
                "the V4L2 camera backend needs OpenCV; install "
                "opencv-python-headless or python3-opencv"
            ) from exc

        self.cv2 = cv2
        self.photo_dir = photo_dir
        self.thumb_dir = thumb_dir
        self.thumb_max = thumb_max
        self.device = device
        self.output = FrameOutput()
        self._frame_lock = Lock()
        self._latest_frame = None
        self._last_error = None
        self._stop = Event()

        source = int(device) if str(device).isdigit() else str(device)
        self._capture = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not self._capture.isOpened():
            self._capture.release()
            raise RuntimeError(
                f"cannot open UVC/V4L2 camera {device}; check `v4l2-ctl "
                "--list-devices` and PILLBOX_VIDEO_DEVICE"
            )
        if len(fourcc) != 4:
            self._capture.release()
            raise RuntimeError("PILLBOX_USB_FOURCC must contain exactly 4 characters")
        self._capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._capture.set(cv2.CAP_PROP_FPS, fps)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._thread = Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + startup_timeout
        with self.output.condition:
            while self.output.frame is None and time.monotonic() < deadline:
                self.output.condition.wait(timeout=0.2)
        if self.output.frame is None:
            error = self._last_error or "no frames received"
            self.close()
            raise RuntimeError(f"camera {device} opened but produced no frames: {error}")

        actual_width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self._capture.get(cv2.CAP_PROP_FPS)
        print(
            f"camera backend=v4l2 device={device} "
            f"mode={actual_width}x{actual_height}@{actual_fps:g}",
            file=sys.stderr,
        )

    def _capture_loop(self):
        while not self._stop.is_set():
            ok, frame = self._capture.read()
            if not ok:
                self._last_error = "V4L2 frame read failed"
                self._stop.wait(0.1)
                continue
            with self._frame_lock:
                self._latest_frame = frame
            ok, encoded = self.cv2.imencode(
                ".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, 85]
            )
            if ok:
                self.output.write(encoded.tobytes())
            else:
                self._last_error = "JPEG encoding failed"

    def capture_still(self):
        with self._frame_lock:
            frame = None if self._latest_frame is None else self._latest_frame.copy()
        if frame is None:
            raise RuntimeError(self._last_error or "camera has not produced a frame")
        name = datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")
        path = self.photo_dir / name
        if not self.cv2.imwrite(
            str(path), frame, [self.cv2.IMWRITE_JPEG_QUALITY, 95]
        ):
            raise RuntimeError(f"failed to save camera frame to {path}")
        _thumbnail(path, self.thumb_dir / name, self.thumb_max)
        return name

    def close(self):
        if self._stop.is_set():
            return
        self._stop.set()
        self._capture.release()
        self._thread.join(timeout=2)


def _default_esp32_urls(base_url):
    """Return capture/stream URLs for Espressif's CameraWebServer layout."""
    parsed = urlsplit(base_url if "://" in base_url else f"http://{base_url}")
    if not parsed.hostname:
        raise RuntimeError(f"invalid PILLBOX_ESP32_BASE_URL: {base_url}")
    capture_port = parsed.port or 80
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    capture_netloc = f"{host}:{capture_port}" if capture_port != 80 else host
    stream_netloc = f"{host}:{capture_port + 1}"
    capture_url = urlunsplit(
        (parsed.scheme or "http", capture_netloc, "/capture", "", "")
    )
    stream_url = urlunsplit(
        (parsed.scheme or "http", stream_netloc, "/stream", "", "")
    )
    return capture_url, stream_url


def _pop_jpeg(buffer):
    """Pop one complete JPEG from an arbitrary MJPEG byte buffer."""
    start = buffer.find(b"\xff\xd8")
    if start < 0:
        return None, buffer[-1:]
    end = buffer.find(b"\xff\xd9", start + 2)
    if end < 0:
        return None, buffer[start:]
    return buffer[start:end + 2], buffer[end + 2:]


class ESP32Camera:
    """HTTP JPEG/MJPEG backend for Espressif CameraWebServer firmware."""

    backend_name = "esp32"
    _MAX_JPEG_BYTES = 12 * 1024 * 1024

    def __init__(
        self,
        photo_dir,
        thumb_dir,
        stream_size,
        still_size,
        thumb_max,
        capture_url,
        stream_url,
        username=None,
        password=None,
        startup_timeout=10.0,
    ):
        del stream_size, still_size
        self.photo_dir = photo_dir
        self.thumb_dir = thumb_dir
        self.thumb_max = thumb_max
        self.capture_url = capture_url
        self.stream_url = stream_url
        self.output = FrameOutput()
        self._frame_lock = Lock()
        self._latest_jpeg = None
        self._last_error = None
        self._stop = Event()
        self._headers = {"User-Agent": "PillWatch/1.0"}
        if username:
            token = base64.b64encode(
                f"{username}:{password or ''}".encode()
            ).decode()
            self._headers["Authorization"] = f"Basic {token}"

        self._thread = Thread(target=self._stream_loop, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + startup_timeout
        with self.output.condition:
            while self.output.frame is None and time.monotonic() < deadline:
                self.output.condition.wait(timeout=0.2)
        if self.output.frame is None:
            error = self._last_error or "no JPEG frames received"
            self.close()
            raise RuntimeError(
                f"ESP32-CAM stream produced no frames at {stream_url}: {error}"
            )
        print(
            f"camera backend=esp32 stream={stream_url} capture={capture_url}",
            file=sys.stderr,
        )

    def _request(self, url):
        return Request(url, headers=self._headers)

    def _publish(self, jpeg):
        with self._frame_lock:
            self._latest_jpeg = jpeg
        self.output.write(jpeg)

    def _stream_loop(self):
        while not self._stop.is_set():
            try:
                with urlopen(self._request(self.stream_url), timeout=5) as response:
                    buffer = b""
                    while not self._stop.is_set():
                        chunk = response.read(4096)
                        if not chunk:
                            raise RuntimeError("stream closed")
                        buffer += chunk
                        while True:
                            jpeg, buffer = _pop_jpeg(buffer)
                            if jpeg is None:
                                break
                            self._publish(jpeg)
                        if len(buffer) > self._MAX_JPEG_BYTES:
                            raise RuntimeError("JPEG frame exceeded safety limit")
            except Exception as exc:
                self._last_error = str(exc)
                self._stop.wait(0.5)

    def _fetch_still(self):
        with urlopen(self._request(self.capture_url), timeout=8) as response:
            data = response.read(self._MAX_JPEG_BYTES + 1)
        if len(data) > self._MAX_JPEG_BYTES:
            raise RuntimeError("captured JPEG exceeded safety limit")
        start, end = data.find(b"\xff\xd8"), data.rfind(b"\xff\xd9")
        if start < 0 or end < start:
            raise RuntimeError("capture endpoint did not return a JPEG")
        return data[start:end + 2]

    def capture_still(self):
        try:
            jpeg = self._fetch_still()
        except Exception as exc:
            with self._frame_lock:
                jpeg = self._latest_jpeg
            if jpeg is None:
                raise RuntimeError(f"ESP32-CAM capture failed: {exc}") from exc
        name = datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")
        path = self.photo_dir / name
        path.write_bytes(jpeg)
        _thumbnail(path, self.thumb_dir / name, self.thumb_max)
        return name

    def close(self):
        if self._stop.is_set():
            return
        self._stop.set()
        self._thread.join(timeout=2)


def create_camera(photo_dir, thumb_dir, stream_size, still_size, thumb_max):
    """Create the configured camera backend.

    ``auto`` prefers Picamera2, then V4L2, then a configured ESP32 base URL.
    Explicit configuration avoids accidentally selecting a USB capture dongle
    on a Raspberry Pi.
    """
    backend = os.environ.get("PILLBOX_CAMERA_BACKEND", "auto").strip().lower()
    common = (photo_dir, thumb_dir, stream_size, still_size, thumb_max)
    if backend == "picamera2":
        return Picamera2Camera(*common)
    if backend == "v4l2":
        return V4L2Camera(
            *common,
            device=os.environ.get("PILLBOX_VIDEO_DEVICE", "/dev/video0"),
            width=int(os.environ.get("PILLBOX_USB_WIDTH", "1920")),
            height=int(os.environ.get("PILLBOX_USB_HEIGHT", "1080")),
            fps=int(os.environ.get("PILLBOX_USB_FPS", "15")),
            fourcc=os.environ.get("PILLBOX_USB_FOURCC", "MJPG"),
        )
    if backend == "esp32":
        base_url = os.environ.get("PILLBOX_ESP32_BASE_URL")
        capture_url = os.environ.get("PILLBOX_ESP32_CAPTURE_URL")
        stream_url = os.environ.get("PILLBOX_ESP32_STREAM_URL")
        if base_url:
            default_capture, default_stream = _default_esp32_urls(base_url)
            capture_url = capture_url or default_capture
            stream_url = stream_url or default_stream
        if not capture_url or not stream_url:
            raise RuntimeError(
                "ESP32 backend needs PILLBOX_ESP32_BASE_URL or both "
                "PILLBOX_ESP32_CAPTURE_URL and PILLBOX_ESP32_STREAM_URL"
            )
        return ESP32Camera(
            *common,
            capture_url=capture_url,
            stream_url=stream_url,
            username=os.environ.get("PILLBOX_ESP32_USERNAME"),
            password=os.environ.get("PILLBOX_ESP32_PASSWORD"),
        )
    if backend != "auto":
        raise RuntimeError(
            "PILLBOX_CAMERA_BACKEND must be auto, picamera2, v4l2, or esp32"
        )
    try:
        return Picamera2Camera(*common)
    except Exception as pi_error:
        try:
            return V4L2Camera(
                *common,
                device=os.environ.get("PILLBOX_VIDEO_DEVICE", "/dev/video0"),
                width=int(os.environ.get("PILLBOX_USB_WIDTH", "1920")),
                height=int(os.environ.get("PILLBOX_USB_HEIGHT", "1080")),
                fps=int(os.environ.get("PILLBOX_USB_FPS", "15")),
                fourcc=os.environ.get("PILLBOX_USB_FOURCC", "MJPG"),
            )
        except RuntimeError as usb_error:
            esp32_base = os.environ.get("PILLBOX_ESP32_BASE_URL")
            if esp32_base:
                capture_url, stream_url = _default_esp32_urls(esp32_base)
                return ESP32Camera(
                    *common,
                    capture_url=os.environ.get(
                        "PILLBOX_ESP32_CAPTURE_URL", capture_url
                    ),
                    stream_url=os.environ.get(
                        "PILLBOX_ESP32_STREAM_URL", stream_url
                    ),
                    username=os.environ.get("PILLBOX_ESP32_USERNAME"),
                    password=os.environ.get("PILLBOX_ESP32_PASSWORD"),
                )
            raise RuntimeError(
                f"no usable camera backend: Picamera2: {pi_error}; "
                f"V4L2: {usb_error}"
            ) from usb_error
