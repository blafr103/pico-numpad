# ------------------------------------------------------------------
# boot.py - Runs once at power-up, before USB enumeration and code.py.
#
# Selects filesystem ownership. The FAT filesystem can be writable
# by either the USB host or CircuitPython, but never both.
#
# 1. Filesystem ownership (Phase 2):
#      Normal boot  - code owns storage (persistence works)
#      NumLock held - host owns storage (dev mode)
# 2. USB CDC (Phase 6): enable the second serial port ("data").
#    console = REPL/Thonny as before; data = host<->Pico messages.
#    Changes here take effect only after a POWER CYCLE, not reload.
# ------------------------------------------------------------------

import board
import digitalio
import storage
import usb_cdc

usb_cdc.enable(console=True, data=True)

# configure GP2 as output, drive HIGH
row0 = digitalio.DigitalInOut(board.GP2)
row0.direction = digitalio.Direction.OUTPUT
row0.value = True

# configure GP12 as input with an internal pull-down resistor
col0 = digitalio.DigitalInOut(board.GP12)
col0.direction = digitalio.Direction.INPUT
col0.pull = digitalio.Pull.DOWN

# read GP12, check if NumLock is pressed
numlock_held = col0.value

# release in/out pins so KeyMatrix can claim them in code.py
row0.deinit()
col0.deinit()

# If NumLock is not held, give CircuitPython write access to the filesystem so the firmware can update count.txt.
# Otherwise, leave the default host-owned (development) mode unchanged.
if not numlock_held:
    storage.remount("/", readonly=False)
