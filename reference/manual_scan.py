# ------------------------------------------------------------------
# Manual Scan.py - manual matrix scanning reference
# NOT the production firmware (see code.py, which uses
# keypad.KeyMatrix). Kept as a hand-written implementation of what
# KeyMatrix does internally: scan sequencing, settle timing,
# per-key debounce state machine, edge extraction.
#
# Hardware: Raspberry Pi Pico W (RP2040), CircuitPython 9.2.1
#   Hand-wired 5x4 matrix, one diode per key
#   (anode toward row, cathode toward column)
#   Rows GP2-GP6 (outputs), Cols GP12-GP15 (inputs, pull-down),
#   LED GP11
#
# Known limitations vs code.py (inherent to polled scanning):
#   - matrix is only observed while scan() runs; a busy loop
#     misses input
#   - debounce sampling period jitters with loop timing
#   - no event history: press+release between scans is lost
# ------------------------------------------------------------------

import time
import board
import digitalio
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

NUM_ROWS = 5
NUM_COLS = 4
DEBOUNCE_MS = 10    # raw state must hold this long to commit
SETTLE_S = 0.0002   # column line charge time after row drive

KEYMAP = {
    (0, 0): Keycode.KEYPAD_NUMLOCK,
    (0, 1): Keycode.KEYPAD_FORWARD_SLASH,
    (0, 2): Keycode.KEYPAD_ASTERISK,
    (0, 3): Keycode.KEYPAD_MINUS,
    (1, 0): Keycode.KEYPAD_SEVEN,
    (1, 1): Keycode.KEYPAD_EIGHT,
    (1, 2): Keycode.KEYPAD_NINE,
    (1, 3): Keycode.KEYPAD_PLUS,
    (2, 0): Keycode.KEYPAD_FOUR,
    (2, 1): Keycode.KEYPAD_FIVE,
    (2, 2): Keycode.KEYPAD_SIX,
    (3, 0): Keycode.KEYPAD_ONE,
    (3, 1): Keycode.KEYPAD_TWO,
    (3, 2): Keycode.KEYPAD_THREE,
    (4, 0): Keycode.KEYPAD_ZERO,
    (4, 2): Keycode.KEYPAD_PERIOD,
    (4, 3): Keycode.KEYPAD_ENTER,
}

# --- pin setup ---
rows = []
for pin in (board.GP2, board.GP3, board.GP4, board.GP5, board.GP6):
    d = digitalio.DigitalInOut(pin)
    d.direction = digitalio.Direction.OUTPUT
    d.value = False
    rows.append(d)

cols = []
for pin in (board.GP12, board.GP13, board.GP14, board.GP15):
    d = digitalio.DigitalInOut(pin)
    d.direction = digitalio.Direction.INPUT
    d.pull = digitalio.Pull.DOWN
    cols.append(d)

led = digitalio.DigitalInOut(board.GP11)
led.direction = digitalio.Direction.OUTPUT
led.value = True

keyboard = Keyboard(usb_hid.devices)

# per-key debounce state, flat index = row * NUM_COLS + col
NUM_KEYS = NUM_ROWS * NUM_COLS
stable = [False] * NUM_KEYS       # accepted state (what we act on)
raw_prev = [False] * NUM_KEYS     # last raw reading
last_change = [0] * NUM_KEYS      # ms timestamp of last raw flip


def now_ms():
    # monotonic_ns + integer math: monotonic() is a float that loses ms resolution as uptime grows
    return time.monotonic_ns() // 1_000_000


def scan():
    """One full matrix pass. Emits HID press/release on debounced edges."""
    t = now_ms()
    for r in range(NUM_ROWS):
        rows[r].value = True
        time.sleep(SETTLE_S)
        for c in range(NUM_COLS):
            k = r * NUM_COLS + c
            raw = cols[c].value

            if raw != raw_prev[k]:
                # raw signal moved: restart the stability window. bounce keeps landing here, so it never commits
                raw_prev[k] = raw
                last_change[k] = t
            elif raw != stable[k] and (t - last_change[k]) >= DEBOUNCE_MS:
                # stable long enough and differs from accepted state: commit and emit the edge
                stable[k] = raw
                keycode = KEYMAP.get((r, c))
                if keycode is not None:
                    if raw:
                        keyboard.press(keycode)
                    else:
                        keyboard.release(keycode)
        rows[r].value = False


while True:
    scan()  # never add blocking work here, see header
