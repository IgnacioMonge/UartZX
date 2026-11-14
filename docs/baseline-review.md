# Baseline review (2026-11-14)

Compares the application UART drivers to choose the base of the canonical
driver. Scope: NetManZX, NetChessZX and BridgeZXadv at the commits in
`sources.md`. Source reading plus two external hardware references;
nothing was assembled, executed or run on hardware for this review.

## Decisions taken (2026-11-14)

- **Base:** BridgeZXadv for Next and divMMC, NetManZX for AY, as proposed
  below. `src/` started as byte-identical copies of them; each backend is
  then redesigned from all the application drivers, as recorded in
  `design.md`.
- **Dialect:** one source in the syntax sjasmplus and z88dk-z80asm share,
  checked by `tests/test_assembly.py`. Both assemblers produce the same bytes
  for the three backends, including with the z88dk code and state in separate
  sections.
- **Assembly only:** no driver logic in C.

The licence (MIT) and publication were settled later. NetManZX's Next and
ZX-Uno drivers and NetChessZX's drivers were the source of features to port;
they are not in `src/`.

## Proposed base

**BridgeZXadv `next.asm` and `divmmc.asm`**, with features ported from the
other two. This is a proposal: the base and the open decisions below are the
maintainer's call.

Why BridgeZXadv:

- NetManZX is ruled out as the Next and divMMC base because its Next driver is
  not extractable: it calls into `Wifi.*` and shares `Uart.io_error` with the
  application. BridgeZXadv and NetChessZX are both self-contained and both
  expose one contract across their two backends, so the choice is between
  those two.
- Against NetChessZX, BridgeZXadv is the feature superset: 2,000,000 baud, a
  non-consuming `uartReady`, the RX cache that protects a pending byte during
  a TX wait, and a status macro its consumer inlines in the receive loop. Its
  divMMC file states it was "hardened after NetChessZX/Spectalk".
- It is the only one with a recorded physical gate under sustained load:
  2 MiB transfers on a Spectrum Next in FAST mode (which its source maps to
  2,000,000 baud) and on a divTIESUS (BridgeZXadv
  `docs/history/next-uart-2026-09-07.md`, `classic-uart-2026-09-11.md`). Those
  gates cover one Next and one divTIESUS; the divisor in use, video timing and
  core version were not recorded. NetChessZX's handoff records a basic
  hardware pass on 2026-09-07 with bursts, CTS and injected-error recovery
  still pending; NetManZX's verification notes claim no hardware result.
- Its contract (CF results, sjasmplus modules) is the one NetManZX already
  uses, so two of the three applications adopt it without an ABI change.

The assembler is the weak point of this choice. Counting SpecTalkZX and
BitStream, three of the five applications build with z88dk, as NetChessZX
does. If the canonical text ends up in z88dk dialect (open decision 2), the
NetChessZX files are the closer starting text and BridgeZXadv's behaviour is
ported onto them instead.

What it lacks, and where to take it from:

| Missing in the base | Take from |
|---|---|
| AY bit-banged backend | NetManZX `ay.asm` (the only one of the three) |
| Baud tables for 9600, 57600 and 1,152,000; baud scan and recovery after an ESP reset | NetManZX `next.asm`, with its `Wifi.*` calls replaced by a caller-supplied probe |
| Read timeouts counted in 50 Hz frames, independent of CPU speed | NetManZX `uart-common.asm` (`poll_block`), as an optional layer |
| z88dk/SDCC ABI (`_net_uart_*`, fastcall, result in L) | NetChessZX |
| 50/60 Hz detection (NextReg `#05` bit 2); `IM 1` before the reset wait | NetChessZX `next_uart.asm` |

Its age is the main caveat: the BridgeZXadv drivers last changed on 2026-09-07
(`next.asm`) and 2026-07-30 (`divmmc.asm`); NetManZX's changed up to
2026-11-11. The newer NetManZX work is in the table above, not in the base.

## Comparison

### Next

| | NetManZX | NetChessZX | BridgeZXadv |
|---|---|---|---|
| Assembler | sjasmplus | z88dk | sjasmplus |
| Self-contained | No (`Wifi.*`, `Uart.io_error`) | Yes | Yes (`enableTurbo` is app policy) |
| Init | Select ESP, reset, 8N1, 115200 | Same | Same, plus a bounded drain (10 frames, then up to 256 bytes) |
| Baud rates | 115200, 57600, 9600, 1,152,000, 2,000,000, with scan | 115200, 230400 (half the 115200 divisor) | 115200, 230400, 2,000,000 |
| Timing index (NextReg `#11`) | `and 7` | Clamp to 0 when >= 8 | Clamp to 0 when >= 8 |
| RX error bits | `#64`, last value stored in the shared `Uart.io_error` | `#64`, ORed into a sticky byte | `#44`, ORed into a sticky byte, inline macro |
| TX wait | 4096 polls | 256 polls, caller retries | 256 polls |
| Read API | `uartRead` (CF) | `ready` and `read` (L) | `uartRead` (CF) and `uartReady` peek with a 1-byte cache |
| ESP reset | 25-frame pulse, then 150 frames and baud recovery | `IM 1`, 25-frame pulse, 180-frame wait | 25-frame pulse; the caller waits |
| Evidence in its repository | Tests that execute the assembled binary (`tests/audit_regression.py`, Python `z80`); no hardware result claimed | Host C test of the wrapper layer against a mocked driver; emulator canary for the RX ring; basic hardware pass on 2026-09-07 | Hardware gate records under sustained load; tests that execute the assembled binary |

