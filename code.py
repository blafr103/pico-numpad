# ------------------------------------------------------------------
# Production Version - 17-key USB numpad + LCD (Phase 4)
# Hardware: Raspberry Pi Pico W (RP2040), CircuitPython 9.2.1
#   Hand-wired 5x4 matrix, one diode per key
#   (anode toward row, cathode toward column)
#   Rows GP2-GP6, Cols GP12-GP15, LED GP11
#   16x2 LCD on PCF8574 I2C backpack @ 0x27, SCL GP1, SDA GP0
#
# Phase 4 adds:
#   - PC stats view (Fn+2): CPU and GPU utilization %, temperature,
#     and package power, one line each. Fed by the host script;
#     shows 'no data' until the first message, '--' for individual
#     sensors the host reports as missing (-1). Values persist
#     unchanged if the host stops sending (no staleness timeout).
#
# Phase 3: host serial channel over usb_cdc.data (requires boot.py
#   enabling it; power cycle after changing boot.py).
#   Protocol: newline-terminated ASCII "key:value" lines.
#   Pico is a pure listener; the PC script (host/companion.py)
#   broadcasts unsolicited. Handled: "time:<epoch>" (epoch
#   pre-adjusted to local time), "cpu:<util>,<temp>,<power>",
#   "gpu:<util>,<temp>,<power>".
#   Clock view (Fn+0) shows YYYY/MM/DD + HH:MM, free-running
#   between syncs (epoch anchored to monotonic ms at receipt);
#   re-anchors on every message, bounding crystal drift to one
#   broadcast interval. UNKNOWN until first sync; time is lost at
#   power-off (no battery RTC on RP2040).
#
# Phase 2: NumLock momentary Fn (Fn+1 press stats), splash on boot,
#   lifetime press counter in /count.txt, batched flushes, boot.py
#   dev-mode via NumLock at plug-in.
# Phase 1: no sleep() in loop, dirty-flag rendering, fixed-width
#   overwrites, edge-triggered backlight, 60 s idle.
# ------------------------------------------------------------------

import time
import board
import busio
import digitalio
import keypad
import usb_cdc
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

from lcd import LCD
from i2c_pcf8574_interface import I2CPCF8574Interface


# ----------------------------
# CONSTANTS
# ----------------------------
IDLE_TIMEOUT_MS = 60_000
LCD_COLS = 16
COUNT_FILE = "/count.txt"
FLUSH_EVERY = 100

FN_KEY = 0            # key_number of NumLock
# LCD views
VIEW_STATS = 1
VIEW_CLOCK = 2
VIEW_SPLASH = 3
VIEW_PCSTATS = 4

# ----------------------------
# TIME BASE
# ----------------------------
# current time in milliseconds
def now_ms():
    return time.monotonic_ns() // 1_000_000

SPLASH_DURATION_MS = 1500
boot_time = now_ms()

# ----------------------------
# KEY MAP
# ----------------------------
# Matrix position - key_number = row * 4 + col - (HID keycode, Fn view or None)
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
    13: (Keycode.KEYPAD_TWO, VIEW_PCSTATS),
    14: (Keycode.KEYPAD_THREE, None),
    16: (Keycode.KEYPAD_ZERO, VIEW_CLOCK),
    18: (Keycode.KEYPAD_PERIOD, None),
    19: (Keycode.KEYPAD_ENTER, None),
}

# ----------------------------
# HARDWARE INIT
# ----------------------------
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

serial = usb_cdc.data      # None if boot.py didn't enable it

# ----------------------------
# STORAGE
# ----------------------------
# load persistent lifetime press counter
def load_count():
    try:
        with open(COUNT_FILE) as f:
            return int(f.read())
    except (OSError, ValueError):
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


# ----------------------------
# STATE
# ----------------------------
# Fn (Function) state
fn_down = False         # NumLock physically held
fn_used = False         # a digit was consumed during this hold
consumed = set()        # keys whose release event should be ignored


view = VIEW_SPLASH		# default view
view_dirty = True

backlight_on = True
last_activity = now_ms()

