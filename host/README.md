
# Host companion

`companion.py` broadcasts time, weather, and PC hardware stats to the
numpad over its second USB CDC serial port. The pad works without it -
this script only feeds the clock, weather, and PC stats views. Protocol
and message format: see the [Design section](../README.md#design) of
the main README and the header comment in `companion.py`.

## Requirements

- Python 3.9+ with `pyserial`, `requests`, `env_canada`
  (`pip install pyserial requests env_canada`)
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
  running as administrator with its web server enabled
  (Options - Remote Web Server - Run) - needed for PC stats only.
  Time and weather work without it.

## Running manually

    python companion.py

There is no console output. Everything, including the "connected"
message and errors, goes to `companion.log` beside the script (see
Logging). The Pico exposes two serial ports; the script auto-detects
the data port, or set `PORT` at the top of the script.

Only one instance can hold the serial port. If a scheduled instance
is already running (see below), stop it first, otherwise the manual
run loops on "disconnected, retrying" without ever connecting.

## Automatic startup (Windows)

Two pieces need to start at logon: LibreHardwareMonitor and
companion.py.

### LibreHardwareMonitor

Use its own options: Run On Windows Startup, Start Minimized,
Minimize To Tray, and Remote Web Server. LHM registers its own
elevated startup task, so UAC is handled for you.

### companion.py via Task Scheduler

The script runs under `pythonw.exe`, the windowless twin of
`python.exe` that ships in the same folder - no console window,
which is also why logging goes to a file (under pythonw, `print()`
output is silently discarded).

**Finding the interpreter.** Do not use paths under
`...\AppData\Local\Microsoft\WindowsApps\` - those are Microsoft
Store stubs, not real executables, and Task Scheduler launching the
pythonw stub exits silently with result 0x1. Get the real path with:

    python -c "import sys; print(sys.executable)"

and use the `pythonw.exe` in that same folder.

**Task configuration** (Task Scheduler - Create Task, not the Basic
wizard):

- General: Run only when user is logged on (keeps the task in your
  session, where LHM's web server and your COM port permissions live).
  Highest privileges not needed.
- Trigger: At log on, and in the same dialog check
  "Repeat task every: 5 minutes" for a duration of "Indefinitely"
  (the dialog defaults to 30 minutes - change it).
- Action: Start a program.
  - Program/script: full path to `pythonw.exe`
  - Arguments: full path to `companion.py`, quoted
  - Start in: the script's folder, not quoted (this field rejects
    quotes)
  The interpreter is the program and the script is its argument
  because Task Scheduler executes the program field directly as a
  process - it does not go through file-association logic, so
  pointing it at the .py file is unreliable.
- Settings:
  - Uncheck "Stop the task if it runs longer than 3 days"
  - "If the task fails, restart every 1 minute", 3 attempts
  - "If the task is already running: Do not start a new instance"

**How the watchdog works.** The 5-minute repeat tick tries to start
the task; "Do not start a new instance" makes that a no-op while the
script is alive. If the process ever dies - crash, clean exit, or
killed - the next tick resurrects it. The failure-restart setting
covers non-zero exits faster (1 minute), and the repeat covers
everything else.

**Testing note.** Repetition belongs to the trigger, and only arms
when the trigger actually fires. A manual right-click Run creates an
instance with no trigger behind it - kill that and nothing restarts
it. To test the watchdog for real: log off and back on, verify
pythonw.exe is running (Task Manager, Details tab), kill it, and
wait up to ~6 minutes.

## Logging

`companion.log` beside the script, rotating at 500 KB with 2 backups
(~1.5 MB cap). Logged events: connect/disconnect of the Pico's port,
per-location weather failures, and full tracebacks for any crash
(the script exits non-zero on a crash so the task's failure-restart
fires).

## Troubleshooting

First stop for any problem is `companion.log`. Then:

| Symptom | Likely cause | Check |
| --- | --- | --- |
| Stat pages say "LHM down" | LHM not running, not admin, or web server off | LHM tray icon; `http://localhost:8085/data.json` in a browser |
| Stat pages say "no data" | companion.py not running or Pico port not found | Task Manager Details for pythonw.exe; log for "no Pico data port" |
| Clock says UNKNOWN | No time message received since power-up | Same as above - the clock has no RTC and needs one sync |
| Weather shows "no weather" or `LABEL --` | env_canada fetch failing or data stale (>30 min) | Log for "wx ... failed" lines |
| Task shows Last Run Result 0x1, no process | WindowsApps stub as the program, or missing packages | Program path in the task's Action; run the same folder's python.exe in a console to see the error |
| Manual run connects nothing, log says "disconnected, retrying" forever | Scheduled instance already holds the COM port | End the running task before manual runs |
| Stats stuck / values look wrong for your PC | Sensor names target the original machine (i7-13700K, RTX 4080) | Adjust the `find()` names in `stat_pages()` against your LHM tree |
