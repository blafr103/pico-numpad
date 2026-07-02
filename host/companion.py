# ------------------------------------------------------------------
# companion.py - host-side broadcaster for pico-numpad
# Replaces timesync.py: one script sends time AND hardware stats.
#
# Sends over the Pico's data serial port (auto-detected):
#   time:<epoch>                        on connect + every 30 s
#   cpu:<util%>,<tempC>,<powerW>        every STATS_INTERVAL_S
#   gpu:<util%>,<tempC>,<powerW>        every STATS_INTERVAL_S
# Values are integers; a missing sensor sends -1 (Pico shows --).
#
# Stats come from LibreHardwareMonitor's web server
# (http://localhost:8085/data.json). LHM must be running (as admin,
# web server enabled). If LHM is down, stats are skipped but time
# keeps working.
#
# Sensor matching: hardware prefix + path kind + display name,
# because numeric sensor indices shift between LHM versions.
# Prefixes/names below are from this PC's tree (i7-13700K,
# RTX 4080); another machine needs these four lines adjusted.
#
# Deps: pip install pyserial requests
# Auto-start: Task Scheduler -> run "pythonw.exe <path>\companion.py"
# at logon (pythonw = no console window).
# ------------------------------------------------------------------

import time
import serial
import serial.tools.list_ports
import requests

PORT = None             # e.g. "COM7" to override auto-detect
TIME_INTERVAL_S = 30
STATS_INTERVAL_S = 2
LHM_URL = "http://localhost:8085/data.json"
ADAFRUIT_VID = 0x239A


def find_port():
    if PORT:
        return PORT
    candidates = sorted(
        p.device for p in serial.tools.list_ports.comports()
        if p.vid == ADAFRUIT_VID
    )
    # console port enumerates first, data second
    return candidates[1] if len(candidates) >= 2 else None


def local_epoch():
    # Pico does no timezone math: send epoch pre-shifted to local
    return int(time.time() - time.timezone + time.daylight * 3600)


def walk(node, out):
    # flatten LHM's tree: SensorId -> (display name, value string)
    for child in node.get("Children", []):
        walk(child, out)
    if "SensorId" in node:
        out[node["SensorId"]] = (node.get("Text", ""), node.get("Value", ""))


def parse_value(text):
    # LHM values are display strings: "45.0 °C", "88.2 W", "63.4 %"
    try:
        return int(float(text.split()[0].replace(",", ".")))
    except (ValueError, IndexError, AttributeError):
        return -1


def read_stats():
    # returns dict or None if LHM unreachable
    try:
        tree = requests.get(LHM_URL, timeout=1).json()
    except (requests.RequestException, ValueError):
        return None
    sensors = {}
    walk(tree, sensors)

    def find(prefix, kind, name):
        for sid, (text, val) in sensors.items():
            if sid.startswith(prefix) and kind in sid and text == name:
                return parse_value(val)
        return -1

    return {
        "cpu": (find("/intelcpu/0", "/load/", "CPU Total"),
                find("/intelcpu/0", "/temperature/", "CPU Package"),
                find("/intelcpu/0", "/power/", "CPU Package")),
        "gpu": (find("/gpu-nvidia/0", "/load/", "GPU Core"),
                find("/gpu-nvidia/0", "/temperature/", "GPU Core"),
                find("/gpu-nvidia/0", "/power/", "GPU Package")),
    }


while True:
    port = find_port()
    if port is None:
        print("no Pico data port found, retrying")
        time.sleep(5)
        continue
    try:
        with serial.Serial(port, 115200, timeout=1) as s:
            print("connected:", port)
            last_time = 0.0
            while True:
                now = time.monotonic()
                if now - last_time >= TIME_INTERVAL_S or last_time == 0:
                    s.write(f"time:{local_epoch()}\n".encode())
                    last_time = now
                stats = read_stats()
                if stats:
                    for key, (util, temp, pwr) in stats.items():
                        s.write(f"{key}:{util},{temp},{pwr}\n".encode())
                time.sleep(STATS_INTERVAL_S)
    except (serial.SerialException, OSError):
        print("disconnected, retrying")
        time.sleep(5)