# ----------------------------
# TIME SYNC STATE
# ----------------------------
synced = False
epoch_at_sync = 0       # unix epoch (local) from last host message
ms_at_sync = 0          # monotonic ms when that message arrived
clock_shown = ""        # last string rendered on the clock view

def current_epoch(t):
    # free-run: anchor epoch + elapsed monotonic time since anchor
    return epoch_at_sync + (t - ms_at_sync) // 1000

rx_buf = b""            # partial-line accumulator for serial input


# ----------------------------
# PC STATS STATE
# ----------------------------
pc_stats = {"cpu": None, "gpu": None}   # each: (util, temp, power)



# ----------------------------
# SERIAL
# ----------------------------
def handle_message(key, value):
    global synced, epoch_at_sync, ms_at_sync
    if key == "time":
        try:
            epoch_at_sync = int(value)
        except ValueError:
            return
        ms_at_sync = now_ms()
        synced = True
    elif key in ("cpu", "gpu"):
        global view_dirty
        try:
            util, temp, pwr = (int(x) for x in value.split(","))
        except ValueError:
            return
        pc_stats[key] = (util, temp, pwr)
        if view == VIEW_PCSTATS:
            view_dirty = True

def poll_serial():
    # non-blocking: consume whatever bytes are waiting, act on
    # complete lines, keep the remainder buffered. A line may
    # arrive split across polls; rx_buf carries the partial.
    global rx_buf
    if serial is None or serial.in_waiting == 0:
        return
    rx_buf += serial.read(serial.in_waiting)
    while b"\n" in rx_buf:
        line, rx_buf = rx_buf.split(b"\n", 1)
        try:
            text = line.decode().strip()
        except UnicodeError:
            continue
        if ":" in text:
            key, value = text.split(":", 1)
            handle_message(key, value)
            
# ----------------------------
# DISPLAY
# ----------------------------

DEGREE = chr(0xDF)      # degree symbol in the HD44780 character ROM

def fmt(v, suffix):
    return "--" + suffix if v < 0 else str(v) + suffix

def stats_line(label, s):
    if s is None:
        return label + " no data"
    util, temp, pwr = s
    return "{} {} {} {}".format(
        label, fmt(util, "%"), fmt(temp, DEGREE), fmt(pwr, "W"))

# overwrite an entire LCD row to avoid clearing the display
def draw_line(row, text):
    lcd.set_cursor_pos(row, 0)
    lcd.print((text + " " * LCD_COLS)[:LCD_COLS])

# draw the active screen
def render(t):
    global clock_shown
    if view == VIEW_SPLASH:
        draw_line(0, "pico-numpad")
        draw_line(1, "")
    elif view == VIEW_CLOCK:
        clock_shown = clock_string(t)
        draw_line(0, "Clock")
        draw_line(1, clock_shown)
    elif view == VIEW_STATS:
        draw_line(0, "Presses:")
        draw_line(1, str(press_count))
    elif view == VIEW_PCSTATS:
        draw_line(0, stats_line("C", pc_stats["cpu"]))
        draw_line(1, stats_line("G", pc_stats["gpu"]))
        
def clock_string(t):
    if not synced:
        return "UNKNOWN"
    tm = time.localtime(current_epoch(t))
    return "{:04}/{:02}/{:02} {:02}:{:02}".format(
        tm.tm_year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min)

# ----------------------------
# INIT DISPLAY
# ----------------------------
# initial LCD state
lcd.clear()
lcd.set_backlight(True)

# ----------------------------
# MAIN LOOP
# ----------------------------
while True:
    t = now_ms()
    
    poll_serial()

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
        
    # splash transition
    if view == VIEW_SPLASH and (t - boot_time) > SPLASH_DURATION_MS:
        view = VIEW_CLOCK
        view_dirty = True
        
    # clock refresh: redraw only when the displayed string would
    # change (minute rollover or first sync) — not every pass
    if view == VIEW_CLOCK and clock_string(t) != clock_shown:
        view_dirty = True
        
    # render
    if view_dirty:
        render(t)
        view_dirty = False
