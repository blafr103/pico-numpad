# ------------------------------------------------------------------
# companion.py - host-side broadcaster for pico-numpad
#
# Sends over the Pico's data serial port (auto-detected):
#   time:<epoch>                      on connect + every 30 s
#   cpu:<util%>,<tempC>,<powerW>      every STATS_INTERVAL_S
#   gpu:<util%>,<tempC>,<powerW>      every STATS_INTERVAL_S
#   wx<i>:<LABEL>,<COND>,<TEMP>,<OFFMIN>   every WX_INTERVAL_S
#     LABEL <=4 chars, COND <=6 chars, TEMP int C, OFFMIN =
#     location's UTC offset minus host's, minutes (DST-correct,
#     computed fresh via zoneinfo each broadcast)
# Missing values sent as -1 (stats) or omitted line (weather).
#
# Sources:
#   stats  - LibreHardwareMonitor web server (localhost:8085),
#            must be running as admin. Down -> stats skipped.
#   weather- Environment Canada via the env_canada package
#            (nearest site to each coordinate). Down -> wx skipped.
#
# Deps: pip install pyserial requests env_canada
# Auto-start: Task Scheduler -> pythonw.exe <path>\companion.py
# ------------------------------------------------------------------

import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import serial
import serial.tools.list_ports
import requests
from env_canada import ECWeather

PORT = None
TIME_INTERVAL_S = 30
STATS_INTERVAL_S = 2
WX_INTERVAL_S = 600
LHM_URL = "http://localhost:8085/data.json"
ADAFRUIT_VID = 0x239A
HOME_TZ = ZoneInfo("America/Toronto")

# label (<=4), (lat, lon), IANA timezone
LOCATIONS = [
    ("OTT",  (45.42, -75.70),  "America/Toronto"),
    ("MTL",  (45.51, -73.57),  "America/Toronto"),
    ("VAN",  (49.28, -123.12), "America/Vancouver"),
    ("PEMB", (45.83, -77.11),  "America/Toronto"),
]

# EC condition string -> <=6 char display form; unknown -> truncate
COND_MAP = {
    "sunny": "Sunny", "clear": "Clear",
    "mainly sunny": "Sunny", "mainly clear": "Clear",
    "partly cloudy": "PtCldy", "mostly cloudy": "Cloudy",
    "cloudy": "Cloudy", "overcast": "Cloudy",
    "rain": "Rain", "heavy rain": "Rain",
    "light rain": "Rain", "light rainshower": "Shower",
    "rainshower": "Shower", "showers": "Shower", "drizzle": "Drizzl",
    "snow": "Snow", "light snow": "Snow", "flurries": "Flurry",
    "light snowshower": "Flurry", "thunderstorm": "Storm",
    "thunderstorms": "Storm", "fog": "Fog", "mist": "Mist",
    "haze": "Haze",
}

wx_stations = [ECWeather(coordinates=coord) for _, coord, _ in LOCATIONS]


def find_port():
    if PORT:
        return PORT
    candidates = sorted(
        p.device for p in serial.tools.list_ports.comports()
        if p.vid == ADAFRUIT_VID
    )
    return candidates[1] if len(candidates) >= 2 else None


def local_epoch():
    return int(time.time() - time.timezone + time.daylight * 3600)


def cond_short(raw):
    if not raw:
        return "--"
    return COND_MAP.get(raw.strip().lower(), raw.strip()[:6])


def tz_offset_min(tzname):
    # location offset minus home offset, right now (DST-correct)
    now = datetime.now()
    loc = ZoneInfo(tzname).utcoffset(now.astimezone(ZoneInfo(tzname)))
    home = HOME_TZ.utcoffset(now.astimezone(HOME_TZ))
    return int((loc - home).total_seconds() // 60)


def read_weather():
    # returns list of (index, label, cond, temp, offset) or []
    out = []
    for i, ((label, _, tzname), st) in enumerate(zip(LOCATIONS, wx_stations)):
        try:
            asyncio.run(st.update())
            temp = st.conditions.get("temperature", {}).get("value")
            cond = st.conditions.get("condition", {}).get("value")
            if temp is None:
                continue
            out.append((i, label, cond_short(cond), round(temp),
                        tz_offset_min(tzname)))
        except Exception as e:
            print("wx", label, "failed:", e)
    return out


def walk(node, out):
    for child in node.get("Children", []):
        walk(child, out)
    if "SensorId" in node:
        out[node["SensorId"]] = (node.get("Text", ""), node.get("Value", ""))


def parse_value(text):
    try:
        return int(float(text.split()[0].replace(",", ".")))
    except (ValueError, IndexError, AttributeError):
        return -1


def read_stats():
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
            last_wx = 0.0
            while True:
                now = time.monotonic()
                if now - last_time >= TIME_INTERVAL_S or last_time == 0:
                    s.write(f"time:{local_epoch()}\n".encode())
                    last_time = now
                if now - last_wx >= WX_INTERVAL_S or last_wx == 0:
                    for i, label, cond, temp, off in read_weather():
                        s.write(f"wx{i}:{label},{cond},{temp},{off}\n"
                                .encode())
                    last_wx = now
                stats = read_stats()
                if stats:
                    for key, (util, temp, pwr) in stats.items():
                        s.write(f"{key}:{util},{temp},{pwr}\n".encode())
                time.sleep(STATS_INTERVAL_S)
    except (serial.SerialException, OSError):
        print("disconnected, retrying")
        time.sleep(5)
