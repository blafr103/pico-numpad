# ------------------------------------------------------------------
# Production Version - 17-key USB numpad firmware + LCD display manager (Phase 1)
# Hardware: Raspberry Pi Pico W (RP2040), CircuitPython 9.2.1
#   Hand-wired 5x4 matrix, one diode per key
#   (anode toward row, cathode toward column)
#   Rows GP2-GP6, Cols GP12-GP15, LED GP11
#   16x2 LCD on PCF8574 I2C backpack @ 0x27, SCL GP1, SDA GP0
#
# Display design (the point of this phase):
#   - No sleep() anywhere in the loop; all timing via monotonic_ns
#     timestamps compared each pass.
#   - Dirty-flag rendering: I2C writes happen ONLY when display
#     state has changed. An LCD line write costs tens of ms over
#     I2C; unconditional redraws would starve input latency (the
#     original firmware's failure mode).
#   - Backlight is edge-triggered: set_backlight() is called only
#     on state transitions, never repeatedly.
#   - Line 0: static title (written once). Line 1: last 16 keys.
#   - Backlight off after 60 s idle; waking press also types.
# ------------------------------------------------------------------

import time
import board
import busio
import digitalio
import keypad
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

from lcd import LCD
from i2c_pcf8574_interface import I2CPCF8574Interface

IDLE_TIMEOUT_MS = 60_000
HISTORY_LEN = 16                     # one full LCD line

# key_number = row * 4 + col  -> (HID keycode, history label); dict so unwired positions are absent and unmapped events fall through as a no-op via .get()
KEYS = {
    0:  (Keycode.KEYPAD_NUMLOCK,       "N"),    # (0,0)
    1:  (Keycode.KEYPAD_FORWARD_SLASH, "/"),    # (0,1)
    2:  (Keycode.KEYPAD_ASTERISK,      "*"),    # (0,2)
    3:  (Keycode.KEYPAD_MINUS,         "-"),    # (0,3)
    4:  (Keycode.KEYPAD_SEVEN,         "7"),    # (1,0)
    5:  (Keycode.KEYPAD_EIGHT,         "8"),    # (1,1)
    6:  (Keycode.KEYPAD_NINE,          "9"),    # (1,2)
    7:  (Keycode.KEYPAD_PLUS,          "+"),    # (1,3)
    8:  (Keycode.KEYPAD_FOUR,          "4"),    # (2,0)
    9:  (Keycode.KEYPAD_FIVE,          "5"),    # (2,1)
    10: (Keycode.KEYPAD_SIX,           "6"),    # (2,2)
    12: (Keycode.KEYPAD_ONE,           "1"),    # (3,0)
    13: (Keycode.KEYPAD_TWO,           "2"),    # (3,1)
    14: (Keycode.KEYPAD_THREE,         "3"),    # (3,2)
    16: (Keycode.KEYPAD_ZERO,          "0"),    # (4,0)
    18: (Keycode.KEYPAD_PERIOD,        "."),    # (4,2)
    19: (Keycode.KEYPAD_ENTER,         "E"),    # (4,3)
}

# hardware init
matrix = keypad.KeyMatrix(
    row_pins=(board.GP2, board.GP3, board.GP4, board.GP5, board.GP6),
    column_pins=(board.GP12, board.GP13, board.GP14, board.GP15),
    columns_to_anodes=False,  # REQUIRED: diodes conduct row -> column; default True drives the blocked direction (all keys dead)
    interval=0.010,           # scan period = debounce window
)

keyboard = Keyboard(usb_hid.devices)

led = digitalio.DigitalInOut(board.GP11)
led.direction = digitalio.Direction.OUTPUT
led.value = True


i2c = busio.I2C(scl=board.GP1, sda=board.GP0)
lcd = LCD(I2CPCF8574Interface(i2c, 0x27), num_rows=2, num_cols=16)



#  display state
history = ""              # last HISTORY_LEN key labels
history_dirty = True      # true whenever line 1 needs a rewrite
backlight_on = True       # belief of current backlight state


def now_ms():
    return time.monotonic_ns() // 1_000_000


def draw_history():
    # rewrite only line 1, padded to full width so shorter content
    # overwrites leftovers without a clear() (clear() blanks the
    # whole screen including the static title, forcing more writes)
    lcd.set_cursor_pos(1, 0)
    lcd.print(" " * (HISTORY_LEN - len(history)) + history)


# one-time draw
lcd.clear()
lcd.set_backlight(True)
lcd.set_cursor_pos(0, 0)
lcd.print("pico-numpad")

last_activity = now_ms()


# Test to count LCD rows
#for r in range(4):
#    lcd.set_cursor_pos(r, 0)
#    lcd.print("ROW" + str(r))

# Test to count LCD columns
#lcd.set_cursor_pos(0, 0)
#lcd.print("0123456789ABCDEFGHIJ")

while True:
    t = now_ms()

    # drain the whole event queue each pass, not just one event:
    # rendering below is per-pass, so a burst of queued events
    # costs one LCD write, not one per event
    event = matrix.events.get()
    while event:
        key = KEYS.get(event.key_number)
        if key is not None:
            keycode, label = key
            if event.pressed:
                keyboard.press(keycode)
                history = (history + label)[-HISTORY_LEN:]
                history_dirty = True
            else:
                keyboard.release(keycode)
        last_activity = t          # any edge counts as activity
        event = matrix.events.get()

    # backlight timeout - edge-triggered in both directions
    idle = (t - last_activity) >= IDLE_TIMEOUT_MS
    if idle and backlight_on:
        lcd.set_backlight(False)
        led.value = False
        backlight_on = False
    elif not idle and not backlight_on:
        lcd.set_backlight(True)
        led.value = True
        backlight_on = True

    # render - the only unconditional-looking call, guarded by flag
    if history_dirty:
        draw_history()
        history_dirty = False
