# ------------------------------------------------------------------
# Production Version - 17-key USB numpad + LCD (Phase 7)
# Hardware: Raspberry Pi Pico W (RP2040), CircuitPython 9.2.1
#   Hand-wired 5x4 matrix, one diode per key
#   (anode toward row, cathode toward column)
#   Rows GP2-GP6, Cols GP12-GP15, LED GP11
#   16x2 LCD on PCF8574 I2C backpack @ 0x27, SCL GP1, SDA GP0
#
# Features:
#   • USB HID numeric keypad with event-driven matrix scanning
#     (keypad.KeyMatrix)
#   • LCD interface with switchable views:
#       - Clock + Environment Canada weather (4 locations)
#       - PC hardware statistics (4 pages)
#       - Calculator (operator precedence, local input capture)
#       - Lifetime keypress statistics
#   • NumLock functions as a momentary Fn modifier:
#       - View selection and cycling
#       - LED brightness adjustment (Fn+'+' / Fn+'-')
#   • Adjustable PWM keypad LED brightness with persistent storage
#   • Persistent settings stored in /count.txt:
#       - Lifetime keypress counter
#       - LED brightness
#   • Automatic LCD backlight and LED timeout after inactivity
#   • Optional host companion over USB CDC for:
#       - Time synchronization
#       - Weather
#       - PC hardware monitoring
#
# Design highlights:
#   • Edge-driven HID press/release handling
#   • Dirty-flag LCD rendering (no continuous redraws)
#   • Event-driven keypad scanning in firmware
#   • Host performs formatting and data collection; Pico acts as a
#     lightweight display/input device
# ------------------------------------------------------------------

import pwmio

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
IDLE_TIMEOUT_MS = 300_000
LCD_COLS = 16
COUNT_FILE = "/count.txt"
FLUSH_EVERY = 100 # save cycle every 100 presses

WX_SLOTS = 4
WX_STALE_MS = 30 * 60_000
STATS_STALE_MS = 15_000
STAT_PAGES = 4

CALC_MAX_EXPR = 64		# hard cap on calc expression length

FN_KEY = 0				# key_number of NumLock
FN_BRIGHT_UP = 7       # '+' key: Fn combo raises LED brightness
FN_BRIGHT_DOWN = 3     # '-' key: Fn combo lowers LED brightness

# LCD views
VIEW_STATS = 1
VIEW_CLOCK = 2
VIEW_SPLASH = 3
VIEW_PCSTATS = 4
VIEW_CALC = 5

# key-leds (white)
LED_STEP = 8192			# 12.5~% per press
LED_MIN = 0
LED_MAX = 65535


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
# key_number = row * 4 + col ->
#   (HID keycode, Fn view or None, calculator symbol or None)
# Unwired matrix positions are intentionally omitted.
KEYS = {
    0:  (Keycode.KEYPAD_NUMLOCK, None, None),
    1:  (Keycode.KEYPAD_FORWARD_SLASH, None, "/"),
    2:  (Keycode.KEYPAD_ASTERISK, None, "*"),
    3:  (Keycode.KEYPAD_MINUS, None, "-"),
    4:  (Keycode.KEYPAD_SEVEN, None, "7"),
    5:  (Keycode.KEYPAD_EIGHT, None, "8"),
    6:  (Keycode.KEYPAD_NINE, None, "9"),
    7:  (Keycode.KEYPAD_PLUS, None, "+"),
    8:  (Keycode.KEYPAD_FOUR, None, "4"),
    9:  (Keycode.KEYPAD_FIVE, None, "5"),
    10: (Keycode.KEYPAD_SIX, None, "6"),
    12: (Keycode.KEYPAD_ONE, VIEW_PCSTATS, "1"),
    13: (Keycode.KEYPAD_TWO, VIEW_CALC, "2"),
    14: (Keycode.KEYPAD_THREE, VIEW_STATS, "3"),
    16: (Keycode.KEYPAD_ZERO, VIEW_CLOCK, "0"),
    18: (Keycode.KEYPAD_PERIOD, None, "."),
    19: (Keycode.KEYPAD_ENTER, None, "="),
}

