# pico-numpad

Hand-wired 17-key USB numpad on a Raspberry Pi Pico W. CircuitPython,
diode-per-key 5x4 matrix, event-driven scanning via `keypad.KeyMatrix`.

## Hardware

- Raspberry Pi Pico W (RP2040), CircuitPython 9.2.1
- 5x4 hand-wired matrix, one diode per key (anode toward row,
  cathode toward column — verified with `tools/diode_test.py`)
- Rows: GP2–GP6 · Columns: GP12–GP15 · Status LED: GP11
- Unwired matrix positions: (2,3), (3,3), (4,1)
- 20x3 character LCD on a PCF8574 I²C backpack, address 0x27
  (SCL GP1, SDA GP0) — hardware present, firmware integration pending

## Install

1. Flash CircuitPython 9.x to the Pico.
2. Copy `code.py` and `lib/` to the CIRCUITPY drive.
3. Install `adafruit_hid` from the
   [CircuitPython 9.x library bundle](https://circuitpython.org/libraries)
   into `lib/`.

## Design

Matrix scanning and debouncing run in C in the background
(`keypad.KeyMatrix`, 10 ms interval); the main loop only consumes
queued edge events, so a busy loop delays input rather than dropping
it. HID output uses `press()`/`release()` on edges so the host sees
real held keys and handles auto-repeat. A hand-written equivalent of
the scanner (polling, per-key debounce state machine) is kept in
[`reference/`](reference/) with a comparison of the two approaches.

`columns_to_anodes=False` is required for this board's diode
orientation — the default silently reads all keys as dead.

## Tools

- `tools/matrix_map_test.py` — prints (row, col) per keypress; used
  to build/verify the keymap
- `tools/diode_test.py` — bidirectional scan; verifies diode
  presence and orientation per key

Run either by copying to CIRCUITPY as `code.py` (back up first) and
watching the serial console.

## Credits

LCD driver (`lib/lcd.py`, `lib/i2c_pcf8574_interface.py`):
[dwhall/circuitpython_lcd](https://github.com/dwhall/circuitpython_lcd),
original license headers retained.