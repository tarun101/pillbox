#include <Arduino.h>
#include <ESPmDNS.h>
#include <WiFi.h>
#include <Wire.h>
#include "esp_camera.h"
#include "esp_http_server.h"

#include "camera_pins.h"

#if __has_include("pillwatch_config.h")
#include "pillwatch_config.h"
#else
#include "pillwatch_config.example.h"
#warning "Using pillwatch_config.example.h; copy it to pillwatch_config.h to configure Wi-Fi"
#endif

namespace {

constexpr char kStreamContentType[] =
    "multipart/x-mixed-replace;boundary=frame";
constexpr char kStreamBoundary[] = "\r\n--frame\r\n";
constexpr char kStreamPart[] =
    "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

httpd_handle_t control_server = nullptr;
httpd_handle_t stream_server = nullptr;
bool fallback_ap_active = false;
unsigned long last_wifi_retry_ms = 0;

const char kIndexHtml[] PROGMEM = R"HTML(
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>PillWatch camera</title>
  <style>
    body { margin: 0; background: #111; color: #eee;
           font: 16px system-ui, sans-serif; text-align: center; }
    main { max-width: 900px; margin: auto; padding: 18px; }
    img { display: block; width: 100%; height: auto; margin: 16px 0;
          border-radius: 8px; background: #222; }
    a { color: #8cf; }
    code { color: #9e9; }
  </style>
</head>
<body>
  <main>
    <h1>PillWatch camera</h1>
    <p><a href="/capture">Download a JPEG still</a> ·
       <a href="/health">Health</a></p>
    <img id="feed" alt="Live camera stream">
    <p>Control: <code>/capture</code> · Stream:
       <code>:81/stream</code></p>
  </main>
  <script>
    document.getElementById("feed").src =
      location.protocol + "//" + location.hostname + ":81/stream";
  </script>
</body>
</html>
)HTML";

bool writeExpanderRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(EXPANDER_I2C_ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission(true) == 0;
}

bool enableCameraPower() {
  Wire.begin(CAMERA_PIN_SIOD, CAMERA_PIN_SIOC, 100000);
  const bool mode_ok = writeExpanderRegister(EXPANDER_MODE_REGISTER, 0xff);
  const bool output_ok = writeExpanderRegister(
      EXPANDER_OUTPUT_REGISTER, 1U << EXPANDER_CAMERA_POWER_BIT);
  delay(100);
  Wire.end();
  return mode_ok && output_ok;
}

bool initializeCamera() {
  if (!enableCameraPower()) {
    Serial.println("Camera power expander did not respond");
    return false;
  }

  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = CAMERA_PIN_D0;
  config.pin_d1 = CAMERA_PIN_D1;
  config.pin_d2 = CAMERA_PIN_D2;
  config.pin_d3 = CAMERA_PIN_D3;
  config.pin_d4 = CAMERA_PIN_D4;
  config.pin_d5 = CAMERA_PIN_D5;
  config.pin_d6 = CAMERA_PIN_D6;
  config.pin_d7 = CAMERA_PIN_D7;
  config.pin_xclk = CAMERA_PIN_XCLK;
  config.pin_pclk = CAMERA_PIN_PCLK;
  config.pin_vsync = CAMERA_PIN_VSYNC;
  config.pin_href = CAMERA_PIN_HREF;
  config.pin_sccb_sda = CAMERA_PIN_SIOD;
  config.pin_sccb_scl = CAMERA_PIN_SIOC;
  config.pin_pwdn = CAMERA_PIN_PWDN;
  config.pin_reset = CAMERA_PIN_RESET;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = CAMERA_FRAME_SIZE;
  config.jpeg_quality = CAMERA_JPEG_QUALITY;
  config.fb_count = CAMERA_FRAME_BUFFERS;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;

  const esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    Serial.printf("Camera initialization failed: 0x%x\n", error);
    return false;
  }

  sensor_t *sensor = esp_camera_sensor_get();
  if (sensor == nullptr) {
    Serial.println("Camera sensor was not detected");
    esp_camera_deinit();
    return false;
  }
  sensor->set_vflip(sensor, CAMERA_VERTICAL_FLIP ? 1 : 0);
  sensor->set_hmirror(sensor, CAMERA_HORIZONTAL_MIRROR ? 1 : 0);

  Serial.printf("Camera sensor PID: 0x%04x\n", sensor->id.PID);
  Serial.printf("PSRAM: %u bytes free\n", ESP.getFreePsram());
  return true;
}

void setCommonHeaders(httpd_req_t *request) {
  httpd_resp_set_hdr(request, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
}

esp_err_t indexHandler(httpd_req_t *request) {
  setCommonHeaders(request);
  httpd_resp_set_type(request, "text/html; charset=utf-8");
  return httpd_resp_send(request, kIndexHtml, HTTPD_RESP_USE_STRLEN);
}

esp_err_t healthHandler(httpd_req_t *request) {
  char body[384];
  const IPAddress ip = fallback_ap_active ? WiFi.softAPIP() : WiFi.localIP();
  const sensor_t *sensor = esp_camera_sensor_get();
  snprintf(
      body, sizeof(body),
      "{\"ok\":true,\"hostname\":\"%s\",\"ip\":\"%s\","
      "\"mode\":\"%s\",\"rssi\":%d,\"uptime_ms\":%lu,"
      "\"free_heap\":%u,\"free_psram\":%u,\"sensor_pid\":%u}",
      DEVICE_HOSTNAME, ip.toString().c_str(),
      fallback_ap_active ? "access-point" : "station",
      fallback_ap_active ? 0 : WiFi.RSSI(), millis(), ESP.getFreeHeap(),
      ESP.getFreePsram(), sensor == nullptr ? 0 : sensor->id.PID);
  setCommonHeaders(request);
  httpd_resp_set_type(request, "application/json");
  return httpd_resp_send(request, body, HTTPD_RESP_USE_STRLEN);
}

esp_err_t captureHandler(httpd_req_t *request) {
  camera_fb_t *frame = esp_camera_fb_get();
  if (frame == nullptr) {
    httpd_resp_send_err(
        request, HTTPD_500_INTERNAL_SERVER_ERROR, "Camera capture failed");
    return ESP_FAIL;
  }

  setCommonHeaders(request);
  httpd_resp_set_type(request, "image/jpeg");
  httpd_resp_set_hdr(
      request, "Content-Disposition", "inline; filename=pillwatch.jpg");
  const esp_err_t result = httpd_resp_send(
      request, reinterpret_cast<const char *>(frame->buf), frame->len);
  esp_camera_fb_return(frame);
  return result;
}

esp_err_t streamHandler(httpd_req_t *request) {
  esp_err_t result = httpd_resp_set_type(request, kStreamContentType);
  if (result != ESP_OK) {
    return result;
  }
  setCommonHeaders(request);

  char part_header[64];
  while (true) {
    camera_fb_t *frame = esp_camera_fb_get();
    if (frame == nullptr) {
      Serial.println("Stream capture failed");
      return ESP_FAIL;
    }

    const size_t header_length = snprintf(
        part_header, sizeof(part_header), kStreamPart, frame->len);
    result = httpd_resp_send_chunk(
        request, kStreamBoundary, strlen(kStreamBoundary));
    if (result == ESP_OK) {
      result = httpd_resp_send_chunk(request, part_header, header_length);
    }
    if (result == ESP_OK) {
      result = httpd_resp_send_chunk(
          request, reinterpret_cast<const char *>(frame->buf), frame->len);
    }
    esp_camera_fb_return(frame);

    if (result != ESP_OK) {
      break;
    }
  }
  return result;
}

bool startWebServers() {
  httpd_config_t control_config = HTTPD_DEFAULT_CONFIG();
  control_config.server_port = 80;
  control_config.ctrl_port = 32768;
  control_config.max_uri_handlers = 4;
  control_config.stack_size = 8192;
  control_config.lru_purge_enable = true;

  if (httpd_start(&control_server, &control_config) != ESP_OK) {
    Serial.println("Failed to start HTTP control server");
    return false;
  }

  const httpd_uri_t index_uri = {
      .uri = "/",
      .method = HTTP_GET,
      .handler = indexHandler,
      .user_ctx = nullptr,
  };
  const httpd_uri_t health_uri = {
      .uri = "/health",
      .method = HTTP_GET,
      .handler = healthHandler,
      .user_ctx = nullptr,
  };
  const httpd_uri_t capture_uri = {
      .uri = "/capture",
      .method = HTTP_GET,
      .handler = captureHandler,
      .user_ctx = nullptr,
  };
  httpd_register_uri_handler(control_server, &index_uri);
  httpd_register_uri_handler(control_server, &health_uri);
  httpd_register_uri_handler(control_server, &capture_uri);

  httpd_config_t stream_config = HTTPD_DEFAULT_CONFIG();
  stream_config.server_port = 81;
  stream_config.ctrl_port = 32769;
  stream_config.max_uri_handlers = 2;
  stream_config.stack_size = 8192;
  stream_config.lru_purge_enable = true;

  if (httpd_start(&stream_server, &stream_config) != ESP_OK) {
    Serial.println("Failed to start HTTP stream server");
    httpd_stop(control_server);
    control_server = nullptr;
    return false;
  }

  const httpd_uri_t stream_uri = {
      .uri = "/stream",
      .method = HTTP_GET,
      .handler = streamHandler,
      .user_ctx = nullptr,
  };
  httpd_register_uri_handler(stream_server, &stream_uri);
  return true;
}

void startFallbackAccessPoint() {
  fallback_ap_active = true;
  WiFi.disconnect(true);
  WiFi.mode(WIFI_AP);
  const bool started = WiFi.softAP(
      FALLBACK_AP_SSID, FALLBACK_AP_PASSWORD, 1, false, 2);
  if (!started) {
    Serial.println("Failed to start fallback access point");
    return;
  }
  Serial.printf("Access point: %s\n", FALLBACK_AP_SSID);
  Serial.printf("Camera URL: http://%s\n", WiFi.softAPIP().toString().c_str());
}

void connectNetwork() {
  WiFi.setSleep(false);
  WiFi.setHostname(DEVICE_HOSTNAME);

  if (strlen(WIFI_SSID) == 0) {
    startFallbackAccessPoint();
    return;
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("Connecting to Wi-Fi: %s", WIFI_SSID);
  const unsigned long started_ms = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - started_ms < WIFI_CONNECT_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi timed out; starting fallback access point");
    startFallbackAccessPoint();
    return;
  }

  Serial.printf("Camera URL: http://%s\n", WiFi.localIP().toString().c_str());
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\nPillWatch Waveshare ESP32-S3 camera starting");

  if (!psramFound()) {
    Serial.println("Fatal: 8 MB PSRAM was not detected");
    while (true) {
      delay(1000);
    }
  }
  if (!initializeCamera()) {
    Serial.println("Fatal: camera initialization failed");
    while (true) {
      delay(1000);
    }
  }

  connectNetwork();
  if (MDNS.begin(DEVICE_HOSTNAME)) {
    MDNS.addService("http", "tcp", 80);
    Serial.printf("mDNS URL: http://%s.local\n", DEVICE_HOSTNAME);
  }

  if (!startWebServers()) {
    Serial.println("Fatal: web server startup failed");
    while (true) {
      delay(1000);
    }
  }
  Serial.println("Still endpoint: /capture");
  Serial.println("MJPEG endpoint: :81/stream");
}

void loop() {
  if (!fallback_ap_active && WiFi.status() != WL_CONNECTED &&
      millis() - last_wifi_retry_ms >= 10000) {
    last_wifi_retry_ms = millis();
    Serial.println("Wi-Fi disconnected; reconnecting");
    WiFi.reconnect();
  }
  delay(250);
}
