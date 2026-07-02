# ------------------------------------------------------------------
# timesync.py — host-side broadcaster for pico-numpad (Phase 6a)
#
# Sends "time:<epoch>\n" (epoch shifted to LOCAL time) to the
# Pico's data serial port: on connect and every INTERVAL_S after.
# Reconnects automatically if the port disappears (unplug/replug).
#
# The Pico exposes TWO serial ports: console (REPL) and data.
# Auto-detect matches Adafruit's USB VID and takes the second
# port; if that picks wrong, set PORT manually (e.g. "COM7").
#
# Usage: python timesync.py        (pip install pyserial)
# ------------------------------------------------------------------

import time
import serial
import serial.tools.list_ports

PORT = None          # e.g. "COM7" to override auto-detect
INTERVAL_S = 30
ADAFRUIT_VID = 0x239A   # CircuitPython's USB vendor ID


def find_port():
    if PORT:
        return PORT
    candidates = sorted(
        p.device for p in serial.tools.list_ports.comports()
        if p.vid == ADAFRUIT_VID
    )
    # console enumerates first, data second
    return candidates[1] if len(candidates) >= 2 else None


def local_epoch():
    # Pico does no timezone math: send epoch pre-shifted to local
    return int(time.time() - time.timezone + time.daylight * 3600)


while True:
    port = find_port()
    if port is None:
        print("no Pico data port found, retrying")
        time.sleep(5)
        continue
    try:
        with serial.Serial(port, 115200, timeout=1) as s:
            print("connected:", port)
            while True:
                s.write(f"time:{local_epoch()}\n".encode())
                time.sleep(INTERVAL_S)
    except (serial.SerialException, OSError):
        print("disconnected, retrying")
        time.sleep(5)