# ----------------------------
# STORAGE
# ----------------------------
# load persistent lifetime press counter and led brightness state
def load_settings():
    try:
        with open(COUNT_FILE) as f:
            lines = f.read().splitlines()

        count = int(lines[0])

        if len(lines) > 1:
            brightness = int(lines[1])
            brightness = max(LED_MIN, min(LED_MAX, brightness))
        else:
            # old file format
            brightness = LED_MAX

        return count, brightness

    except (OSError, ValueError, IndexError):
        return 0, LED_MAX

def save_settings():
    global unsaved
    try:
        with open(COUNT_FILE, "w") as f:
            f.write("{}\n{}".format(
                press_count,
                led_brightness
            ))
        unsaved = 0
    except OSError:
        pass            # ignore writes when storage is read-only (dev mode)

press_count, led_brightness = load_settings()
unsaved = 0

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

# key-leds (white)
# replace DigitalInOut with PWMOut
led = pwmio.PWMOut(
    board.GP11,
    frequency=1000,
    duty_cycle=led_brightness
)
#led = digitalio.DigitalInOut(board.GP11)
#led.direction = digitalio.Direction.OUTPUT
#led.value = True
def update_led():
    if backlight_on:
        led.duty_cycle = led_brightness
    else:
        led.duty_cycle = 0

i2c = busio.I2C(scl=board.GP1, sda=board.GP0)
lcd = LCD(I2CPCF8574Interface(i2c, 0x27), num_rows=2, num_cols=LCD_COLS)

serial = usb_cdc.data      # None if boot.py didn't enable it

# ----------------------------
# STATE
# ----------------------------
# Fn (Function) state
fn_down = False         # NumLock physically held
fn_used = False         # a digit was consumed during this hold
consumed = set()        # keys whose release event should be ignored

view = VIEW_SPLASH      # default view
view_dirty = True

backlight_on = True
last_activity = now_ms()

# ----------------------------
# TIME SYNC STATE
# ----------------------------
synced = False
epoch_at_sync = 0       # unix epoch (home-local) from last host message
ms_at_sync = 0          # monotonic ms when that message arrived
clock_shown = ""        # last strings rendered on the clock view

def current_epoch(t):
    # free-run: anchor epoch + elapsed monotonic time since anchor
    return epoch_at_sync + (t - ms_at_sync) // 1000

rx_buf = b""            # partial-line accumulator for serial input

# ----------------------------
# PC STATS STATE
# ----------------------------
# host-formatted display lines, [page][row]; '*' -> degree at draw
stat_lines = [["", ""] for _ in range(STAT_PAGES)]
stats_rx_ms = 0          # receipt time for staleness
stat_page = 0            # page shown on the PC stats view

# ----------------------------
# WEATHER STATE
# ----------------------------
wx = [None] * WX_SLOTS   # each: (label, cond, temp, offset_min)
wx_rx_ms = 0             # receipt time for staleness
wx_index = 0             # location shown on the clock view

# ----------------------------
# CALCULATOR STATE
# ----------------------------
# expr   - expression string as typed ("2+3*4")
# result - result string after Enter, or None
# error  - True after div-by-zero/malformed; next digit clears
calc_expr = ""
calc_result = None
calc_error = False

OPS = "+-*/"

def calc_clear():
    global calc_expr, calc_result, calc_error
    calc_expr = ""
    calc_result = None
    calc_error = False

def calc_display(v):
    # exact int when possible; scientific if too wide for the row
    if v == int(v):
        v = int(v)
    s = str(v)
    if len(s) <= LCD_COLS - 1:      # -1 for the '=' prefix
        return s
    return "{:.6e}".format(v)

