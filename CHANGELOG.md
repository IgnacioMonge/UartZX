# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project will follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from its first release.

## [Unreleased]

### Added

- Standalone Next diagnostic NEX and a dependency-free host collector for
  unattended, run-matched TCP telemetry: RX loss/backpressure and verified TX
  patterns at 115200 and near 2 Mbaud, board-gated flow selection, bounded waits,
  structured logs, CPU/protocol and loopback tests, and operating instructions.
  One real Next run verifies lossless paused RX with flow at both rates,
  overflow in the no-flow controls and exact TX patterns in all four rows.
- Optional sustained-duplex probe build: timed phases at both Next UART rates,
  concurrent host traffic, incremental RX service during AT/TX waits, per-chunk
  integrity and overlap checks, and accumulated telemetry with bounded waits.
  Five minutes per rate on a real Next completed all 1492 chunks without
  integrity/UART errors, with RX/TX overlap measured in every chunk.
- One-launch Next validation battery with symmetric traffic, CPU and 50/60 Hz
  cases, planned fault/recovery checks and two-hour endurance; the collector
  validates the complete manifest, reconnects for planned TCP resets and keeps
  Windows awake temporarily. Integration guidance now distinguishes no-flow
  FIFO budgets from explicitly negotiated hardware flow.
  Reconnection requires fresh pattern data; missing/reordered AT notices remain
  diagnostic. Recoverable failed cases are retained while later cases run, and
  initial connection wait does not consume the active test budget.
  The completed hardware battery passes 44/44 cases, including all four CPU
  selections, both frame modes, injected recovery checks and over two hours of
  symmetric endurance with 169443328 B of verified payload.
- Ready-to-include Next duplex flow selection, with 115200/2 Mbaud support,
  immediate TX, bounded recovery and wire-idle query: 367 code/read-only bytes
  and two separately allocated state bytes. Both assemblers and a separate-object
  link verify the selection; individual leaves remain available.
- Optional Next `next_flow.inc`: `uart_flow_enable` / `uart_flow_disable`
  change only the ESP UART's hardware-flow bit and restore the previous
  ESP/Pi selection without rewriting divisors. The 33-byte leaf preserves
  interrupt state, adds no writable state, and keeps board/ESP negotiation
  in the caller. Tests cover frame/selection isolation, live traffic and
  error preservation, interrupts, both assemblers and independent linking.
- Optional Next `uart_tx_idle` query (13 code bytes) that checks final serial
  completion while retaining RX errors, and effective-rate metadata for both
  fast baud modes (32 read-only bytes), without new writable state or core cost.
- Detailed, version-pinned comparisons with NextSync, z88dk AY, UART.DRV/ESPAT,
  NXtel, newt and Moon Rabbit ZX, including local extracted-routine experiments
  and explicit limits on hardware, OS, parser and complete-build evidence.
- Optional Next `uart_flush_budget` with a caller-owned total sample budget,
  independent of the quiet window, and `uart_try_write` with one status sample
  and retryable busy distinct from the existing TX failure latch. Both retain
  RX error capture and interrupt state, cost 52/29 code bytes, add no state,
  and leave the core and compatible full Next include unchanged.
- Portable one-step RX example for all three backends, assembled and executed
  in tests; integration contracts cover AY cache-only readiness, interrupt
  behavior and the divMMC flush's fixed-poll guarantee.
- Selectable `<backend>_core.inc` and optional ready, flush and block-read
  includes, plus independent string/block send helpers and AY mixer-preserving
  initialization. Compatible duplex cores emit 82/192/403 code bytes and 1/2/3 state
  bytes for divMMC/Next/AY; omitted helpers emit no code or state. Bounded TX,
  fail-stop, RX error capture and Next's initial 115200 configuration remain
  mandatory. README tables and separate-object link tests record the costs.
- Optional Next reset assert/release controls without HALT or boot waits,
  preserving interrupt enable state; the compatible frame-wait reset shares
  them. The ESP reset also resets the expansion bus.
- Fast divMMC L-input entry preserving E/HL for Classic's real inline callers,
  with an optional A-input preserving adapter and one shared TX algorithm.
  The complete Classic specimen is 90 B and 150 T-states with TX ready,
  reuses its shared state byte and adds no TX stack use; it keeps ten frame
  waits and 512 bounded startup reads. Both assemblers and separate-object
  links verify the selections; instruction-level UART model tests cover
  registers, flags, stack, failure paths and one-shot disconnect effects.
- Assembly-time divMMC first-time TX failure destination, with a default
  state-1 latch. One shared-state check rejects later sends in states 1/2
  without I/O or repeated effects; Classic can jump directly to its existing
  disconnect/SNTP handler without a second gate or indirect call.
