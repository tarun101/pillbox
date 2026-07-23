#pragma once

// Waveshare ESP32-S3-CAM-OVxxxx 24-pin DVP camera connector.
// Source: Waveshare's official 02_CameraWebServer example.
#define CAMERA_PIN_PWDN -1
#define CAMERA_PIN_RESET -1
#define CAMERA_PIN_XCLK 38
#define CAMERA_PIN_SIOD 8
#define CAMERA_PIN_SIOC 7

#define CAMERA_PIN_D7 21
#define CAMERA_PIN_D6 39
#define CAMERA_PIN_D5 40
#define CAMERA_PIN_D4 42
#define CAMERA_PIN_D3 46
#define CAMERA_PIN_D2 48
#define CAMERA_PIN_D1 47
#define CAMERA_PIN_D0 45
#define CAMERA_PIN_VSYNC 17
#define CAMERA_PIN_HREF 18
#define CAMERA_PIN_PCLK 41

// The board's CH32V003 I/O expander enables camera power on output 6. It
// shares the camera SCCB pins for setup before esp_camera takes ownership.
#define EXPANDER_I2C_ADDRESS 0x24
#define EXPANDER_MODE_REGISTER 0x02
#define EXPANDER_OUTPUT_REGISTER 0x03
#define EXPANDER_CAMERA_POWER_BIT 6
