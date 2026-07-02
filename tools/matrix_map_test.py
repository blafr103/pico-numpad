# ------------------------------------------------------------------
# Matrix Mapping - maps physical keys to (row, col)
#
# Prints PRESS/RELEASE with matrix coordinates for every key edge.
# Use to build or verify KEYMAP: press each key in reading order
# and record which (row, col) it reports. Raw scan, no debounce —
# occasional PRESS/RELEASE/PRESS bursts from one physical press
# are contact bounce, visible here by design.
#
# Also reveals: ghosting (one press reports two positions),
# opens (press reports nothing), and whether tall keys (+/Enter/0)
# are one switch or two in parallel (different spots on the cap
# reporting different coordinates).
#
# Usage: copy to CIRCUITPY as code.py (back up production code
# first), open a serial console, press keys.
#
# Hardware: Pi Pico W, rows GP2-GP6, cols GP12-GP15
# ------------------------------------------------------------------

import time
import board
import digitalio

ROW_PINS = [board.GP2, board.GP3, board.GP4, board.GP5, board.GP6]
COL_PINS = [board.GP12, board.GP13, board.GP14, board.GP15]

rows = []
for p in ROW_PINS:
    d = digitalio.DigitalInOut(p)
    d.direction = digitalio.Direction.OUTPUT
    d.value = False
    rows.append(d)

cols = []
for p in COL_PINS:
    d = digitalio.DigitalInOut(p)
    d.direction = digitalio.Direction.INPUT
    d.pull = digitalio.Pull.DOWN
    cols.append(d)

prev = set()  # keys pressed on the previous scan

while True:
    pressed = set()
    for r in range(5):
        rows[r].value = True
        time.sleep(0.001)  # settle: let the column line charge
        for c in range(4):
            if cols[c].value:
                pressed.add((r, c))
        rows[r].value = False

    # set difference - edge detection: act on transitions, not levels
    for key in pressed - prev:
        print("PRESS  row", key[0], "col", key[1])
    for key in prev - pressed:
        print("RELEASE row", key[0], "col", key[1])

    prev = pressed
    time.sleep(0.01)