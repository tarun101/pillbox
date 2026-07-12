#!/usr/bin/env python3
"""Capture a single still photo at the sensor's full resolution (4608x2592 on imx708)."""
import sys
from datetime import datetime

from picamera2 import Picamera2

filename = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")

picam2 = Picamera2()
config = picam2.create_still_configuration(main={"size": (4608, 2592)})
picam2.configure(config)
picam2.start()
picam2.capture_file(filename)
picam2.stop()

print(f"Saved {filename}")
