
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

import time
import board
import busio
import keypad
import usb_cdc
import usb_hid
import pwmio
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
from lcd import LCD
from i2c_pcf8574_interface import I2CPCF8574Interface

# ----------------------------
# CONSTANTS
# ----------------------------
# --- LCD geometry ---
LCD_COLS = 16                # number of character columns on the LCD
# --- timing / idle ---
IDLE_TIMEOUT_MS = 300_000    # inactivity time until screen and LEDs sleep
WX_STALE_MS = 30 * 60_000    # weather older than this shows as stale
STATS_STALE_MS = 15_000      # PC stats older than this show "no data"
# --- persistence ---
COUNT_FILE = "/count.txt"    # persistent store: keystroke count + LED brightness
FLUSH_EVERY = 100            # batch flash writes: save every N presses (idle also flushes)
# --- matrix key numbers (row * 4 + col) ---
FN_KEY = 0                   # NumLock, used as the Fn modifier
FN_BRIGHT_UP = 7             # '+' key: Fn combo raises LED brightness
FN_BRIGHT_DOWN = 3           # '-' key: Fn combo lowers LED brightness
# --- LCD views ---
VIEW_STATS = 1               # lifetime keypress counter
VIEW_CLOCK = 2               # clock + weather (default after splash)
VIEW_SPLASH = 3              # boot screen, transitions to clock
VIEW_PCSTATS = 4             # host PC stats, cycling pages
VIEW_CALC = 5                # calculator
STAT_PAGES = 4               # number of cycling PC-stat pages
WX_SLOTS = 4                 # number of preset weather locations
CALC_MAX_EXPR = 64           # hard cap on calc expression length
# --- key LEDs (white, PWM on GP11) ---
LED_STEP = 8192			     # brightness step per press (65535/8 = 8 levels + off)
LED_MIN = 0                  # minimum led strength
LED_MAX = 65535              # maximum led strength (16-bit PWM duty - 65,536 possible duty cycle values)

# ----------------------------
# TIME BASE
# ----------------------------
# current time in milliseconds
def now_ms():
    return time.monotonic_ns() // 1_000_000

SPLASH_DURATION_MS = 1500    # how long the boot splash stays before the clock
boot_time = now_ms()         # reference point for the splash timeout

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
            brightness = max(LED_MIN, min(LED_MAX, brightness))    # clamp to valid PWM range
        else:
            brightness = LED_MAX    # old file format

        return count, brightness

    except (OSError, ValueError, IndexError):
        return 0, LED_MAX    # missing/corrupt file: fresh defaults

def save_settings():
    global unsaved
    try:
        with open(COUNT_FILE, "w") as f:
            f.write("{}\n{}".format(
                press_count,
                led_brightness
            ))
        unsaved = 0    # reset the unsaved-press counter on a real write
    except OSError:
        pass    # ignore writes when storage is read-only (dev mode)

press_count, led_brightness = load_settings()
unsaved = 0    # presses accumulated since the last successful save

# ----------------------------
# HARDWARE INIT
# ----------------------------
# background matrix scanner (C-level, debounced, event queue).
matrix = keypad.KeyMatrix(
    row_pins=(board.GP2, board.GP3, board.GP4, board.GP5, board.GP6),
    column_pins=(board.GP12, board.GP13, board.GP14, board.GP15),
    columns_to_anodes=False,  # REQUIRED: diodes conduct row to column
    interval=0.010,
)

keyboard = Keyboard(usb_hid.devices)    # HID keyboard bound to the USB interface

# key-leds (white)
# GP11 drives all white key LEDs together, PWM gives brightness control.
# initial duty comes from the loaded setting so there's no flash-then-dim at boot.
led = pwmio.PWMOut(
    board.GP11,
    frequency=1000,
    duty_cycle=led_brightness
)

