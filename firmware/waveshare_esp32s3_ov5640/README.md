# PillWatch camera firmware for Waveshare ESP32-S3 + OV5640

This is a self-contained camera appliance for the Waveshare
**ESP32-S3-CAM-OV5640** (SKU 33699). It captures and streams JPEG images over
Wi-Fi for a Pi, Jetson, laptop, or server running the main PillWatch
application.

It does **not** run PillWatch's OpenCV/ONNX detection locally. The ESP32-S3
handles the camera and network connection; the host performs inference.

## HTTP interface

| Endpoint | Purpose |
| --- | --- |
| `http://<camera-ip>/` | Browser preview and diagnostics |
| `http://<camera-ip>/capture` | One UXGA JPEG still |
| `http://<camera-ip>/health` | JSON health information |
| `http://<camera-ip>:81/stream` | Multipart MJPEG stream |

These URLs match PillWatch's `PILLBOX_CAMERA_BACKEND=esp32` defaults.

## Prepare

1. Install [Visual Studio Code](https://code.visualstudio.com/) and the
   [PlatformIO extension](https://platformio.org/install/ide?install=vscode),
   or install the PlatformIO CLI.
2. Copy the example configuration:

   ```sh
   cd firmware/waveshare_esp32s3_ov5640
   cp include/pillwatch_config.example.h include/pillwatch_config.h
   ```

3. Edit `include/pillwatch_config.h` and set `WIFI_SSID` and `WIFI_PASSWORD`.
   The network must offer 2.4 GHz Wi-Fi. `pillwatch_config.h` is ignored by Git.

If `WIFI_SSID` is empty or the network cannot be reached within 30 seconds,
the board creates:

- SSID: `PillWatch-Camera`
- password: `pillwatchcam`
- address: `http://192.168.4.1`

Change those defaults in your private `pillwatch_config.h` before deploying
the camera.

## Build and flash

From this directory:

```sh
pio run
pio run --target upload
pio device monitor
```

The serial monitor prints the assigned address. If the USB port is not
recognized, disconnect USB, hold **BOOT**, reconnect USB, and release **BOOT**.
After flashing, press the board's power/reset button.

The default firmware captures at 1600x1200. This is intentional: it gives each
of the 21 pillbox cells useful image detail without making the MJPEG stream as
slow and fragile as the sensor's full 5 MP mode. Resolution, JPEG quality, and
orientation can be changed in `include/pillwatch_config.h`.

## Connect PillWatch

On the Pi or Jetson, add:

```ini
PILLBOX_CAMERA_BACKEND=esp32
PILLBOX_ESP32_BASE_URL=http://pillwatch-cam.local
```

Use the numeric IP printed by the serial monitor if `.local` discovery is not
available:

```ini
PILLBOX_ESP32_BASE_URL=http://192.168.1.50
```

Test the camera before starting PillWatch:

```sh
curl --fail http://pillwatch-cam.local/health
curl --fail http://pillwatch-cam.local/capture -o pillwatch-test.jpg
curl --fail --max-time 3 http://pillwatch-cam.local:81/stream -o /dev/null
```

## Installation notes

- Use a stable USB-C power supply and disable any router setting that isolates
  wireless clients from wired clients.
- Mount the camera rigidly, centered over the pillbox, with even lighting.
- This firmware intentionally has no public-internet or cloud integration.
  Keep it on a trusted LAN or isolated IoT network.
- The pin map and camera-power sequence are specific to Waveshare's
  ESP32-S3-CAM-OVxxxx board. Do not flash this onto the HiLetgo ESP8266
  NodeMCU boards.

Hardware reference:
[Waveshare ESP32-S3-CAM-OVxxxx documentation](https://docs.waveshare.com/ESP32-S3-CAM-OVxxxx).
