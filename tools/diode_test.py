# ------------------------------------------------------------------
# Diode Test - verifies per-key diode presence and orientation
#
# Method: scans the matrix in both directions. Forward = drive rows,
# read columns (normal operation). Reverse = drive columns, read
# rows. A series diode conducts one way only, so where a pressed
# key appears tells you the diode's state:
#   FWD only  - diode present, anode-to-row (healthy)
#   BOTH      - diode bypassed/shorted (solder bridge?)
#   REV only  - diode reversed relative to the others
#   neither   - open connection
#
# Usage: copy to CIRCUITPY as code.py (back up production code
# first), open a serial console, hold each key ~0.5 s (scan
# alternates directions; a quick tap may land in only one phase
# and false-flag). Healthy result: one "FWD press ... diode OK"
# line per key, zero BOTH/REV lines.
#
# Limitation: cannot detect ALL diodes being reversed (forward
# scan would simply show nothing) — establish the baseline with
# matrix_mapping_test.py first.
#
# Hardware: Pi Pico W, rows GP2-GP6, cols GP12-GP15
# ------------------------------------------------------------------

import time
import board
import digitalio

ROW_PINS = (board.GP2, board.GP3, board.GP4, board.GP5, board.GP6)
COL_PINS = (board.GP12, board.GP13, board.GP14, board.GP15)

def make(pins, output):
    ios = []
    for p in pins:
        d = digitalio.DigitalInOut(p)
        if output:
            d.direction = digitalio.Direction.OUTPUT
            d.value = False
        else:
            d.direction = digitalio.Direction.INPUT
            d.pull = digitalio.Pull.DOWN
        ios.append(d)
    return ios

def scan(drivers, readers):
    hits = set()
    for i, drv in enumerate(drivers):
        drv.value = True
        time.sleep(0.0005)
        for j, rdr in enumerate(readers):
            if rdr.value:
                hits.add((i, j))
        drv.value = False
    return hits

rows = make(ROW_PINS, output=True)
cols = make(COL_PINS, output=False)

prev_fwd = set()
prev_rev = set()

while True:
    # forward: drive rows, read cols
    fwd = scan(rows, cols)          # (row, col)

    # swap pin roles - deinit() releases each pin so it can be reconfigured; skipping this raises "pin in use"
    for d in rows + cols:
        d.deinit()
    cols_drv = make(COL_PINS, output=True)
    rows_rd = make(ROW_PINS, output=False)

    # reverse scan yields (col, row); normalize to (row, col)
    rev = {(r, c) for (c, r) in scan(cols_drv, rows_rd)} 

    # swap back
    for d in cols_drv + rows_rd:
        d.deinit()
    rows = make(ROW_PINS, output=True)
    cols = make(COL_PINS, output=False)

    for k in (fwd - prev_fwd):
        print("FWD  press", k, "<- diode OK" if k not in rev else "")
    for k in (rev - prev_rev):
        if k in fwd:
            print("BOTH directions", k, "<- diode bypassed/shorted!")
        else:
            print("REV only", k, "<- diode reversed vs others!")

    prev_fwd, prev_rev = fwd, rev
    time.sleep(0.02)