# push the current LED state to hardware: chosen brightness when awake,
# fully off when the display has slept. reads backlight_on/led_brightness
# as globals (both exist by the time this is first called in the loop).
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
fn_down = False             # NumLock physically held
fn_used = False             # a digit was consumed during this hold
consumed = set()            # keys whose release event should be ignored

view = VIEW_SPLASH          # default view
view_dirty = True           # set on any visible change, cleared after a render

backlight_on = True         # shadow of the (write-only) backlight/LED on-state
last_activity = now_ms()    # timestamp of the last key event, for idle timeout

# ----------------------------
# TIME SYNC STATE
# ----------------------------
synced = False          # False until the first host time message, clock shows UNKNOWN
epoch_at_sync = 0       # unix epoch (home-local) from last host message
ms_at_sync = 0          # monotonic ms when that message arrived
clock_shown = ""        # last strings rendered on the clock view

# reconstruct current time by free-running from the last sync anchor
# (host re-syncs periodically, so drift never exceeds one broadcast interval)
def current_epoch(t):
    return epoch_at_sync + (t - ms_at_sync) // 1000    # free-run: anchor epoch + elapsed monotonic time since anchor

rx_buf = b"" # partial-line accumulator for serial input

# ----------------------------
# PC STATS STATE
# ----------------------------
stat_lines = [["", ""] for _ in range(STAT_PAGES)]    # host-formatted display lines, [page][row]; '*' -> degree at draw
stats_rx_ms = 0          # receipt time of last stat message (0 = never); for staleness
stat_page = 0            # page shown on the PC stats view

# ----------------------------
# WEATHER STATE
# ----------------------------
wx = [None] * WX_SLOTS   # each: (label, cond, temp, offset_min), None until first msg
wx_rx_ms = 0             # receipt time of last weather message; for staleness
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

OPS = "+-*/"    # the four supported operator characters

# reset the calculator to empty (also used as clear-entry)
def calc_clear():
    global calc_expr, calc_result, calc_error
    calc_expr = ""
    calc_result = None
    calc_error = False

# format a numeric result for the row: whole numbers drop the ".0",
# anything too wide for the 16-char row (minus the '=') falls back to sci notation
def calc_display(v):
    # exact int when possible; scientific if too wide for the row
    if v == int(v):
        v = int(v)
    s = str(v)
    if len(s) <= LCD_COLS - 1:      # -1 for the '=' prefix
        return s
    return "{:.6e}".format(v)

# evaluate a full expression with PEMDAS via two reduction passes.
# input rules guarantee tokens strictly alternate number/op, so no parser
# stack is needed. returns a display string, or None on any error
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

    # even indices are numbers, odd indices are operators
    try:
        vals = [float(tokens[i]) for i in range(0, len(tokens), 2)]
    except ValueError:
        return None                 # malformed number, e.g. "."
    ops = [tokens[i] for i in range(1, len(tokens), 2)]

    # pass 1: fold * and / left-to-right (higher precedence)
    # i does not advance after a fold: the next op slides into index i.
    i = 0
    while i < len(ops):
        if ops[i] in "*/":
            if ops[i] == "/" and vals[i + 1] == 0:
                return None    # divide by zero -> error
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

# handle one captured calculator keypress (digit, '.', operator, or '=')
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
                return    # this number already has a decimal point
        if len(calc_expr) < CALC_MAX_EXPR:
            calc_expr += sym
        return

    if sym in OPS:
        if calc_error:
            return    # operators do nothing while in error state
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
        return    # nothing to do
    r = calc_eval(calc_expr)
    if r is None:
        calc_error = True
    else:
        calc_result = r

# the two calculator rows: expression (scrolled to its tail) and result/error
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
# dispatch one parsed host message. unknown keys are ignored, which is how
# the protocol stays forward-compatible: new host messages can't break old firmware.
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
        # wx{slot}: label,cond,temp,offset_min for one weather location
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
            continue    # skip non-UTF-8 lines
        if ":" in text:
            key, value = text.split(":", 1)    # split on first ':' only
            handle_message(key, value)