def calc_eval(expr):
    """PEMDAS for + - * / by two-pass reduction.
    Returns result string, or None on error."""
    if expr and expr[-1] in OPS:
        expr = expr[:-1]            # trailing operator: ignore
    if not expr:
        return None

    # tokenize: numbers and operators, strictly alternating
    tokens = []
    num = ""
    for ch in expr:
        if ch in OPS:
            tokens.append(num)
            tokens.append(ch)
            num = ""
        else:
            num += ch
    tokens.append(num)

    try:
        vals = [float(tokens[i]) for i in range(0, len(tokens), 2)]
    except ValueError:
        return None                 # malformed number, e.g. "."
    ops = [tokens[i] for i in range(1, len(tokens), 2)]

    # pass 1: fold * and / left-to-right (higher precedence)
    i = 0
    while i < len(ops):
        if ops[i] in "*/":
            if ops[i] == "/" and vals[i + 1] == 0:
                return None
            vals[i] = (vals[i] * vals[i + 1] if ops[i] == "*"
                       else vals[i] / vals[i + 1])
            del vals[i + 1]
            del ops[i]
        else:
            i += 1

    # pass 2: sum the remaining +/- terms
    acc = vals[0]
    for op, v in zip(ops, vals[1:]):
        acc = acc + v if op == "+" else acc - v
    return calc_display(acc)

def calc_key(sym):
    global calc_expr, calc_result, calc_error
    if sym in "0123456789.":
        if calc_error or calc_result is not None:
            calc_clear()            # digit after result/error: fresh start
        if sym == ".":
            # one '.' per number: scan back to the last operator
            tail = calc_expr
            for op in OPS:
                tail = tail.split(op)[-1]
            if "." in tail:
                return
        if len(calc_expr) < CALC_MAX_EXPR:
            calc_expr += sym
        return

    if sym in OPS:
        if calc_error:
            return
        if calc_result is not None:
            # operator after result: continue from the result
            calc_expr = calc_result
            calc_result = None
        if not calc_expr:
            return                  # no leading operator (no unary minus)
        if calc_expr[-1] in OPS:
            calc_expr = calc_expr[:-1] + sym   # replace repeated op
        else:
            calc_expr += sym
        return

    # sym == "=": evaluate
    if calc_error or not calc_expr or calc_result is not None:
        return
    r = calc_eval(calc_expr)
    if r is None:
        calc_error = True
    else:
        calc_result = r

def calc_rows():
    top = calc_expr[-LCD_COLS:]     # scroll: show the tail
    if calc_error:
        return (top, "Error")
    if calc_result is not None:
        return (top, "=" + calc_result)
    return (top, "")

# ----------------------------
# SERIAL
# ----------------------------
def handle_message(key, value):
    global synced, epoch_at_sync, ms_at_sync
    global view_dirty, stats_rx_ms, wx_rx_ms
    if key == "time":
        try:
            epoch_at_sync = int(value)
        except ValueError:
            return
        ms_at_sync = now_ms()
        synced = True
    elif len(key) == 3 and key[0] == "p" and key[2] in "ab":
        # p{page}{row}: pre-formatted stat line from the host
        try:
            page = int(key[1])
        except ValueError:
            return
        if 0 <= page < STAT_PAGES:
            stat_lines[page][0 if key[2] == "a" else 1] = value
            stats_rx_ms = now_ms()
            if view == VIEW_PCSTATS and page == stat_page:
                view_dirty = True
    elif key.startswith("wx"):
        try:
            slot = int(key[2:])
            label, cond, temp, off = value.split(",")
            entry = (label, cond, int(temp), int(off))
        except (ValueError, IndexError):
            return
        if 0 <= slot < WX_SLOTS:
            wx[slot] = entry
            wx_rx_ms = now_ms()
            if view == VIEW_CLOCK:
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

def stat_row(row, t):
    if (t - stats_rx_ms) > STATS_STALE_MS or stats_rx_ms == 0:
        return "no data" if row == 0 else ""
    # '*' is the host's placeholder for the degree symbol
    return stat_lines[stat_page][row].replace("*", DEGREE)

def weather_line(t):
    entry = wx[wx_index]
    if entry is None:
        return "no weather"
    label, cond, temp, _ = entry
    if (t - wx_rx_ms) > WX_STALE_MS:
        return label + " --"
    return "{} {} {}{}".format(label, cond, temp, DEGREE)

def clock_string(t):
    if not synced:
        return "UNKNOWN"
    entry = wx[wx_index]
    off_min = entry[3] if entry is not None else 0
    tm = time.localtime(current_epoch(t) + off_min * 60)
    return "{:04}/{:02}/{:02} {:02}:{:02}".format(
        tm.tm_year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min)