### divMMC / ZX-Uno

| | NetManZX `zxuno.asm` | NetChessZX | BridgeZXadv |
|---|---|---|---|
| RX byte pending when TX starts | Read and cached | Not handled: assumes a status read never consumes RX-ready | Read and cached |
| TX wait | 4096 polls, sets `Uart.io_error` | 256 polls, returns L=1 | 256 polls, returns CF=1 |
| Init drain | 50 frames, then 2048 polls | None (the caller flushes) | 10 frames, then up to 256 bytes |
| Ready without consuming | No | Status-only | `uartReady` (caches the byte) |

### AY

Only NetManZX has it: bit-banged through the AY I/O port with interrupts off
for each byte, 9600 baud by default (`_baud`), and a second-byte capture after
each read. It becomes the AY base as it is.

## Hardware facts checked

- **Next UART ports** (official `ports.txt`, ZX Spectrum Next FPGA repository).
  Status `#133B`: bit 0 RX has bytes, bit 1 TX full, bit 2 RX overflow (clears
  on read), bit 3 RX near full, bit 4 TX empty, bit 5 "next Rx byte was
  received after an error condition", bit 6 framing error (clears on read),
  bit 7 break. Frame `#163B` bit 5 enables hardware flow control; "the esp only
  honours flow control on an issue 4 board".
- **Next UART clock** (official `nextreg.txt`, NextReg `#11`): indices 0..6 are
  VGA settings at 28000000, 28571429, 29464286, 30000000, 31000000, 32000000
  and 33000000 Hz; index 7 is HDMI at 27000000 Hz. Bits 7:3 are reserved zero.
  `prescaler = Fsys / baud`.
- **ZX-Uno UART** (ZX-Uno core `common/zxunouart.v` and `uart.v`; the open
  divTIESUS CPLD `divtiesus_maple` uses the same decode). A status read has no
  side effect, the receiver holds one byte with RTS raised until the data
  register is read, a data write while TX is busy is dropped, and TX has no
  flow control input. Details and limits in `design.md`.

## Contradictions between the drivers

These were the first backlog. `design.md` records how each was settled for
each backend; hardware flow control on the Next (item 8) is open.

1. **2,000,000 baud table.** NetManZX has `14,14,15,15,15,16,16,13`,
   BridgeZXadv `14,14,15,15,16,16,17,14`. Three clocks fall exactly between
   two divisors. Against the documented clocks BridgeZXadv's values are the
   nearest (worst error 3.57 % against 3.85 %) and are the ones behind its
   FAST-mode hardware gate. Every other table in the three drivers is the nearest divisor (worst
   error 0.39 %; 1.90 % at 1,152,000).
2. **RX error mask.** `#64` (NetManZX, NetChessZX) includes status bit 5;
   `#44` (BridgeZXadv) does not. Bit 5 is not documented as clear-on-read, so
   it may keep a fault latched for as long as tainted bytes stay in the FIFO.
   Needs a hardware check.
3. **divMMC RX model** (resolved by the HDL, see `design.md`: no cache).
   NetChessZX polls status freely and keeps no cache;
   the other two cache the pending byte before a TX wait. The cache is the
   safer model; the cache-free one is only correct where a status read is
   non-destructive. Even the cached drivers test RX only once,
   before the TX busy loop: on hardware that clears RX-ready on a status read,
   a byte completing during that loop would still be lost.
4. **TX wait length** (divMMC resolved in `design.md`: 256). 4096 polls (NetManZX, sized for a 9600 baud byte at
   28 MHz) against 256 (the others, with NetChessZX retrying in its C layer).
   256 polls are shorter than one 9600 baud byte at 28 MHz.
5. **Fault ownership.** NetManZX keeps one `io_error` byte outside the driver
   and overwrites it; the other two keep a sticky byte inside the driver.
6. **Timing index.** `and 7` against a clamp. Both are safe for the documented
   3-bit field. NetManZX's table comment gives a wrong index order ("ZX48,
   ZX128, Pentagon, +2A/+3, HDMI, VGA0..2"); its values are right.
7. **Init drain and reset wait** (divMMC drain resolved in `design.md`).
   Three different drain policies and three
   post-reset waits (150 frames, 180 frames, caller-owned).
8. **Hardware flow control.** No driver sets frame bit 5. BridgeZXadv's
   history records an RX overflow in FAST mode that was fixed by shortening
   the receive loop. Worth evaluating on issue 4 boards.

