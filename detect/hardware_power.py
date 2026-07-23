"""Portable board-power readers used by the paper statistics utility."""

import re
import shutil
import subprocess


def classify_device_model(model):
    """Classify a Linux device-tree model into a supported hardware family."""
    normalized = (model or "").lower()
    if "nvidia" in normalized or "jetson" in normalized:
        return "jetson"
    if "raspberry pi 5" in normalized:
        return "raspberry_pi_5"
    if "raspberry pi 4" in normalized:
        return "raspberry_pi_4"
    if "raspberry pi" in normalized:
        return "raspberry_pi"
    return "other"


def _read_pi_power_watts():
    """Return total Pi 5 PMIC rail power, or None when vcgencmd is unavailable."""
    try:
        out = subprocess.run(
            ["vcgencmd", "pmic_read_adc"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    volts, amps = {}, {}
    for line in out.stdout.splitlines():
        match = re.match(r"\s*(\S+)_([AV])\s+\S+=([0-9.]+)[AV]\s*$", line)
        if match:
            target = amps if match.group(2) == "A" else volts
            target[match.group(1)] = float(match.group(3))
    power = sum(rail_volts * amps[name]
                for name, rail_volts in volts.items() if name in amps)
    return round(power, 2) if power > 0 else None


_JETSON_TOTAL_RAILS = ("VDD_IN", "POM_5V_IN", "VIN_SYS_5V0", "VDD_TOTAL")


def _parse_tegrastats_power(text):
    """Extract total board input power from one or more tegrastats lines."""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    for rail in _JETSON_TOTAL_RAILS:
        # Examples vary by JetPack generation:
        #   VDD_IN 2532mW/2532mW
        #   POM_5V_IN 4286/4286
        match = re.search(
            rf"(?:^|\s){re.escape(rail)}\s+([0-9.]+)\s*(mW|W)?(?:/|\s|$)",
            text,
            re.IGNORECASE,
        )
        if match:
            value = float(match.group(1))
            unit = (match.group(2) or "mW").lower()
            watts = value if unit == "w" else value / 1000.0
            return round(watts, 3) if watts > 0 else None
    return None


def _read_jetson_power_watts():
    """Return Jetson input power from tegrastats, or None off Jetson.

    tegrastats has no portable one-shot flag across JetPack releases, so start
    it briefly, collect its first sample, and terminate it.
    """
    command = shutil.which("tegrastats")
    if command is None:
        return None
    try:
        proc = subprocess.Popen(
            [command, "--interval", "100"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            stdout, _ = proc.communicate(timeout=0.35)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                stdout, _ = proc.communicate(timeout=0.2)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, _ = proc.communicate()
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    return _parse_tegrastats_power(stdout)


def read_power_watts(with_source=False):
    """Read board input power on a Pi 5 or Jetson.

    By default this retains the original float-or-None API. ``with_source`` is
    used by the paper output so results record which hardware interface supplied
    the measurement. Pi 4 is supported as a latency-only target because it does
    not expose total board-power telemetry through ``vcgencmd``.
    """
    for source, reader in (
        ("raspberry_pi_pmic", _read_pi_power_watts),
        ("jetson_tegrastats", _read_jetson_power_watts),
    ):
        watts = reader()
        if watts is not None:
            return (watts, source) if with_source else watts
    return (None, None) if with_source else None