# ----------------------------
# DISPLAY
# ----------------------------

DEGREE = chr(0xDF)      # degree symbol in the HD44780 character ROM

# one PC-stats row for the current page, or a staleness placeholder
def stat_row(row, t):
    if (t - stats_rx_ms) > STATS_STALE_MS or stats_rx_ms == 0:
        return "no data" if row == 0 else ""
    # '*' is the host's placeholder for the degree symbol
    return stat_lines[stat_page][row].replace("*", DEGREE)

# clock-view row 0: current location's weather, or a placeholder if absent/stale
def weather_line(t):
    entry = wx[wx_index]
    if entry is None:
        return "no weather"
    label, cond, temp, _ = entry
    if (t - wx_rx_ms) > WX_STALE_MS:
        return label + " --"    # stale: label with no data
    return "{} {} {}{}".format(label, cond, temp, DEGREE)

# clock-view row 1: date/time in the displayed location's local time
# (host-supplied offset_min shifts the home-local epoch)
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
    # both PC-stats rows as one tuple, matching the clock's refresh pattern
    return (stat_row(0, t), stat_row(1, t))

# write one full LCD row, padded/truncated to width, so old content is
# overwritten without a clear() (clear() is slow and blanks both rows)
def draw_line(row, text):
    lcd.set_cursor_pos(row, 0)
    lcd.print((text + " " * LCD_COLS)[:LCD_COLS])

# draw the active view's two rows; caches the "shown" tuples for the
# time-varying views so the loop's refresh check can detect real changes
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

pcstats_shown = ("", "")    # last-rendered PC-stats rows (compared each loop)

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

    poll_serial()    # ingest any waiting host messages first

    # process every queued key event before updating the display, so a
    # burst of presses costs one render rather than one render per key
    event = matrix.events.get()
    while event:
        key = KEYS.get(event.key_number)
        if key is not None:
            keycode, fn_action, calc_sym = key

            if event.pressed:
                press_count += 1
                unsaved += 1
                # NumLock: don't act yet, decide tap vs. Fn at release
                if event.key_number == FN_KEY:
                    fn_down = True
                    fn_used = False
                # Fn + '+' = increase LED brightness
                elif fn_down and event.key_number == FN_BRIGHT_UP:
                    fn_used = True
                    consumed.add(event.key_number)    # suppress its release

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
                # input routing: while in the calculator, capture the key
                # for the calc instead of sending it to the host
                elif view == VIEW_CALC and calc_sym is not None:
                    consumed.add(event.key_number)
                    calc_key(calc_sym)
                    view_dirty = True
                else:
                    keyboard.press(keycode)    # normal typing -> HID
                if view == VIEW_STATS:
                    view_dirty = True      # count changed on-screen
            # key released
            else:
                if event.key_number == FN_KEY:
                    fn_down = False
                    # NumLock released without being used as Fn: send the tap now
                    if not fn_used:
                        keyboard.press(keycode)
                        keyboard.release(keycode)
                elif event.key_number in consumed:
                    consumed.discard(event.key_number)  # captured key: swallow release
                else:
                    keyboard.release(keycode)

        last_activity = t    # any edge counts as activity
        event = matrix.events.get()

    # batched persistence: flush after enough presses accumulate
    if unsaved >= FLUSH_EVERY:
        save_settings()

    # idle timeout: sleep display + LEDs (edge-triggered), and flush on
    # the way down since "walked away" is a natural save point
    idle = (t - last_activity) >= IDLE_TIMEOUT_MS
    if idle and backlight_on:
        lcd.set_backlight(False)

        backlight_on = False
        update_led()    # LEDs off with the backlight

        if unsaved:
            save_settings()
    elif not idle and not backlight_on:
        lcd.set_backlight(True)

        backlight_on = True
        update_led()    # restore chosen brightness on wake

    # splash transition: show the boot screen briefly, then the clock
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

    # render only when something actually changed
    if view_dirty:
        render(t)
        view_dirty = False