def clock_rows(t):
    # both clock-view rows as one tuple, so the refresh check and
    # the renderer compare/draw the same thing
    return (weather_line(t), clock_string(t))

def pcstats_rows(t):
    return (stat_row(0, t), stat_row(1, t))

# overwrite an entire LCD row to avoid clearing the display
def draw_line(row, text):
    lcd.set_cursor_pos(row, 0)
    lcd.print((text + " " * LCD_COLS)[:LCD_COLS])

# draw the active screen
def render(t):
    global clock_shown, pcstats_shown
    if view == VIEW_SPLASH:
        draw_line(0, "pico-numpad")
        draw_line(1, "")
    elif view == VIEW_CLOCK:
        clock_shown = clock_rows(t)
        draw_line(0, clock_shown[0])
        draw_line(1, clock_shown[1])
    elif view == VIEW_STATS:
        draw_line(0, "Presses:")
        draw_line(1, str(press_count))
    elif view == VIEW_PCSTATS:
        pcstats_shown = pcstats_rows(t)
        draw_line(0, pcstats_shown[0])
        draw_line(1, pcstats_shown[1])
    elif view == VIEW_CALC:
        rows = calc_rows()
        draw_line(0, rows[0])
        draw_line(1, rows[1])

pcstats_shown = ("", "")

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
            keycode, fn_action, calc_sym = key

            if event.pressed:
                press_count += 1
                unsaved += 1
                # decide tap vs. Fn when NumLock is released
                if event.key_number == FN_KEY:
                    fn_down = True
                    fn_used = False
                # Fn + '+' = increase LED brightness
                elif fn_down and event.key_number == FN_BRIGHT_UP:
                    fn_used = True
                    consumed.add(event.key_number)

                    led_brightness = min(LED_MAX, led_brightness + LED_STEP)
                    update_led()


                # Fn + '-' = reduce LED brightness
                elif fn_down and event.key_number == FN_BRIGHT_DOWN:
                    fn_used = True
                    consumed.add(event.key_number)

                    led_brightness = max(LED_MIN, led_brightness - LED_STEP)
                    update_led()

                # Fn shortcut: switch view without sending a key.
                # Repeated on the view's own key: clock cycles the
                # weather location, PC stats cycles the page,
                # calculator clears.
                elif fn_down and fn_action is not None:
                    fn_used = True
                    consumed.add(event.key_number)
                    if view != fn_action:
                        if view == VIEW_CALC:
                            calc_clear()       # leaving calc = CE
                        view = fn_action
                    elif fn_action == VIEW_CLOCK:
                        wx_index = (wx_index + 1) % WX_SLOTS
                    elif fn_action == VIEW_PCSTATS:
                        stat_page = (stat_page + 1) % STAT_PAGES
                    elif fn_action == VIEW_CALC:
                        calc_clear()
                    view_dirty = True
                # input routing: calculator captures its keys
                elif view == VIEW_CALC and calc_sym is not None:
                    consumed.add(event.key_number)
                    calc_key(calc_sym)
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
        save_settings()

    # handle idle backlight and save before sleeping
    idle = (t - last_activity) >= IDLE_TIMEOUT_MS
    if idle and backlight_on:
        lcd.set_backlight(False)

        backlight_on = False
        update_led()

        if unsaved:
            save_settings()
    elif not idle and not backlight_on:
        lcd.set_backlight(True)

        backlight_on = True
        update_led()

    # splash transition
    if view == VIEW_SPLASH and (t - boot_time) > SPLASH_DURATION_MS:
        view = VIEW_CLOCK
        view_dirty = True

    # clock refresh: redraw only when either displayed row would
    # change (minute rollover, sync, weather update, staleness edge)
    if view == VIEW_CLOCK and clock_rows(t) != clock_shown:
        view_dirty = True

    # PC stats refresh: same pattern (new host lines, page change,
    # staleness edge)
    if view == VIEW_PCSTATS and pcstats_rows(t) != pcstats_shown:
        view_dirty = True

    # render
    if view_dirty:
        render(t)
        view_dirty = False
