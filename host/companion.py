# ------------------------------------------------------------------
# companion.py - host-side broadcaster for pico-numpad
#
# Sends over the Pico's data serial port (auto-detected):
#   time:<epoch>                     on connect + every 30 s
#   p{0-3}{a,b}:<16-char line>       every STATS_INTERVAL_S
#     pre-formatted PC stat page rows; '*' renders as a degree
#     symbol on the Pico. Pages: 0 overview, 1 memory, 2 clocks,
#     3 voltages. Missing sensors render as '--'.
#   wx<i>:<LABEL>,<COND>,<TEMP>,<OFFMIN>  every WX_INTERVAL_S
#
# Sources:
#   stats  - LibreHardwareMonitor web server (localhost:8085),
#            running as admin. Down -> stat lines say 'LHM down'.
#   weather- Environment Canada via env_canada. Down -> wx skipped.
#
# Sensor matching: hardware prefix + path kind + display name
# (numeric indices shift between LHM versions). Names target this
# PC (i7-13700K, RTX 4080, 2x Corsair DDR5); adjust for others.
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

LOCATIONS = [
    ("OTT",  (45.42, -75.70),  "America/Toronto"),
    ("MTL",  (45.51, -73.57),  "America/Toronto"),
    ("VAN",  (49.28, -123.12), "America/Vancouver"),
    ("PEMB", (45.83, -77.11),  "America/Toronto"),
]

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
    now = datetime.now()
    loc = ZoneInfo(tzname).utcoffset(now.astimezone(ZoneInfo(tzname)))
    home = HOME_TZ.utcoffset(now.astimezone(HOME_TZ))
    return int((loc - home).total_seconds() // 60)


def read_weather():
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


# ---------------- LHM stats -> formatted page lines ----------------

def walk(node, out):
    for child in node.get("Children", []):
        walk(child, out)
    if "SensorId" in node:
        out[node["SensorId"]] = (node.get("Text", ""), node.get("Value", ""))


def parse_float(text):
    try:
        return float(text.split()[0].replace(",", "."))
    except (ValueError, IndexError, AttributeError):
        return None


def stat_pages():
    """Return dict message-key -> 16-char display line for pages 0-3.
    '*' stands for the degree symbol (Pico substitutes at draw)."""
    try:
        tree = requests.get(LHM_URL, timeout=1).json()
    except (requests.RequestException, ValueError):
        return {f"p{p}{r}": "LHM down" if r == "a" else ""
                for p in range(4) for r in "ab"}

    sensors = {}
    walk(tree, sensors)

    def find(prefix, kind, name):
        for sid, (text, val) in sensors.items():
            if sid.startswith(prefix) and kind in sid and text == name:
                return parse_float(val)
        return None

    def find_max(prefix, kind, name_prefix):
        vals = [parse_float(v) for sid, (t, v) in sensors.items()
                if sid.startswith(prefix) and kind in sid
                and t.startswith(name_prefix)]
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None

    def find_min(prefix, kind, name_suffix):
        vals = [parse_float(v) for sid, (t, v) in sensors.items()
                if sid.startswith(prefix) and kind in sid
                and t.endswith(name_suffix)]
        vals = [v for v in vals if v is not None]
        return min(vals) if vals else None

    def i(v):          # int display or --
        return "--" if v is None else str(round(v))

    def v3(v):         # 3-decimal voltage or --
        return "--" if v is None else "{:.3f}".format(v)

    cpu_util = find("/intelcpu/0", "/load/", "CPU Total")
    cpu_temp = find("/intelcpu/0", "/temperature/", "CPU Package")
    cpu_pwr  = find("/intelcpu/0", "/power/", "CPU Package")
    gpu_util = find("/gpu-nvidia/0", "/load/", "GPU Core")
    gpu_temp = find("/gpu-nvidia/0", "/temperature/", "GPU Core")
    gpu_pwr  = find("/gpu-nvidia/0", "/power/", "GPU Package")

    ram_util  = find("/ram", "/load/", "Memory")
    dimm1     = find("/memory/dimm/1", "/temperature/", "DIMM #1")
    dimm3     = find("/memory/dimm/3", "/temperature/", "DIMM #3")
    vram_used = find("/gpu-nvidia/0", "/smalldata/", "GPU Memory Used")
    vram_tot  = find("/gpu-nvidia/0", "/smalldata/", "GPU Memory Total")

    pclk = find_max("/intelcpu/0", "/clock/", "P-Core")
    eclk = find_max("/intelcpu/0", "/clock/", "E-Core")
    gclk = find("/gpu-nvidia/0", "/clock/", "GPU Core")
    tjd  = find_min("/intelcpu/0", "/temperature/", "Distance to TjMax")

    vcore = find("/intelcpu/0", "/voltage/", "CPU Core")
    vgpu  = find("/gpu-nvidia/0", "/voltage/", "GPU Core Voltage")

    return {
        "p0a": "C {}% {}* {}W".format(i(cpu_util), i(cpu_temp), i(cpu_pwr)),	# page 0 row a: CPU util/temp/power
        "p0b": "G {}% {}* {}W".format(i(gpu_util), i(gpu_temp), i(gpu_pwr)),	# page 0 row b: GPU
        "p1a": "R {}% {}* {}*".format(i(ram_util), i(dimm1), i(dimm3)),			# page 1: RAM util + DIMM temps
        "p1b": "V {}/{}MB".format(i(vram_used), i(vram_tot)),					# VRAM used/total
        "p2a": "C {} E{} MHz".format(i(pclk), i(eclk)),							# page 2: CPU P/E max clock
        "p2b": "G {}MHz Tj-{}".format(i(gclk), i(tjd)),							# GPU clock + TjMax dist
        "p3a": "Vc {}  Vg {}".format(v3(vcore), v3(vgpu)),						# page 3: core voltages
        "p3b": "CPU {}W GPU {}W".format(i(cpu_pwr), i(gpu_pwr)),				# CPU/GPU package powers
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
                    for i_, label, cond, temp, off in read_weather():
                        s.write(f"wx{i_}:{label},{cond},{temp},{off}\n"
                                .encode())
                    last_wx = now
                for key, line in stat_pages().items():
                    s.write(f"{key}:{line}\n".encode())
                time.sleep(STATS_INTERVAL_S)
    except (serial.SerialException, OSError):
        print("disconnected, retrying")
        time.sleep(5)