## Cost and robustness against NetChessZX and SpecTalkZX

Added after the base was copied. T-states are counted by hand from the
sources (from routine entry to return, no Next wait states); sizes come from
assembling each file, with stubs for SpecTalkZX's externals.

| | BridgeZXadv (base) | NetChessZX | SpecTalkZX |
|---|---|---|---|
| divMMC: read, nothing pending | 89 T | 60 T | 60 T |
| divMMC: read, byte returned | 132 T | 103 T | 103 T |
| divMMC: size | 195 bytes | 96 bytes | 101 bytes |
| Next: read, nothing pending | 117 T | 80 T | 99 T |
| Next: size | 360 bytes | 239 bytes | 267 bytes |

The base is the largest and has the slowest callable read on both backends.
The sizes are not like for like: the base carries the RX cache, `uart_ready`,
three baud tables, turbo and the reset pulse. Its transfer speed comes from
the receive loop BridgeZXadv inlines in `net.asm`, not from `uart_read`.

- **divMMC.** The 29 T difference is the RX cache check. NetChessZX and
  SpecTalkZX keep no cache; SpecTalkZX states why: RTS holds the ESP while a
  byte is pending. That matches the divTIESUS HDL above, and both run on the
  maintainer's divTIESUS. The cache only earns its cost on hardware where a
  status read clears RX-ready, which is still unverified for the ZX-Uno core.
- **Next, error policy.** The base and NetChessZX latch a fault and refuse
  reads until a reset or flush. SpecTalkZX does the opposite on purpose: it
  marks a gap in its ring, drains the FIFO and resumes, because a latch "muted
  all reception during MOTD/NAMES bursts". A file transfer wants the first
  policy and a text stream the second, so the policy belongs to the
  application, not to the read path.
- **Next, error mask.** SpecTalkZX uses `#E4` (adds break, bit 7), a third
  value next to `#64` and `#44`.
- **Only in SpecTalkZX:** RX is pumped into its ring during a TX wait; a TX
  timeout latches and blocks later sends until reinit; the init flush reads a
  fixed 512 times instead of stopping at the first empty poll. Its Next driver
  depends on seven application symbols, so none of this is extractable as is.

Backlog from this check: make the divMMC cache optional once the ZX-Uno core
is verified; give the Next backend a lean raw read with the error policy
layered on top; decide on the TX failure latch and the fixed-length flush.

## Constraints on the canonical design

- **Assembly only.** Every driver is Z80 assembly (maintainer rule). The
  three applications already comply at the port level. The exception is
  NetChessZX, whose RX ring, flush and send retries live in C
  (`src/spectrum/platform/platform.c`): any of that adopted here is written in
  assembly. SpecTalkZX already has an assembly RX ring
  (`asm/spectalk_asm/20_rx_ring_uart.asm`, not reviewed). The z88dk consumers
  get assembly entry points with a C-callable ABI, not C code.
- **Two assemblers.** NetManZX and BridgeZXadv build with sjasmplus;
  NetChessZX, SpecTalkZX and BitStream with z88dk. A single source has to
  serve both.
- **Consumers inline the hot path.** BridgeZXadv's `net.asm` reads
  `Uart._is_recv`, `_rx_byte`, `_rx_errors`, the register constants and the
  `READ_STATUS` macro directly in its payload loops (its Next loop costs
  165 T per byte by its own trace). The canonical driver must expose its
  constants and state, not only callable routines. `src/` has no macros, so
  `next.inc` documents the four-instruction status read for a consumer to
  inline; BridgeZXadv would keep its `READ_STATUS` macro on its own side.
- **Test harness.** The Python `z80` package (1.2.0) already drives assembled
  binaries in NetManZX (`tests/audit_regression.py`), BridgeZXadv
  (`server/tests/z80_control.py`) and BitStream (`tools/test_uart_cpu.py`).
  Reuse it here.

## Open decisions

1. Licence of this repository (sources are MIT and GPL). Settled: MIT.
2. Review of the SpecTalkZX and BitStream drivers. Done in `design.md`.
   BitStream has a second AY implementation.

## Found while copying

- **AY init sends a persistent baud command at a non-standard rate.**
  `uart_set_speed` sends `AT+UART_DEF=9600,8,1,0,2` on every `uart_init`.
  `UART_DEF` is the persistent form: if the ESP accepts it, each start writes
  its flash and leaves it at 9600 baud with CTS flow control for whatever runs
  next. The loop takes 56 T-states per bit, which is 62,500 baud at 3.5 MHz:
  no rate an ESP listens at. The upstream driver (netman-zx) sends the same
  table with unrolled code at 30 T-states per bit, which is 115200 baud; the
  counted loop has been in NetManZX since its first commit, and BitStream has
  one of 65 T-states. Settled in `design.md`: the command moved to the
  optional `ay_speed.inc` with the upstream timing.
