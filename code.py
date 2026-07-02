# ------------------------------------------------------------------
# Production Version - 17-key USB numpad firmware
# Hardware: Raspberry Pi Pico W (RP2040), CircuitPython 9.2.1
#   Hand-wired 5x4 matrix, one diode per key
#   (anode toward row, cathode toward column)
#   Rows GP2-GP6, Cols GP12-GP15, LED GP11
#   Unwired positions: (2,3), (3,3), (4,1) - absent from KEYMAP
#
# Design:
#   - keypad.KeyMatrix scans in the background (C, fixed 10 ms
#     timer) and queues debounced edge events; a stalled main loop
#     delays input, never drops it.
#   - press()/release() on edges (never send()) so the host sees
#     real held keys and provides auto-repeat.
# ------------------------------------------------------------------

import board
import digitalio
import keypad
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

# key_number = row * 4 + col; dict so unwired positions are absent and unmapped events fall through as a no-op via .get()
KEYMAP = {
    0:  Keycode.KEYPAD_NUMLOCK,        # (0,0)
    1:  Keycode.KEYPAD_FORWARD_SLASH,  # (0,1)
    2:  Keycode.KEYPAD_ASTERISK,       # (0,2)
    3:  Keycode.KEYPAD_MINUS,          # (0,3)
    4:  Keycode.KEYPAD_SEVEN,          # (1,0)
    5:  Keycode.KEYPAD_EIGHT,          # (1,1)
    6:  Keycode.KEYPAD_NINE,           # (1,2)
    7:  Keycode.KEYPAD_PLUS,           # (1,3)
    8:  Keycode.KEYPAD_FOUR,           # (2,0)
    9:  Keycode.KEYPAD_FIVE,           # (2,1)
    10: Keycode.KEYPAD_SIX,            # (2,2)
    12: Keycode.KEYPAD_ONE,            # (3,0)
    13: Keycode.KEYPAD_TWO,            # (3,1)
    14: Keycode.KEYPAD_THREE,          # (3,2)
    16: Keycode.KEYPAD_ZERO,           # (4,0)
    18: Keycode.KEYPAD_PERIOD,         # (4,2)
    19: Keycode.KEYPAD_ENTER,          # (4,3)
}

matrix = keypad.KeyMatrix(
    row_pins=(board.GP2, board.GP3, board.GP4, board.GP5, board.GP6),
    column_pins=(board.GP12, board.GP13, board.GP14, board.GP15),
    columns_to_anodes=False,  # REQUIRED: our diodes conduct row -> column; default True drives the blocked direction (all keys dead)
    interval=0.010,           # scan period = debounce window
)

keyboard = Keyboard(usb_hid.devices)

led = digitalio.DigitalInOut(board.GP11)
led.direction = digitalio.Direction.OUTPUT
led.value = True

while True:
    event = matrix.events.get()  # non-blocking; None if queue empty
    if event:
        keycode = KEYMAP.get(event.key_number)
        if keycode is not None:
            if event.pressed:
                keyboard.press(keycode)
            else:
                keyboard.release(keycode)