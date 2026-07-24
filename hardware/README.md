# pico-numpad - Hardware

Custom two-layer PCB for a 17-key USB numpad with per-key RGB, a rotary encoder, and a 16x2 character LCD. This is the custom-board successor to the hand-wired CircuitPython numpad documented in the [repository root](../), rebuilt around a bare RP2040 with hot-swap switches.

**Status - Rev A.** Design complete, DRC clean, full design review passed, boards on order (PCBWay, ENIG). This revision has **not been assembled or tested**, and the C firmware targeting it is **not yet written**. Treat the design as unvalidated.

Designed in KiCad 10.

---

## Features

- Bare **RP2040** (not a Pico module) with 16 MB **W25Q128** QSPI flash and a 12 MHz crystal
- **17 hot-swap keys** on Kailh MX sockets (CPG151101S11), any MX-style switch
- **Per-key RGB** - 17x SK6812MINI-E, 5 V data driven through a 74AHCT1G125 level buffer
- **Rotary encoder** with push (EC11), push mapped into the key matrix
- **16x2 HD44780 LCD** over I2C (PCF8574 backpack), driven through an onboard level shifter and connected via header
- **USB-C** with dual 5.1 kΩ CC pulldowns, USBLC6-2SC6 ESD protection, and 27 Ω series termination
- 5x4 diode-per-key matrix, one 1N4148 per switch (cathode to row)
- BOOTSEL and RESET buttons, SWD header
- Screws enter from either face (bottom edge extended so the lower mounting holes clear the 0 and Enter keycaps)

---

## Repository layout

```
hardware/numpad/
├── README.md              this file
├── numpad.kicad_pro       KiCad project
├── numpad.kicad_prl       project-local settings (editor state, netclasses)
├── numpad.kicad_pcb       board
├── numpad.kicad_sch       schematic, root sheet
├── 01_power_usb.kicad_sch   USB-C, ESD, 3.3 V regulator
├── 02_mcu.kicad_sch         RP2040, QSPI flash, crystal, SWD, BOOTSEL/RESET
├── 03_matrix.kicad_sch      key matrix and rotary encoder
├── 04_rgb.kicad_sch         SK6812 chain and 74AHCT1G125 buffer
├── 05_ui.kicad_sch          LCD header and I2C level shifters
├── fp-lib-table             footprint library table
├── sym-lib-table            symbol library table
├── lib/                     marbastlib (git submodule) - MX symbols/footprints, Kailh sockets
├── plot-files/              raw KiCad plot output - all gerbers, both drill maps, DXF, drills
├── numpad-revA-fab/         cleaned fab set actually sent to the fab (12 files)
├── numpad-revA-fab.zip      the same set, zipped for upload
├── step-files/              board STEP for the enclosure model
└── W1129510AS1K1.pdf        PCBWay order document (rev A)
```


`lib/` is a submodule. After cloning:

```
git submodule update --init --recursive
```

---

## Board summary

| | |
|---|---|
| Size | 83 x 127 mm |
| Layers | 2 |
| Thickness | 1.6 mm |
| Copper | 1 oz |
| Min track / clearance | 0.2 mm / 0.15 mm |
| Vias | 0.6 / 0.3 mm |
| Footprints | 120 |

Switches and RGB are surface-mount on the **back** copper (marbastlib MX footprints are authored for back-side placement). The USB/MCU/encoder strip sits along the top edge on the front.

---

## Key matrix

17 keys plus the encoder push occupy 18 positions in a 5x4 grid. The 2u-tall `+` and `Enter` keys each occupy one electrical cell, which frees `R2C3` for the encoder push.

| | C0 | C1 | C2 | C3 |
|----|-----|-----|-----|--------------|
| R0 | Num | /   | *   | -            |
| R1 | 7   | 8   | 9   | +            |
| R2 | 4   | 5   | 6   | Encoder push |
| R3 | 1   | 2   | 3   | -            |
| R4 | 0   | -   | .   | Enter        |

---

## Notable ICs and interfaces

| Ref | Part | Role |
|-----|------|------|
| U3  | RP2040 | MCU, QFN-56, 0.4 mm pitch |
| U4  | W25Q128JVS | 16 MB QSPI program flash |
| U1  | NCP1117-3.3 | 3.3 V LDO (see substitution note below) |
| U2  | USBLC6-2SC6 | USB ESD protection |
| U5  | 74AHCT1G125 | 3.3 V to 5 V buffer for the RGB data line |
| Q1/Q2 | BSS138 x2 | I2C level shift for the LCD header |
| J1  | USB-C receptacle (HRO TYPE-C-31-M-12) | Power and data |
| J2  | SWD header | Debug |
| J3  | LCD header | I2C to the character LCD |

Exact GPIO assignments live in the schematic and are the source of truth for firmware. Confirmed peripheral nets from the design: RGB data on **GP14**, encoder A/B on **GP7/GP8**. Verify the matrix and remaining pins against the schematic before writing firmware - they differ from the hand-wired build.

---

## Ordering this board

Fab specs, all inside a standard 2-layer capability:

- 2-layer, 1.6 mm FR-4, 1 oz copper
- 6/6 mil track/space class, 0.3 mm minimum drill
- **ENIG recommended.** The RP2040 is a 0.4 mm-pitch QFN-56 - a flat immersion-gold finish wets far more predictably under hand soldering than domed HASL

The board has **6 intentional plated slots** (4 at the USB-C connector shield legs, 2 at the encoder bracket), written as route-mode Excellon (`G00/M15/M16`) rather than G85. Most CAM tools read this correctly, but it is worth confirming in the fab's gerber viewer that they render as elongated openings.

---

## Assembly and bring-up

The board is designed for **staged bring-up**, not a single reflow:

1. Populate the USB-C and regulator section first. Scope the 3.3 V rail under a ~100 Ω load (AC-coupled, bandwidth-limited, short ground spring) and confirm it is not oscillating **before** placing the RP2040.
2. Then place the MCU, flash, and crystal, and confirm enumeration over USB.
3. Then the switch matrix, encoder, RGB chain, and LCD header.

Two part choices worth considering before assembly:

- **Regulator.** The board is drawn for the NCP1117ST33T3G, but C2 is a 10 µF MLCC that sits below that regulator's 0.25–2.2 Ω output-ESR floor. If the 3.3 V rail oscillates, swap in a ceramic-stable **TLV1117-33** (same SOT-223 pinout, drop-in). Recommend ordering both for cheap insurance.
- **Crystal.** 12 MHz 3225, chosen for **CL ≈ 18 pF** to match the 27 pF loading caps. Check the specific part's max ESR - higher-CL parts trend higher ESR, and higher CL also slows startup, which may need the pico-sdk XOSC startup multiplier raised. A CL = 10 pF part with 15 pF caps is a BOM-only fallback if either becomes a problem.

---

## Firmware

The hand-wired numpad in the repository root runs **CircuitPython** on a Pico W. That firmware does **not** port directly to this board - the pin map, the bare RP2040 boot flow, and RGB (vs single-color PWM) all differ.

The custom board is designed to run a **C firmware on pico-sdk + TinyUSB**. This is planned and not yet started.

---

## License

See the repository root for license terms.
