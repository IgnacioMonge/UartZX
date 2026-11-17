# UartZX

A shared UART driver, in Z80 assembly, for talking to an ESP Wi-Fi module
from a ZX Spectrum or a ZX Spectrum Next. One source tree, three hardware
backends, the same entry points on all of them.

## What it does

| Backend | Hardware | Minimal code | Compatible full code | State |
|---|---|---:|---:|---:|
| divMMC / ZX-Uno | ZX-Uno compatible UART (`#FC3B`/`#FD3B`): divTIESUS, ZX-Uno. Fixed 115200 baud | 82 B | 163 B | 1 B |
| Next | Internal UART (`#133B`..`#163B`), initially 115200 baud | 192 B | 339 B | 2 B |
| AY | Serial on AY-3-8912 port A, 9600 baud | 403 B | 478 B | 3 B |

Minimal means `src/<backend>_core.inc`; full means the existing
`src/<backend>.inc`. Both need `src/<backend>_vars.inc` in RAM, or equivalent
application-owned storage. These are measured emitted code and state sizes,
verified with both assemblers and a z88dk link with code and BSS apart.
Send helpers and other optional files are additional; see the costs below.

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

The core supplies `uart_init`, `uart_read`, `uart_read_raw` and `uart_write`.
The other routines are selected through optional includes. TX timeouts,
fail-stop and receive error capture are always part of the core.

The driver moves bytes. AT commands, ring buffers, receive deadlines and
recovery policy stay in the application.

The exact contract of each backend (registers destroyed, interrupts, error
handling, T-state costs) is in the header of its `_core.inc` file. Read it before
using a backend: they are not identical. In particular:

- **divMMC / ZX-Uno:** the receiver holds one byte, so the ESP must use CTS
  flow control (`AT+UART_CUR=115200,8,1,0,2`). A machine without the UART
  reads as an endless stream of `#FF` bytes: send first, and bound every
  receive loop.