- Integration guide with tested SpecTalkZX fast/compatible adapters, shared TX
  failure state preserving 0/1/2 transitions and disconnect effects,
  application-owned raw RX recovery, and a Next RX/TX scheduling budget.
  Regressions reproduce overflow with 128 RX bytes pending during 512-byte
  TX and exercise bounded RX service between sends at several line/CPU rates.
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
  NetManZX and BitStream drivers and the netman-zx original: unchanged
  timing of the data bits, a known port level after `uart_init`, CTS raised on every exit,
  `uart_read` that preserves DE and HL, `uart_write` that preserves A and
  returns CF=0, `uart_ready`, `uart_flush` and `uart_read_block`. Init no
  longer waits or sends anything. A line that keeps pulsing after a byte can
  no longer hold `uart_read` with interrupts off, as it could in the
  application drivers. A byte the ESP starts just as a read gives up, up to
  1.5 bit times after CTS rises, is received; the NetManZX driver loses it or
  reads it from its middle on the next call.
- AY stop-bit check: a received byte whose stop bit reads low is dropped
  and latches `UART_RX_FRAMING` in `uart_rx_errors`, and `uart_read` then
  reports nothing until `uart_flush`, as on the Next (`uart_read_raw` opts
  out). A line held low no longer reads as a run of `#00` bytes.
- `uart_init_keep_mixer` (AY): sets only the port A bit of the mixer, so
  music keeps its tone and noise settings; `uart_init` still writes `#FC`.
- `src/next_write_block.inc` (optional): `uart_write_block_fast`, the Next
  block send with the same checks as `uart_write_block` in 101 T-states a
  byte instead of 218, counted without the Next's wait states: on those
  counts it outruns 2000000 baud at 28 MHz, where 218 does not.
- `src/ay_speed.inc` (optional): `uart_esp_set_9600` sends the persistent
  `AT+UART_DEF=9600,8,1,0,2` at 115200 baud, as the netman-zx original did.
  The counted loops in NetManZX and BitStream sent it at a rate no ESP reads.
- One source per backend for both sjasmplus and z88dk-z80asm, with code and
  state in separate files.
- Tests: both assemblers must produce the same bytes for each backend
  (`tests/test_assembly.py`), and the drivers run on the Python `z80`
  emulator: divMMC and Next against UART models built from the HDL
  (`tests/test_divmmc.py`, `tests/test_next.py`), AY against a model of the
  AY port with every bit placed in T-states (`tests/test_ay.py`). Both README
  examples are assembled and run too, the z88dk one with code and state
  linked to separate addresses (`tests/test_readme.py`). The emulator
  harness raises the frame interrupt every 69888 T-states and counts its
  acceptance, so the `uart_esp_reset` hold is checked against 25 real
  frames.
- Documented limits: on the Next, `uart_write_block` reads no input while it
  sends, so with flow disabled and the same rate both ways input is lost past
  575 bytes at 115200 baud and past 329 at 2000000 baud and 28 MHz; `uart_esp_reset` also
  resets the expansion bus; `docs/design.md` gives the worst rate error of
  each divisor table, 3.57 % at 2000000 baud over HDMI. On the AY, 9600 baud
  is the only rate the tests check.
- The versions of the NetManZX, NetChessZX, BridgeZX, SpecTalkZX and
  BitStream drivers the design was compared against, and the revisions of
  the Next, ZX-Uno and divTIESUS hardware sources the drivers were checked
  against (`docs/sources.md`).
- Provenance note in the README: the AY bit loops come from netman-zx,
  which publishes no licence, and no permission from its author is recorded.
- MIT licence.
- Baseline review comparing the application drivers, checked against the
  Spectrum Next port documentation and the ZX-Uno UART HDL
  (`docs/baseline-review.md`).
- Project rule: every driver is Z80 assembly, with no driver logic in C.

### Changed

- Existing backend and `send.inc` paths now compose the selected source
  leaves with the same entry points and transport ABI. z88dk full-include
  builds must add the UartZX `src` directory to their include search path.
  No consumer is migrated automatically.
- Full Next code grows by 10 bytes to share interrupt-state-preserving reset
  controls (339 code + 2 state bytes); minimal builds omit all reset code.
  Next read/write instruction costs are unchanged. AY full code is 478 bytes;
  its core and timed bit loops are unchanged.
- Compatible divMMC core/full includes retain all public entries and register
  contracts through the optional A adapter. Sharing the L-input algorithm
  costs 7 additional code bytes, 37 T-states and 2 temporary stack bytes for
  compatible A-input TX (82/163 B code, 235 T-states, 6 B internal stack).
  Fast Classic omits this adapter and replaces the previous 116 B/256 T
  integration with 90 B/150 T. The default nonzero failure value is now 1
  instead of 0x41; existing nonzero states remain unchanged on later sends.

### Fixed

- The quick Next probe rejects RX/TX lengths that differ from its fixed
  workload and records the received bytes on a TX mismatch. All probe
  modes send DONE before shutting down the ESP server, since some firmware
  closes the active connection during shutdown. The collector requires both
  end of stream and a refused connection to the diagnostic port.
- An incomplete AY flush now latches `UART_RX_RECOVERY` (`0x80`), clears its
  second-byte cache and blocks normal RX until a later flush completes or
  initialization clears it; clean over-budget streams and stuck-low lines
  both retain the recovery requirement.
- AY test model now applies R7's port direction to TX/CTS and RX reads,
  including latch writes during input mode and direction changes by music.
  Regressions cover these cases, Next recovery under drip input, and RX
  service during stalled TX using the immediate write primitive.
