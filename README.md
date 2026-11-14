# UartZX

A shared UART driver, in Z80 assembly, for talking to an ESP Wi-Fi module
from a ZX Spectrum or a ZX Spectrum Next. One source tree, three hardware
backends, the same entry points on all of them.

> **Experimental.** Everything here has been tested in emulation only. No
> backend has run on real hardware yet, and no application uses this
> repository yet. Entry points and behaviour may still change. Do not ship it
> without testing it on your own machine.

## What it does

| Backend | Hardware | Files | Size |
|---|---|---|---|
| divMMC / ZX-Uno | ZX-Uno compatible UART (`#FC3B`/`#FD3B`): divTIESUS, ZX-Uno. 115200 baud, fixed by the hardware | `src/divmmc.inc`, `src/divmmc_vars.inc` | 157 bytes |
| Next | ZX Spectrum Next internal UART (`#133B`..`#163B`). 115200 baud by default | `src/next.inc`, `src/next_vars.inc`, optional `src/next_baud_*.inc` | 326 bytes |
| AY | Bit-banged serial on I/O port A of the AY-3-8912. 9600 baud | `src/ay.inc`, `src/ay_vars.inc`, optional `src/ay_speed.inc` | 367 bytes |

Every backend offers the same routines:

| Routine | In | Out |
|---|---|---|
| `uart_init` | | Driver ready |
| `uart_read` | | CF=1 and A = byte, or CF=0: nothing to read |
| `uart_ready` | | CF=1 when a byte is waiting; nothing is consumed |
| `uart_write` | A = byte | CF=0 sent, CF=1 not sent. Preserves A, BC, DE, HL |
| `uart_flush` | | Discards pending input |
| `uart_read_block` | HL = buffer, B = room | HL = next free position, D = room left, CF=1 buffer full |
| `uart_write_string` (`src/send.inc`) | HL = NUL-terminated text | CF=1 and HL at the unsent byte on failure |
| `uart_write_block` (`src/send.inc`) | HL = data, BC = length | CF=1, HL and BC at the unsent byte on failure |

The driver moves bytes. It does not speak AT commands, keep a ring buffer or
time anything out for you: that stays in the application.

The exact contract of each backend (registers destroyed, interrupts, error
handling, T-state costs) is in the header of its `.inc` file. Read it before
using a backend: they are not identical. In particular:

- **divMMC / ZX-Uno:** the receiver holds one byte, so the ESP must use CTS
  flow control (`AT+UART_CUR=115200,8,1,0,2`). A machine without the UART
  reads as an endless stream of `#FF` bytes: send first, and bound every
  receive loop.
- **Next:** receive errors (FIFO overflow, framing) stop `uart_read` until
  `uart_flush`, so a stream with a hole is never handed over as whole.
  `uart_set_baud` and the optional tables change the rate; `uart_esp_reset`
  pulses the ESP reset line.
- **AY:** sending and receiving run with interrupts off and return with them
  enabled; `uart_read` waits up to 3.9 ms; the code and the stack must be in
  uncontended memory; `uart_ready` only knows about a byte already captured.
  The ESP must already run at 9600 baud with CTS flow control.
  `uart_esp_set_9600` (`src/ay_speed.inc`) sets that, **persistently**: it
  rewrites the ESP's stored configuration, which then applies to every other
  interface the module is plugged into.

## Where it comes from

