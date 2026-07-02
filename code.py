# ------------------------------------------------------------------
# Production Version — 17-key USB numpad + LCD (Phase 2)
# Hardware: Raspberry Pi Pico W (RP2040), CircuitPython 9.2.1
#   Hand-wired 5x4 matrix, one diode per key
#   (anode toward row, cathode toward column)
#   Rows GP2-GP6, Cols GP12-GP15, LED GP11
#   16x2 LCD on PCF8574 I2C backpack @ 0x27, SCL GP1, SDA GP0
#
# Phase 2 adds:
#   - NumLock as momentary function key (Fn): hold NumLock, tap a
#     digit to switch views. Plain NumLock tap still sends NumLock
#     (deferred to release; digits used with Fn are consumed, and
#     their release edges suppressed).
#       Fn+0: history view    Fn+1: stats view (lifetime presses)
#   - Lifetime keystroke counter persisted to /count.txt.
#     Requires boot.py (storage remount; hold NumLock at plug-in
#     for dev mode - host-writable drive, persistence off).
#     Writes batched: flush every FLUSH_EVERY presses and on idle
#     transition - flash erase cycles are finite and writes cost ms.
#
# Display design (Phase 1, unchanged):
#   - No sleep() in the loop; timing via monotonic_ns timestamps.
#   - Dirty-flag rendering: I2C writes only on state change
#     (a full line write costs ~50 ms).
#   - Fixed-width line overwrites, no clear().
#   - Backlight edge-triggered; off after 60 s idle, wake also types.
# ------------------------------------------------------------------

import time
import board
import busio
import digitalio
import keypad
#import storage
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

from lcd import LCD
from i2c_pcf8574_interface import I2CPCF8574Interface

IDLE_TIMEOUT_MS = 60_000
LCD_COLS = 16
COUNT_FILE = "/count.txt"
FLUSH_EVERY = 100

FN_KEY = 0            # key_number of NumLock
VIEW_HISTORY = 0
VIEW_STATS = 1

# Matrix position (HID keycode, display label, Fn view or None)
# Unwired matrix positions are intentionally omitted.
KEYS = {
    0:  (Keycode.KEYPAD_NUMLOCK, None),
    1:  (Keycode.KEYPAD_FORWARD_SLASH, None),
    2:  (Keycode.KEYPAD_ASTERISK, None),
    3:  (Keycode.KEYPAD_MINUS, None),
    4:  (Keycode.KEYPAD_SEVEN, None),
    5:  (Keycode.KEYPAD_EIGHT, None),
    6:  (Keycode.KEYPAD_NINE, None),
    7:  (Keycode.KEYPAD_PLUS, None),
    8:  (Keycode.KEYPAD_FOUR, None),
    9:  (Keycode.KEYPAD_FIVE, None),
    10: (Keycode.KEYPAD_SIX, None),
    12: (Keycode.KEYPAD_ONE, VIEW_STATS),
    13: (Keycode.KEYPAD_TWO, None),
    14: (Keycode.KEYPAD_THREE, None),
    16: (Keycode.KEYPAD_ZERO, VIEW_HISTORY),
    18: (Keycode.KEYPAD_PERIOD, None),
    19: (Keycode.KEYPAD_ENTER, None),
}

# initialize key matrix scanner
matrix = keypad.KeyMatrix(
    row_pins=(board.GP2, board.GP3, board.GP4, board.GP5, board.GP6),
    column_pins=(board.GP12, board.GP13, board.GP14, board.GP15),
    columns_to_anodes=False,  # REQUIRED: diodes conduct row to column
    interval=0.010,
)

keyboard = Keyboard(usb_hid.devices)

led = digitalio.DigitalInOut(board.GP11)
led.direction = digitalio.Direction.OUTPUT
led.value = True

i2c = busio.I2C(scl=board.GP1, sda=board.GP0)
lcd = LCD(I2CPCF8574Interface(i2c, 0x27), num_rows=2, num_cols=LCD_COLS)

# load persistent lifetime press counter
def load_count():
    try:
        with open(COUNT_FILE) as f:
            return int(f.read())
    except (OSError, ValueError) as e:
        return 0        # missing or invalid file

def save_count():
    global unsaved
    try:
        with open(COUNT_FILE, "w") as f:
            f.write(str(press_count))
        unsaved = 0
    except OSError:
        pass            # ignore writes when storage is read-only (dev mode)

press_count = load_count()
unsaved = 0             # Presses since last save

# Fn (Function) state
fn_down = False         # NumLock physically held
fn_used = False         # a digit was consumed during this hold
consumed = set()        # keys whose release event should be ignored
view = VIEW_HISTORY
view_dirty = True

# LCD display state
backlight_on = True

# current time in milliseconds
def now_ms():
    return time.monotonic_ns() // 1_000_000

# overwrite an entire LCD row to avoid clearing the display
def draw_line(row, text):
    lcd.set_cursor_pos(row, 0)
    lcd.print((text + " " * LCD_COLS)[:LCD_COLS])

# draw the active screen
def render():
    if view == VIEW_HISTORY:
        draw_line(0, "pico-numpad")
        draw_line(1, "")
    elif view == VIEW_STATS:
        draw_line(0, "Presses:")
        draw_line(1, str(press_count))

# initial LCD state
lcd.clear()
lcd.set_backlight(True)

last_activity = now_ms()

while True:
    t = now_ms()

    # process every queued key event before updating the display
    event = matrix.events.get()
    while event:
        key = KEYS.get(event.key_number)
        if key is not None:
            keycode, fn_action = key

            if event.pressed:
                press_count += 1
                unsaved += 1
                # decide tap vs. Fn when NumLock is released
                if event.key_number == FN_KEY:
                    fn_down = True
                    fn_used = False
                # Fn shortcut: switch view without sending a key
                elif fn_down and fn_action is not None:
                    fn_used = True
                    consumed.add(event.key_number)
                    if view != fn_action:
                        view = fn_action
                        view_dirty = True
                else:
                    keyboard.press(keycode)
                if view == VIEW_STATS:
                    view_dirty = True      # count changed on-screen
            # key released
            else:
                if event.key_number == FN_KEY:
                    fn_down = False
                    # NumLock was tapped
                    if not fn_used:
                        keyboard.press(keycode)
                        keyboard.release(keycode)
                elif event.key_number in consumed:
                    consumed.discard(event.key_number)  # ignore release
                else:
                    keyboard.release(keycode)

        last_activity = t
        event = matrix.events.get()

    # save periodically to reduce flash writes
    if unsaved >= FLUSH_EVERY:
        save_count()

    # handle idle backlight and save before sleeping
    idle = (t - last_activity) >= IDLE_TIMEOUT_MS
    if idle and backlight_on:
        lcd.set_backlight(False)
        led.value = False
        backlight_on = False
        if unsaved:
            save_count()
    elif not idle and not backlight_on:
        lcd.set_backlight(True)
        led.value = True
        backlight_on = True

    if view_dirty:
        render()
        view_dirty = False
