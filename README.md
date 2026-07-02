# pico-numpad

Hand-wired 17-key USB numpad on a Raspberry Pi Pico W. CircuitPython,
diode-per-key 5x4 matrix, event-driven scanning via `keypad.KeyMatrix`.

## Features

- USB HID numeric keypad
- Event-driven matrix scanning (`keypad.KeyMatrix`)
- 16x2 LCD with switchable views:
  - Clock (date + time, synced from the host over USB serial)
  - Lifetime keypress statistics
- NumLock doubles as a momentary Fn key:
  - Fn+0: Clock view
  - Fn+1: Statistics view
- Persistent lifetime key counter stored in onboard flash
- Automatic LCD backlight/LED timeout after 60 seconds of inactivity
- Optional host companion script feeding local time (extensible
  key:value serial protocol; PC stats planned)

## Hardware

- Raspberry Pi Pico W (RP2040), CircuitPython 9.2.1
- 5x4 hand-wired matrix, one diode per key (anode toward row,
  cathode toward column, verified with `tools/diode_test.py`)
- Rows: GP2–GP6 · Columns: GP12–GP15 · Status LED: GP11
- Unwired matrix positions: (2,3), (3,3), (4,1)
- 16x2 character LCD on a PCF8574 I²C backpack, address 0x27
  (SCL GP1, SDA GP0)

## Install

1. Flash CircuitPython 9.x to the Pico.
2. Copy `code.py`, `boot.py`, and `lib/` to the CIRCUITPY drive,
   then power cycle (`boot.py` changes only apply at power-up).
   - On first boot, the firmware creates `/count.txt` to store the
     lifetime keypress count.
3. Install `adafruit_hid` from the
   [CircuitPython 9.x library bundle](https://circuitpython.org/libraries)
   into `lib/`.
4. Optional, for the clock: `pip install pyserial` on the PC and run
   `python host/timesync.py`. The pad is fully functional without it;
   the clock shows UNKNOWN until first sync.
	- The Pico exposes two serial ports; the script auto-detects 
	the data port, or set PORT in the script manually.

## Design

Matrix scanning and debouncing run in C in the background
(`keypad.KeyMatrix`, 10 ms interval); the main loop only consumes
queued edge events, so a busy loop delays input rather than dropping
it. HID output uses `press()`/`release()` on edges so the host sees
real held keys and handles auto-repeat. A hand-written equivalent of
the scanner (polling, per-key debounce state machine) is kept in
[`reference/`](reference/) with a comparison of the two approaches.

`columns_to_anodes=False` is required for this board's diode
orientation; the default silently reads all keys as dead.

`boot.py` remounts the filesystem to allow the firmware to save the
persistent key counter. Holding **NumLock** while connecting the Pico
starts a development mode that leaves the drive writable from the host
and disables persistence.

The LCD is driven with no `sleep()` in the main loop: dirty-flag
rendering (I²C writes only on state change), fixed-width line
overwrites instead of `clear()`, and edge-triggered backlight
control. A full LCD line write costs ~50 ms over I²C, so
unconditional redraws would starve input latency.

The LCD is organized as switchable views (splash on boot, clock,
statistics). Holding **NumLock** acts as a momentary Fn key: tapping
a digit while held switches views without typing it. A plain NumLock
tap is deferred until release so tap and hold can be distinguished
without affecting normal typing.

Time comes from an optional host script (`host/timesync.py`) over a
second USB CDC serial port enabled in `boot.py`. The protocol is
newline-terminated ASCII `key:value` lines; the Pico is a pure
listener and the host broadcasts unsolicited (on connect and every
30 s). The clock free-runs on the crystal between syncs and re-anchors
on every message, bounding drift to one broadcast interval. The RP2040
has no battery-backed RTC, so time is lost at power-off and shows
UNKNOWN until the first sync.

The lifetime keypress counter is stored in `/count.txt`. To minimize
flash wear, writes are batched (100 keypresses by default) and flushed
when the device transitions to the idle state.

## Tools

- `tools/matrix_map_test.py` prints (row, col) per keypress; used
  to build/verify the keymap
- `tools/diode_test.py` is a bidirectional scan; verifies diode
  presence and orientation per key

Run either by copying to CIRCUITPY as `code.py` (back up first) and
watching the serial console. Run in dev mode (NumLock at plug-in) 
so the drive is writable

## Credits

LCD driver (`lib/lcd.py`, `lib/i2c_pcf8574_interface.py`):
[dwhall/circuitpython_lcd](https://github.com/dwhall/circuitpython_lcd),
original license headers retained.