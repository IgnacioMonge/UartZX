# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project will follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from its first release.

## [Unreleased]

### Added

- Canonical divMMC/ZX-Uno driver (`src/divmmc.inc`), redesigned from the five
  application drivers and the ZX-Uno core HDL: lean status-only read,
  non-consuming `uart_ready`, bounded `uart_write` with a failure latch,
  fixed-length `uart_flush` and a bulk `uart_read_block`. No RX cache and no
  dependence on the interrupt setup. The feature ledger is in
  `docs/design.md`.
- Canonical Spectrum Next driver (`src/next.inc`), redesigned from the five
  application drivers and the Next core HDL: every status read keeps the
  receive error bits, reads refuse a damaged stream until `uart_flush`
  (`uart_read_raw` opts out), bounded `uart_write` with a failure latch,
  quiet-window `uart_flush` that reports when it gave up, bulk
  `uart_read_block`, `uart_set_baud` that
  never forces interrupts on, and `uart_esp_reset`. Baud tables for 9600,
  57600, 230400, 1152000 and 2000000 are optional 16-byte files
  (`src/next_baud_*.inc`).
- `src/send.inc`: `uart_write_string` and `uart_write_block` for every
  backend.
- Shared contract: a program that keeps its own fault latch may copy a
  failed `uart_write` into it and clear `uart_tx_failed`, taking over the
  rule that a command is never sent with holes, and on the Next move
  `uart_rx_errors` into it and read through `uart_read_raw`, taking over
  the rule that a damaged stream is never delivered (`docs/design.md`).
- Canonical AY bit-banged driver (`src/ay.inc`), redesigned from the
  NetManZX and BitStream drivers and the netman-zx original: unchanged bit
  timing, a known port level after `uart_init`, CTS raised on every exit,
  `uart_read` that preserves DE and HL, `uart_write` that preserves A and
  returns CF=0, `uart_ready`, `uart_flush` and `uart_read_block`. Init no
  longer waits or sends anything. A line that keeps pulsing after a byte can
  no longer hold `uart_read` with interrupts off, as it could in the
  application drivers. A byte the ESP starts just as a read gives up, up to
  1.5 bit times after CTS rises, is received; the NetManZX driver loses it or
  reads it from its middle on the next call.
- `src/ay_speed.inc` (optional): `uart_esp_set_9600` sends the persistent
  `AT+UART_DEF=9600,8,1,0,2` at 115200 baud, as the netman-zx original did.
  The counted loops in NetManZX and BitStream sent it at a rate no ESP reads.
- One source per backend for both sjasmplus and z88dk-z80asm, with code and
  state in separate files.
- Tests: both assemblers must produce the same bytes for each backend
  (`tests/test_assembly.py`), and the drivers run on the Python `z80`
  emulator: divMMC and Next against UART models built from the HDL
  (`tests/test_divmmc.py`, `tests/test_next.py`), AY against a model of the
  AY port with every bit placed in T-states (`tests/test_ay.py`). The README
  example is assembled and run too (`tests/test_readme.py`).
- The versions of the NetManZX, NetChessZX, BridgeZX, SpecTalkZX and
  BitStream drivers the design was compared against (`docs/sources.md`).
- MIT licence.
- Baseline review comparing the application drivers, checked against the
  Spectrum Next port documentation and the ZX-Uno UART HDL
  (`docs/baseline-review.md`).
- Project rule: every driver is Z80 assembly, with no driver logic in C.
