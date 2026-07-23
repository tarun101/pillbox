import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import camera_backends


class FrameOutputTests(unittest.TestCase):
    def test_write_publishes_immutable_jpeg_bytes(self):
        output = camera_backends.FrameOutput()
        source = bytearray(b"jpeg")
        output.write(source)
        source[:] = b"xxxx"
        self.assertEqual(output.frame, b"jpeg")


class CameraSelectionTests(unittest.TestCase):
    def setUp(self):
        self.paths = (Path("/photos"), Path("/thumbs"))
        self.sizes = ((1024, 576), (4608, 2592), (400, 400))

    @mock.patch.object(camera_backends, "Picamera2Camera")
    def test_explicit_picamera2_backend(self, pi_camera):
        with mock.patch.dict(
            os.environ, {"PILLBOX_CAMERA_BACKEND": "picamera2"}, clear=True
        ):
            camera_backends.create_camera(*self.paths, *self.sizes)
        pi_camera.assert_called_once()

    @mock.patch.object(camera_backends, "V4L2Camera")
    def test_explicit_wyze_v4l2_configuration(self, v4l2_camera):
        env = {
            "PILLBOX_CAMERA_BACKEND": "v4l2",
            "PILLBOX_VIDEO_DEVICE": "/dev/video2",
            "PILLBOX_USB_WIDTH": "1280",
            "PILLBOX_USB_HEIGHT": "720",
            "PILLBOX_USB_FPS": "10",
            "PILLBOX_USB_FOURCC": "YUYV",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            camera_backends.create_camera(*self.paths, *self.sizes)
        _, kwargs = v4l2_camera.call_args
        self.assertEqual(kwargs["device"], "/dev/video2")
        self.assertEqual(kwargs["width"], 1280)
        self.assertEqual(kwargs["height"], 720)
        self.assertEqual(kwargs["fps"], 10)
        self.assertEqual(kwargs["fourcc"], "YUYV")

    @mock.patch.object(camera_backends, "V4L2Camera")
    @mock.patch.object(
        camera_backends, "Picamera2Camera", side_effect=RuntimeError("no Pi camera")
    )
    def test_auto_falls_back_to_v4l2(self, _pi_camera, v4l2_camera):
        with mock.patch.dict(
            os.environ, {"PILLBOX_CAMERA_BACKEND": "auto"}, clear=True
        ):
            camera_backends.create_camera(*self.paths, *self.sizes)
        v4l2_camera.assert_called_once()

    def test_rejects_unknown_backend(self):
        with mock.patch.dict(
            os.environ, {"PILLBOX_CAMERA_BACKEND": "wyze"}, clear=True
        ):
            with self.assertRaisesRegex(
                RuntimeError, "auto, picamera2, v4l2, or esp32"
            ):
                camera_backends.create_camera(*self.paths, *self.sizes)

    @mock.patch.object(camera_backends, "ESP32Camera")
    def test_explicit_esp32_uses_standard_camerawebserver_urls(self, esp32_camera):
        env = {
            "PILLBOX_CAMERA_BACKEND": "esp32",
            "PILLBOX_ESP32_BASE_URL": "http://192.168.4.20",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            camera_backends.create_camera(*self.paths, *self.sizes)
        _, kwargs = esp32_camera.call_args
        self.assertEqual(kwargs["capture_url"], "http://192.168.4.20/capture")
        self.assertEqual(kwargs["stream_url"], "http://192.168.4.20:81/stream")


class ESP32ProtocolTests(unittest.TestCase):
    def test_default_urls_follow_espressif_port_layout(self):
        self.assertEqual(
            camera_backends._default_esp32_urls("esp-cam.local"),
            (
                "http://esp-cam.local/capture",
                "http://esp-cam.local:81/stream",
            ),
        )
        self.assertEqual(
            camera_backends._default_esp32_urls("http://10.0.0.5:8080"),
            (
                "http://10.0.0.5:8080/capture",
                "http://10.0.0.5:8081/stream",
            ),
        )

    def test_extracts_complete_jpeg_from_multipart_chunks(self):
        first = b"\xff\xd8first\xff\xd9"
        second = b"\xff\xd8second\xff\xd9"
        frame, remainder = camera_backends._pop_jpeg(
            b"--boundary\r\nheaders\r\n\r\n" + first + b"\r\n" + second
        )
        self.assertEqual(frame, first)
        next_frame, remainder = camera_backends._pop_jpeg(remainder)
        self.assertEqual(next_frame, second)
        self.assertEqual(remainder, b"")

    def test_keeps_partial_jpeg_for_next_network_read(self):
        partial = b"noise\xff\xd8unfinished"
        frame, remainder = camera_backends._pop_jpeg(partial)
        self.assertIsNone(frame)
        self.assertEqual(remainder, b"\xff\xd8unfinished")

    def test_network_backend_streams_and_saves_capture_jpeg(self):
        stream_jpeg = b"\xff\xd8stream\xff\xd9"
        capture_jpeg = b"\xff\xd8capture\xff\xd9"

        class Response:
            def __init__(self, chunks):
                self.chunks = list(chunks)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return self.chunks.pop(0) if self.chunks else b""

        def fake_urlopen(request, timeout):
            self.assertGreater(timeout, 0)
            if request.full_url.endswith("/capture"):
                return Response([capture_jpeg])
            return Response([b"multipart headers\r\n" + stream_jpeg])

        with TemporaryDirectory() as temp:
            root = Path(temp)
            photos, thumbs = root / "photos", root / "thumbs"
            photos.mkdir()
            thumbs.mkdir()
            with mock.patch.object(camera_backends, "urlopen", fake_urlopen), \
                    mock.patch.object(camera_backends, "_thumbnail"):
                camera = camera_backends.ESP32Camera(
                    photos,
                    thumbs,
                    (1024, 576),
                    (4608, 2592),
                    (400, 400),
                    capture_url="http://esp/capture",
                    stream_url="http://esp:81/stream",
                    startup_timeout=1,
                )
                try:
                    self.assertEqual(camera.output.frame, stream_jpeg)
                    name = camera.capture_still()
                    self.assertEqual((photos / name).read_bytes(), capture_jpeg)
                finally:
                    camera.close()


class _FakeFrame:
    def copy(self):
        return self


class _FakeEncoded:
    def tobytes(self):
        return b"jpeg-frame"


class _FakeCapture:
    def __init__(self, *_args):
        self.released = False

    def isOpened(self):
        return True

    def set(self, *_args):
        return True

    def get(self, prop):
        return {3: 1920, 4: 1080, 5: 15}.get(prop, 0)

    def read(self):
        time.sleep(0.001)
        return (False, None) if self.released else (True, _FakeFrame())

    def release(self):
        self.released = True


class V4L2CameraTests(unittest.TestCase):
    def test_streams_and_captures_latest_uvc_frame(self):
        fake_cv2 = SimpleNamespace(
            CAP_V4L2=200,
            CAP_PROP_FOURCC=6,
            CAP_PROP_FRAME_WIDTH=3,
            CAP_PROP_FRAME_HEIGHT=4,
            CAP_PROP_FPS=5,
            CAP_PROP_BUFFERSIZE=38,
            IMWRITE_JPEG_QUALITY=1,
            VideoCapture=_FakeCapture,
            VideoWriter_fourcc=lambda *_args: 0,
            imencode=lambda *_args: (True, _FakeEncoded()),
            imwrite=lambda *_args: True,
        )
        with TemporaryDirectory() as temp:
            root = Path(temp)
            photos, thumbs = root / "photos", root / "thumbs"
            photos.mkdir()
            thumbs.mkdir()
            with mock.patch.dict(sys.modules, {"cv2": fake_cv2}), \
                    mock.patch.object(camera_backends, "_thumbnail"):
                camera = camera_backends.V4L2Camera(
                    photos,
                    thumbs,
                    (1024, 576),
                    (4608, 2592),
                    (400, 400),
                    startup_timeout=1,
                )
                try:
                    self.assertEqual(camera.output.frame, b"jpeg-frame")
                    self.assertRegex(camera.capture_still(), r"photo_\d{8}_\d{6}\.jpg")
                finally:
                    camera.close()


if __name__ == "__main__":
    unittest.main()
