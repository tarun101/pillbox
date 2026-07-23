#pragma once

// Copy this file to pillwatch_config.h and enter the 2.4 GHz Wi-Fi credentials used by
// the Pi or Jetson. Leave WIFI_SSID empty to run the camera as its own access
// point at http://192.168.4.1.
#define WIFI_SSID ""
#define WIFI_PASSWORD ""

// The camera is also advertised as http://pillwatch-cam.local when mDNS is
// supported by the client network.
#define DEVICE_HOSTNAME "pillwatch-cam"

// Used only when WIFI_SSID is empty or the configured network cannot be
// reached within WIFI_CONNECT_TIMEOUT_MS.
#define FALLBACK_AP_SSID "PillWatch-Camera"
#define FALLBACK_AP_PASSWORD "pillwatchcam"
#define WIFI_CONNECT_TIMEOUT_MS 30000

// UXGA is 1600x1200. It preserves substantially more pillbox detail than the
// QVGA default in many CameraWebServer examples while remaining streamable.
#define CAMERA_FRAME_SIZE FRAMESIZE_UXGA

// Lower values mean higher JPEG quality. Espressif recommends 10-63.
#define CAMERA_JPEG_QUALITY 10
#define CAMERA_FRAME_BUFFERS 2

// Change these if the physical camera mount needs correction.
#define CAMERA_VERTICAL_FLIP false
#define CAMERA_HORIZONTAL_MIRROR false