Five ZX Spectrum networking programs by the same author each carried their
own copy of the UART driver, and the copies had drifted apart:
[NetManZX](https://github.com/IgnacioMonge/NetManZX),
[BridgeZX](https://github.com/IgnacioMonge/BridgeZX),
[SpecTalkZX](https://github.com/IgnacioMonge/spectalkzx),
[BitStream](https://github.com/IgnacioMonge/BitStream) and NetChessZX. This
repository compares those drivers, keeps what each one does best and drops
the rest, so the programs can share one driver instead of five.

- NetManZX is based on [netman-zx](https://github.com/nihirash/netman-zx) by
  Alex Nihirash, which has an AY, a ZX-Uno and a Next driver, and the other
  programs' drivers share its register names and access sequences. The AY
  backend here still carries its timed bit loops almost unchanged; the
  divMMC and Next backends were redesigned from the HDL but keep that
  vocabulary.
- The hardware behaviour the drivers rely on was read from the published
  HDL: the [ZX-Uno core](https://github.com/mcleod-ideafix/zxuno_spectrum_core),
  the [divTIESUS CPLD](https://github.com/anarsoul/divtiesus_maple) and the
  [ZX Spectrum Next core](https://gitlab.com/SpectrumNext/ZX_Spectrum_Next_FPGA).
- `docs/design.md` records, per backend, every feature found in the
  application drivers, whether it was taken or dropped, and why.
  `docs/baseline-review.md` is the comparison that started it, and
  `docs/sources.md` lists the versions that were compared.

The design was written with the help of an AI coding assistant and reviewed
by two independent automated audits. That is not a substitute for running it
on hardware.

## How to use it

Copy `src/` into your project, or add this repository as a submodule.
Include one backend per build, plus `send.inc` if you want the string and
block helpers. Code and state are separate files so you decide where the
variables live.

sjasmplus (wrap it in a module; labels become `Uart.uart_read` and so on):

```asm
start:
    call Uart.uart_init
    call Uart.uart_flush        ; drop whatever the ESP said while booting
    ld hl, command
    call Uart.uart_write_string
    jr c, no_uart               ; divMMC and Next: the write timed out
    ld b, 0                     ; bound every wait yourself
wait:
    call Uart.uart_read         ; CF=1: A = byte
    jr c, got_byte
    djnz wait
no_uart:
    ret
got_byte:
    ret

command:
    defb "AT", 13, 10, 0

    module Uart
    include "UartZX/src/divmmc.inc"
    include "UartZX/src/send.inc"
    include "UartZX/src/divmmc_vars.inc"
    endmodule
```

z88dk-z80asm (the state files are zero-initialised, so they can go in bss):

```asm
    SECTION code_user
    PUBLIC uart_init, uart_read, uart_ready, uart_write, uart_flush
    PUBLIC uart_read_block, uart_write_string, uart_write_block
    INCLUDE "UartZX/src/next.inc"
    INCLUDE "UartZX/src/send.inc"
    SECTION bss_user
    INCLUDE "UartZX/src/next_vars.inc"
```

C programs call the assembly entry points through their own thin wrappers;
none are provided, because names and calling conventions differ per project.

## Rules of the source

- Every driver is Z80 assembly. No driver logic is written in C.
- One source for both assemblers. `src/` uses only the syntax sjasmplus and
  z88dk-z80asm share: global labels, `EQU`, `0x` literals, `defb`/`defw`. No
  local labels, macros, modules, sections or `#` literals.
- No routine uses IX or IY. Only the AY backend uses the alternate
  registers, and its header says where.

## Tests

```
python -m unittest discover tests
```

Needs `sjasmplus` and `z88dk-z80asm` on the PATH and the Python `z80`
package (`pip install z80==1.2.0`).

- `test_assembly.py`: both assemblers produce the same bytes for each
  backend.
- `test_divmmc.py`, `test_next.py`: each driver runs on a Z80 emulator
  against a UART model built from the HDL of its hardware. They check the
  register contract, T-state costs, the failure latches, error handling, and
  that no byte is written to a busy or full transmitter.
- `test_ay.py`: the AY driver against a model of the AY port and an ESP at
  9600 baud. Every bit sent is decoded and every bit received is sampled in
  emulated T-states, at the 48K and 128K clocks and with the ESP 2.5 % off.

The tests prove the drivers match those models. They do not prove the models
match your hardware.

## What is not proven

- Nothing has run on a real divTIESUS, ZX-Uno, Spectrum Next or AY
  interface.
- The divMMC model comes from the published HDL; a CPLD image in the field
  may differ.
- The Next tests do not model the wait states the machine adds at 28 MHz.
- `uart_read_block`, the Next quiet-window `uart_flush` and the restored
  timing of `uart_esp_set_9600` are new code that no application has run.
- z88dk has only been exercised through `z88dk-z80asm`, not a full `zcc`
  build.

## Repository layout

- `src/`: the drivers.
- `docs/design.md`: the shared contract and, per backend, the hardware model
  and the ledger of what was taken from each application driver.
- `docs/baseline-review.md`: the comparison of the application drivers.
- `docs/sources.md`: which versions of which programs were compared.
- `tests/`: assembly and emulation checks.

## Licence

[MIT](LICENSE): use it, change it and ship it in anything, keeping the
copyright notice.

One caveat on provenance: these drivers descend from netman-zx, whose
repository carried no licence file or statement on 2026-11-14. The lineage is
most direct in the AY backend's bit loops and is visible in the register
names and access sequences of the other two. The MIT licence here covers
this repository's own work; if that matters to you, look at that origin
before relying on it.