- **Next:** receive errors (FIFO overflow, framing) stop `uart_read` until
  `uart_flush`, so a stream with a hole is never handed over as whole.
  `uart_set_baud` and the optional tables change the rate; `uart_esp_reset`
  pulses the reset line of the ESP, which also resets the expansion bus.
  Optional `next_reset.inc` offers assert/release without HALT or boot waits,
  preserving the interrupt enable state for application-owned waits.
  Optional `next_flush_budget.inc` bounds recovery by a total status-sample
  budget; `next_try_write.inc` attempts TX once so the application can service
  RX even while TX stays full. Neither changes the compatible full include.
  `next_tx_idle.inc` queries final serial completion; `next_baud_khz.inc`
  exposes the two fast modes' effective rates for application-owned ESP setup.
  Optional `next_flow.inc` controls ESP hardware flow after application-owned
  board checks and negotiation. It preserves the previous ESP/Pi selection;
  initialization still turns flow off.
  The [duplex flow selection](examples/next_flow_uart.inc) combines these
  optional controls with the core, recovery, TX idle and 2 Mbaud metadata/table:
  367 code/read-only bytes plus the two separately allocated state bytes.
  `uart_write_block` reads no input while it sends. With flow disabled, at the
  same rate both ways with both FIFOs initially empty, a send longer than 575 bytes can
  overflow the RX FIFO at 115200 baud, and a shorter one at faster rates
  (329 bytes at 2000000 baud and
  28 MHz). With 128 bytes already pending, even a 512-byte send overflows.
  Service RX before sending and between bounded parts; the
  [adoption guide](docs/integration.md#simultaneous-next-rx-and-tx) gives the
  occupancy/time budget and a tested schedule.
  `uart_write_block_fast` (`src/next_write_block.inc`, optional) does the
  same job with the same checks in 101 T-states a byte instead of 218,
  counted without the Next's wait states.
- **AY:** sending and receiving run with interrupts off and return with them
  enabled; `uart_read` waits up to 3.9 ms; the code and the stack must be in
  uncontended memory; `uart_ready` only knows about a byte already captured.
  A byte whose stop bit reads low stops reception until `uart_flush`, as a
  framing error does on the Next. `uart_init` rewrites the whole mixer
  register; `uart_init_keep_mixer` only makes port A an output, so music
  keeps its tone and noise settings. The ESP must already run at 9600 baud
  with CTS flow control.
  An incomplete `uart_flush` latches `UART_RX_RECOVERY` (`0x80`) and refuses
  normal reads until a flush completes or `uart_init` clears it.
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

## How to use it

For a maintained application, use a Git submodule pinned to a reviewed commit.
The [consumer dependency guide](docs/dependency-integration.md) covers setup,
reproducible clones, automatic update proposals, CI and rollback. Copying `src/`
is also supported, with synchronization owned by the application.
Include one backend per build, plus `send.inc` if you want both send helpers.
Code and state are separate files so you decide where the variables live.
The existing full includes retain their entry points and ABI. For z88dk,
add `-I UartZX/src` (or the absolute path to `src`) to the assembler command:
nested includes are resolved through its include search path. sjasmplus
resolves them relative to the including file. Keep the `src` directory intact.

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
    push bc                     ; uart_read destroys BC
    call Uart.uart_read         ; CF=1: A = byte
    pop bc
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

### Minimal compatible selections

For divMMC / ZX-Uno:

```asm
    include "UartZX/src/divmmc_core.inc"
    include "UartZX/src/divmmc_vars.inc"
```

For Next, including the mandatory 115200 initialization table and setter:

```asm
    include "UartZX/src/next_core.inc"
    include "UartZX/src/next_vars.inc"
```

For AY:

```asm
    include "UartZX/src/ay_core.inc"
    include "UartZX/src/ay_vars.inc"
```

For a divMMC consumer passing the byte in L, select `divmmc_fast.inc`
instead. `uart_write_fast` preserves E/HL, clobbers AF/BC/D, uses no internal
stack, and sends in 150 T-states with TX ready. Supply `uart_tx_fail` as an
assembly-time destination, or include `divmmc_tx_fail.inc` for the default
state-1 latch. Add `divmmc_write.inc` only when the A-input, register-preserving
`uart_write` API or the generic send helpers are needed. The existing
`divmmc_core.inc` and `divmmc.inc` include both leaves automatically.
The complete [Classic example](examples/classic_uart.inc) uses the application's
failure handler directly: **90 B code including initialization**, the existing
shared state byte, **zero internal TX stack and 150 T-states per ready send**.

Do not combine a full backend include with its core or leaves. Optional leaves
normally follow the core. The exception is `ay_init_keep_mixer.inc`: put it
immediately **before** `ay_core.inc`, so its relative jump reaches the shared
initializer without an extra byte or call. The full AY include does this already.

### Optional costs

Each row adds only the listed code/read-only bytes, **zero state** and no selection
overhead in the core. Constants emit no bytes. An omitted routine has no label
or machine code. The linker does not prune functions inside a selected object.

| Include / entry | divMMC | Next | AY |
|---|---:|---:|---:|
| `divmmc_write.inc` / compatible A-input `uart_write` | 12 B | — | — |
| `divmmc_tx_fail.inc` / default first-failure destination | 7 B | — | — |
| `<backend>_ready.inc` / `uart_ready` | 12 B | 20 B | 11 B |
| `<backend>_flush.inc` / `uart_flush` | 12 B | 43 B | 38 B |
| `<backend>_read_block.inc` / `uart_read_block` | 57 B | 49 B | 12 B |
| `send_string.inc` / `uart_write_string` | 10 B | 10 B | 10 B |
| `send_block.inc` / `uart_write_block` | 12 B | 12 B | 12 B |
| `send.inc` / both send helpers | 22 B | 22 B | 22 B |
| `next_reset.inc` / `uart_esp_reset_assert`, `uart_esp_reset_release` | — | 24 B | — |
| `next_esp_reset.inc` / legacy frame-wait reset, including `next_reset.inc` | — | 35 B | — |
| Each `next_baud_*.inc` optional table | — | 16 B | — |
| `next_write_block.inc` / `uart_write_block_fast` | — | 46 B | — |
| `next_try_write.inc` / `uart_try_write` | — | 29 B | — |
| `next_flush_budget.inc` / `uart_flush_budget` | — | 52 B | — |
| `next_tx_idle.inc` / `uart_tx_idle` | — | 13 B | — |
| `next_baud_khz.inc` / both fast-mode effective-rate tables | — | 32 B | — |
| `next_flow.inc` / `uart_flow_enable`, `uart_flow_disable` | — | 33 B | — |
| `ay_init_keep_mixer.inc` / `uart_init_keep_mixer` | — | — | 14 B |
| `ay_speed.inc` / persistent ESP rate change | — | — | 360 B |

`uart_set_baud` and `uart_baud_115200` stay in the Next core: `uart_init`
uses both. Omitting the other baud tables removes all their bytes. The AY
second-byte cache and error byte, Next RX error byte, and hardware-backend
TX failure byte are required by the duplex core, not by optional helpers.

| Configuration | divMMC code + state | Next code + state | AY code + state |
|---|---:|---:|---:|
| Minimal compatible duplex core | 82 + 1 B | 192 + 2 B | 403 + 3 B |
| Compatible full include | 163 + 1 B | 339 + 2 B | 478 + 3 B |
| Optional duplex flow selection | — | 367 + 2 B | — |
| Full + both send helpers + all applicable optional leaves/tables/speed | 185 + 1 B | 646 + 2 B | 860 + 3 B |

The full Next include is 10 code bytes larger than before this split: its
reset now shares assert/release controls that preserve IFF. The minimal core
omits those 35 bytes entirely. AY's full flush adds 5 bytes for its recovery
latch; the AY core and timed bit loops are unchanged. divMMC's
shared fast core is 63 B with an application failure destination, or 70 B with
the default destination; the compatible A adapter adds 12 B. This makes the
compatible core/full selections 7 B larger and A-input TX 235 T-states instead
of 198; the Classic L-input path omits that cost. Next read
remains 88/108 T-states empty/byte, raw read 66/86, and immediate write 152;
AY write remains 4233 T-states. These are model costs without physical contention
or Next wait states.

[Integration and SpecTalkZX adoption](docs/integration.md) covers fast L-input
and compatible A-input selection, before/after measurements, shared TX state,
application-owned RX recovery, concurrent Next traffic, reset waits and the
limits of these measurements.

## Rules of the source

- Every driver is Z80 assembly. No driver logic is written in C.
- One source for both assemblers. `src/` uses only the syntax sjasmplus and
  z88dk-z80asm share: global labels, `EQU`, `0x` literals, `defb`/`defw`. No
  local labels, macros, modules, sections or `#` literals.
- No routine uses IX or IY. Only the AY backend uses the alternate
  registers, and its header says where.

## Tests

For an unattended Next hardware run, see [the telemetry probe](docs/next-probe.md).
It builds a standalone NEX and collects run-matched RX/TX results over TCP.
Its `--soak` build runs sustained duplex traffic at both rates, recording
errors, exact payload checks, byte totals and receive/transmit overlap.
The [one-launch battery](docs/next-batch.md) combines clock/load combinations,
recovery checks and a two-hour endurance run in one unattended execution.
The completed hardware run passed 44/44 cases; the report records its exact
workload, injected faults, transferred bytes and coverage limits.

```
python -m unittest discover tests
```

Needs `sjasmplus` and `z88dk-z80asm` on the PATH and the Python `z80`
package (`pip install z80==1.2.0`). Last checked with sjasmplus 1.23.1 and
z88dk-z80asm build 24860-762578732c-20260630.

- `test_assembly.py`: both assemblers produce the same bytes for each
  backend.
- `test_divmmc.py`, `test_next.py`: each driver runs on a Z80 emulator
  against a UART model built from the HDL of its hardware. They check the
  register contract, T-state costs, the failure latches, error handling, and
  that no byte is written to a busy or full transmitter.
- `test_readme.py`: the full examples assemble and run, the z88dk one with
  code and state linked to separate addresses; all three minimal selections
  assemble with both tools.
- `test_modularity.py`: minimal/full configurations and independent optional
  leaves assemble with both tools and link from separate application/driver
  objects; emitted code, BSS size, omitted labels and mandatory safety are checked.
- `test_classic.py`: complete 90-byte Classic specimen and optional compatible
  A adapter assembled with both tools, with separate-object z88dk links. Real
  instructions check the 150/235 T-state paths, E/HL and compatible register
  contracts, carry, IFF, alternate registers, stack, one shared-state read,
  256-poll timeout, absent UART, effects once, states 1/2, partial strings and
  blocks, inline SNTP's E lifetime, and ten frame waits plus 512 startup reads.
- `test_next.py` also reproduces RX overflow with input already pending and
  exercises a bounded consumer schedule under simultaneous RX/TX.
- `test_ay.py`: the AY driver against a model of the AY port and an ESP at
  9600 baud. Every bit sent is decoded and every bit received is sampled in
  emulated T-states, at the 48K and 128K clocks and with the ESP 2.5 % off.
- `test_probe_cpu.py`, `test_probe_host.py`: the hardware probe's complete AT
  sequence, bounded reads, matrix/board selection and payloads on the CPU;
  fragmented TCP reports, run tokens, corruption and incomplete runs on the host.

## Repository layout

- `src/`: the drivers.
- `docs/design.md`: the shared contract and, per backend, the hardware model
  and the ledger of what was taken from each application driver.
- `docs/integration.md`: modular costs, ownership and SpecTalkZX adoption.
- `examples/classic_uart.inc`: minimal Classic module with shared failure handling.
- `docs/baseline-review.md`: the comparison of the application drivers.
- `docs/sources.md`: which versions of which programs were compared.
- `tests/`: assembly and emulation checks.

## Licence

[MIT](LICENSE): use it, change it and ship it in anything, keeping the
copyright notice.

One exception to check before relying on that: the timed bit loops in
`src/ay_core.inc` come, almost unchanged, from
[netman-zx](https://github.com/nihirash/netman-zx) by Alex Nihirash, through
NetManZX. netman-zx publishes no licence: on 2026-11-16 it had no licence
file, and its latest commit, `9cd6173`, is the one compared here. No licence
or permission from its author for those loops is recorded in this
repository.
