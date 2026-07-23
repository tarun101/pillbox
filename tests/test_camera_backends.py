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
            with self.assertRaisesRegex(RuntimeError, "auto, picamera2, or v4l2"):
                camera_backends.create_camera(*self.paths, *self.sizes)


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
