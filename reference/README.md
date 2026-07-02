# reference

manual_scan.py is a hand-written implementation of what
keypad.KeyMatrix does internally. Functional, but not the production
firmware (see code.py at repo root).

## Manual polling vs KeyMatrix

manual_scan.py scans in Python inside the main loop, so the matrix
is only observed while scan() runs and the debounce sampling period
jitters with loop timing. State is a shared array with no history:
a press and release between two scans is lost, and a stalled loop
can desync HID state.

code.py uses KeyMatrix: scanning runs in C on a fixed 10 ms timer,
independent of the main loop, and debounced edges are delivered
through an event queue. A stalled loop delays input instead of
dropping it.

Bare-metal analogue: busy-wait polling vs timer-ISR capture feeding
a ring buffer — same pattern as UART FIFOs.

## Debounce

manual_scan.py implements a per-key stability window: a raw state
change is committed only after 10 ms without further change, so
contact bounce keeps resetting the timer and never commits. This is
the standard algorithm, as opposed to lockout-style debounce (fire
immediately, then ignore for N ms), which acts on possibly-bouncing
first readings and blocks legitimate fast re-presses.