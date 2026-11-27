# Integration and SpecTalkZX adoption

UartZX owns register access, bounded TX, fail-stop and RX error capture.
The application owns scheduling, buffers, framing, recovery and disconnect
effects. Selecting smaller includes does not change that boundary.

For repository setup and explicit upstream update checks, use the
[pinned dependency guide](dependency-integration.md). The application owns
adoption and validation of each new UartZX revision.

## Measured scope — 2026-11-17

This section is the record of that day's measurements and is kept as it was.
A size pass on 2026-11-22 changed the bytes of every selection below without
changing entry points, contracts or port accesses: the Classic specimen is now
89 B and 147 T-states, the compatible divMMC core 78 B and 232 T-states. The
current figures are in the [README](../README.md#optional-costs) and in the
sections that follow this one.

These UartZX measurements started from `014d21d` and used the following
consumer snapshots read-only:

- Original Classic: `C:/dev/SpecTalkZX dev`, reference `2140e6b`.
- Previous integration snapshot: `C:/Users/ignac/.codex/worktrees/uartzx-evaluation/SpecTalkZX dev`,
  branch `codex/uartzx-classic`, including its uncommitted work.
- Real callers: `src/clock_classic.c`, `asm/spectalk_asm/40_text_numeric_screen.asm`,
  `src/net_classic.c`, `src/spectalk.c`, and the integration's compiler listings
  in `build/evidence/main_build.c.lis` and `spectalk_asm.asm.lis`.

The inline SNTP sequence stores the second decimal digit in E across the
first send. The string and compiled C callers also keep pointers in HL.
The fast contract therefore preserves **E and HL**; a C ABI alone would miss
these requirements. BC and D are scratch in the original Classic writer.

Both original and integrated UART modules were freshly assembled in temporary
files, with their original instructions unchanged and only object directives
adapted to the common syntax. Both assemblers emitted identical bytes. The
measurements below execute those bytes against the UART model: status is read,
the requested byte (`0x5A`) is transmitted, and pending RX is not consumed.
These before values are reproduced measurements, not timings copied from notes.

| Selection | UART code | Persistent UART state | Internal TX stack | TX ready |
|---|---:|---:|---:|---:|
| Original complete Classic module, `2140e6b` | 101 B | 1 B shared | 0 B | 150 T |
| Prior complete Classic integration, `014d21d` core | 116 B | 1 B shared | 6 B | 256 T |
| New complete Classic, fast L entry | **90 B** | **same 1 B shared** | **0 B** | **150 T** |
| New Classic + optional compatible A adapter | 102 B | same 1 B shared | L: 0 B; A: 6 B | L: 150 T; A: 235 T |
| Previous generic compatible divMMC core | 75 B | 1 B | 4 B | 198 T |
| New generic compatible divMMC core | 82 B | 1 B | 6 B | 235 T |
| New generic compatible full divMMC include | 163 B | 1 B | 6 B | 235 T |
| New fast divMMC core + default failure destination | 70 B | 1 B | 0 B | 150 T |
| New fast divMMC core + external failure destination | 63 B | 1 B shared | 0 B | 150 T |

Classic's **90 B = 63 B init/read/fast-send core + 27 B initialization wrapper**.
The L entry is an EQU alias and emits zero adapter bytes. Count all of these
bytes even if some labels are unreferenced. The only excluded dependencies
are those already external to the original module: **`_frame_wait`,
`_uart_tx_fail`, and the shared byte `_uart_tx_failed`**. State is shown
separately in the table; it is reused, never allocated a second time.
Test callers, test instrumentation and external handler implementations are
not UART module code. The optional A adapter is deliberately absent from the
minimal Classic selection; linking it costs 12 B and is shown separately.

Cycles run from the entry of `_ay_uart_send` to its return, excluding the
caller's CALL, with TX ready on the first status sample. The A rows measure
`uart_write` by the same rule. Stack excludes the caller's two-byte return
address; it records the deepest internal use, also observed at port I/O.
The fast path makes no stack writes. The former 192-sample Classic timeout
becomes 256 samples; it remains bounded. All rows use plain Z80 timing without
contention, interrupts occurring inside TX, physical UART latency or Next waits.

`tests/test_classic.py` compares sjasmplus and z88dk-z80asm bytes, then links
separate caller, UART and external-effects objects with code at `0x8100`,
effects at `0x9000` and BSS at `0xC000`. The minimal UART section is exactly
90 B plus the 10 B test caller; the compatible selection is 102 B plus that
caller. Generic compatible selections and every other backend also have
separate-object link checks in `tests/test_modularity.py`. No selected code
is assumed to disappear through dead-code elimination.

Reference SHA-256 (files read without modification):

- Original module: `07aa20307cf4c319c116ddcd38dbf808ab35852076626be6237be53d005510e0`.
- Integration adapter: `1fc8230ce892749462506de7f24bf373aa2ca316278d31f10a895048f905a1cc`.
- Integration vendored core: `9eb8b1a1b48dbbae8e19c96230c31a91e905471cc011f46b50478e5a708651b5`.

Tools: sjasmplus 1.23.1, z88dk-z80asm 24860-762578732c-20260630,
Python 3.11 and the `z80` emulator. GitHub tags and releases were checked:
none published, so the changelog tracks the first release under Unreleased.

Verification from the UartZX root:

```powershell
& 'C:/Program Files/Python311/python.exe' -m unittest discover -s tests -v
```

Result: **69 tests pass**, covering all three backends, compatible examples,
selected sizes/links and the Classic regressions. `git diff --check` passes.
During those library measurements, both consumer Git statuses and the
three reference hashes above remained unchanged.

**HW PENDING.** The tests model UART ports and frame waits. They do not
validate real CTS/ESP timing, contention, interrupt/NMI stack high-water or
the guarded initial read on physical hardware. The initial library
measurements did not rebuild, migrate, deliver or push a full consumer; final
resident/overlay guards must be checked when a consumer adopts the change. The earlier Next
specimen remains 233 B versus its original 267 B module, but is not a complete
replacement: RX service, gap marking and recovery still need integration.

## Includes, ABI and placement

The compatible `<backend>_core.inc`, `<backend>.inc` and `send.inc` retain their
public entries and register contracts. divMMC has one transmission algorithm,
kept in two files that a test holds byte for byte alike: `divmmc_fast.inc`
ends in the jump to the failure destination, `divmmc_fast_open.inc` runs
into whatever follows it. Neither includes another file, so the fast
selection still builds without an include path. The compatible core
composes the open one with the default destination and the optional
preserving A adapter. There are no runtime
options, indirect calls or preprocessor feature combinations.

| divMMC file | Supplies | Code |
|---|---|---:|
| `divmmc_fast_open.inc` | `uart_init`, `uart_read`, `uart_read_raw`, `uart_write_fast` (L); a TX timeout runs into the next include | 59 B |
| `divmmc_fast.inc` | The same, closed with `jp uart_tx_fail` | 62 B |
| `divmmc_tx_fail.inc` | Default `uart_tx_fail`: latch 1, CF=1 | 7 B |
| `divmmc_write.inc` | `uart_write` (A), preserves A/BC/DE/HL | 12 B |
| `divmmc_core.inc` | Open core, default destination and A adapter, same existing API | 78 B |
| `divmmc.inc` | Compatible core + ready/flush/read-block | 158 B |

Choose `divmmc_fast.inc` plus **one** failure destination: either define
`uart_tx_fail EQU application_handler`, or include `divmmc_tx_fail.inc`.
For the default destination, `divmmc_fast_open.inc` with `divmmc_tx_fail.inc`
as the very next include saves the 3-byte jump (66 B in all). Nothing else
may come between the two: after 256 busy samples the open core executes
whatever follows it.
Include `divmmc_write.inc` only if an A-input caller or the generic string/block
helpers needs it. The L input is native, so an L caller needs no adapter.
The fast selection alone intentionally has no `uart_write` label. Both
`divmmc_core.inc` and `divmmc.inc` include that label and its compatible
contract automatically. The +3 B/+34 T/+2 B stack tradeoff on compatible
A-input writes, against the earlier 75 B core, buys a single shared algorithm
with a zero-overhead Classic L entry. Read costs remain 60/103 T-states empty/byte.

For z88dk, pass `-I <UartZX>/src` for nested includes, including the compatible
divMMC core. Use consumer-owned SECTION/PUBLIC/EXTERN declarations; shared
source leaves contain none. Keep one symbol scope per driver and keep code
and state resident and mapped while called. State must be writable RAM and
initialized on cold start or through `uart_init`; AY cache bytes stay adjacent.
Both AY initializers leave R14 selected for direct port A access.

No core uses IX or IY. divMMC and Next preserve IFF; AY timed I/O returns with
interrupts enabled and still needs uncontended code/stack. Drivers are not
reentrant: an interrupt handler must not operate the same UART/AY registers
or change required banks. Next init still selects the ESP, resets FIFOs,
selects 8N1 and programs 115200; its setter and table remain core dependencies.

## Classic fast contract and failure ownership

`uart_write_fast` takes L. It destroys AF/BC/D, preserves E/HL/IX/IY, alternate
registers and IFF, and uses only the existing return address. Only CF is a
result: 0=sent, 1=not sent. Other flags are unspecified. The byte is written
from L after a fresh not-busy sample; TX never reads RX.

The first operation reads the shared failure byte once. Any nonzero value,
including 1=pending report and 2=already reported, returns CF=1 before port
access. After 256 busy samples, the core tail-jumps directly to `uart_tx_fail`.
This assembly-time destination must:

- latch a nonzero state (1 for Classic), and perform immediate failure effects;
- return CF=1 with SP balanced;
- preserve E/HL/IX/IY, alternate registers and IFF; AF/BC/D may change.

There is no driver latch write before the custom destination. The real
SpecTalkZX `_uart_tx_fail` sets 1 and clears `_connection_state`,
`_sntp_init_sent` and `_sntp_waiting`, then sets `_status_bar_dirty` and CF.
Later sends in state 1 or 2 never reach it again. The default destination
also stores 1 (the previous driver used nonzero `0x41`); the public contract
remains nonzero=failure. Existing nonzero values remain unchanged on rejection.

The complete selectable example is [examples/classic_uart.inc](../examples/classic_uart.inc).
Its essential wiring is:

```asm
uart_tx_failed EQU _uart_tx_failed
uart_tx_fail EQU _uart_tx_fail
_ay_uart_send EQU uart_write_fast
uartRead EQU uart_read
    include "UartZX/src/divmmc_fast.inc"
```

Do not include `divmmc_vars.inc` when aliasing the application's byte. With
z88dk, import the three external dependencies and export `_ay_uart_init`,
`_ay_uart_send` and `uartRead`; EQU aliases to external symbols are covered
by the link test. sjasmplus can use the same example in a single scope.

The example retains **ten frame waits and 512 bounded startup reads**, plus
the core's guarded initial stale-byte read. The total is 523 status queries;
an unattached port returning a constant `0xFF` causes 523 data reads.
On a 48K floating bus, RX-ready varies with the display. `_frame_wait` retains
ownership of IY, interrupts and overlay handling. Deliberate init clears the
shared latch; it must accompany the application's existing recovery sequence,
never an automatic retry of a partly sent command.

`tests/test_classic.py` executes the linked example, the consumer's failure
body, its string loop and its inline SNTP digit sequence. It tests byte values
0/0x5A/0x80/0xFF, incoming carry both ways, IFF, alternate registers, stack,
E/HL, busy/timeout/absent ports, one shared-state read per send, failure effects
once, states 1/2, and failures at the first/middle/last byte of strings/blocks.
The compatible A wrapper has the same checks and preserves A/BC/DE/HL.

## Next compatible adapter

Next's TX and RX-error contracts are unchanged. SpecTalkZX still needs its
application gate and first-failure transfer around the compatible A API:

```asm
_ay_uart_send:
    ld a, (_uart_tx_failed)
    or a
    scf
    ret nz
    ld a, l
    call uart_write
    ret nc
    jp _uart_tx_fail
```

Alias `uart_tx_failed` to `_uart_tx_failed`, omit its allocation from
`next_vars.inc`, and allocate `uart_rx_errors` separately unless its ownership
has been proved compatible. `tests/spectalk_adapter.inc` retains this adapter
and the ten-frame/512-read initialization specimen; the tests still cover its
1/2 refusal and one-shot effects. All send paths must pass through the
application boundary. If separate latches are used, transfer failures/effects
on every path and retain fail-stop until deliberate recovery. Do not retry a
partly sent command. Next RX obligations follow below.

## RX recovery owned by the application

`uart_read_raw` bypasses the **old** error latch gate, not error detection.
Next still captures clear-on-status-read overflow/framing bits in
`uart_rx_errors`, and a read that sees a new error returns CF=0 without
consuming a byte. A later raw read can continue. AY still drops a byte with
a bad stop bit and captures the error; it can also return a valid first byte
while latching a bad second one. divMMC raw/read are the same operation and
have no hardware error reporting; CTS flow control remains mandatory.

Every Next status reader, including `uart_write` and the fast block sender,
retains RX errors. **CF=0 from TX says nothing about RX integrity.** A central
consumer boundary must examine/transfer `uart_rx_errors` after TX as well as
RX, before handing subsequent data to a parser. Preserve a received byte
and its CF while doing so. Code that reads status directly must also capture
its error bits using `uart_latch_rx_error`; the core header shows the exact
mask/compare sequence. That helper preserves registers and flags.

SpecTalkZX's existing `_next_uart_status` also signals that cached ring
indices must be reloaded after a gap. An adoption must preserve this effect,
not merely return the driver CF. Keep the current application policy:

- For resident IRC RX, mark the loss boundary with NUL (or replace the newest
  byte when the ring is full), preserving whole queued lines. Discard the
  damaged line through LF. Never consume a byte without ring room.
- ABOUT owns direct RX. Transfer the loss into its direct-consumer state;
  do not leave a marker for a ring parser that does not own that stream.
- UDP must reject the entire response when any gap occurs, including an
  error noticed while sending its command. Clear its transaction gap only
  at the existing transaction boundary, not after each read.

Once errors have been recorded in application-owned loss state, a bounded
raw drain can discard the uncertain tail. SpecTalkZX currently uses at most
1024 hardware bytes/polls and stops on FIFO empty; it does not wait for
silence. Continue that approach. A raw CF=0 can itself mean a newly detected
error, not an empty FIFO: inspect and transfer newly latched bits before
treating CF=0 as empty. Clear the driver's bits only after transferring them;
if a new error appears during the drain, clear/record it and continue within
the same budget. If the budget expires, keep the application stream invalid
and retry/resynchronize later. Preserve failure state until a clean boundary;
do not deliver the remaining tail as an intact response.

There is no need to link or call `uart_flush` in this critical path. It is an
optional explicit discard policy. On Next, 2048 consecutive samples must
show an empty FIFO and no new framing or overflow error. A byte, an error,
or both in one sample restart the window and count once toward the limit.
The 4097th such sample ends the flush with reads still gated. Both routines
ignore the persistent BREAK level when counting clean samples.
`uart_init` is not a substitute for RX
recovery either: it also clears fail-stop, resets TX FIFO and resets baud.

For a caller that wants the driver's quiet-window policy with bounded total
work, select `next_flush_budget.inc` after `next_core.inc` (or `next.inc`).
`uart_flush_budget` takes DE = total status samples, 1..65535, with 0 meaning
65536. Every poll counts. A byte or a new RX error restarts only the
`UART_FLUSH_QUIET` window. Use a budget
at least that large (2048) to allow success. For example, DE=4096 limits one
call to 4096 status reads, even with one byte near the end of every quiet
window. Smaller budgets always fail to establish silence. This is a work
bound. Interrupts, contention and wait states add elapsed time.

CF=0 means the quiet window completed and `uart_rx_errors` is clear. CF=1
means recovery remains incomplete. Both flush routines retain the observed
RX error bits and set `UART_RX_OVERFLOW`. Guarded RX stays blocked.
Handle UI/deadlines between calls; finish a later flush before reopening the
stream. No quiet-window progress is retained between calls. The routine
destroys A/BC/DE, preserves HL and IFF, adds 51 code bytes and no state.

Do not blindly alias `uart_rx_errors` to `_next_uart_gap`. They currently
have different representations and lifetimes: a hardware bitmap versus a
transaction/parser notification. Reuse storage only if all reads accept a
nonzero bitmap, all consumers use raw reads, and no clear loses an unreported
gap. Otherwise retain the one-byte capture latch and explicitly transfer
it; the additional byte preserves a different responsibility.

## Simultaneous Next RX and TX

The driver does not own a ring or call application code. A generic block
send does not receive while sending; a faster sender changes the time budget
but does not remove this obligation. With flow disabled and equal-rate traffic, 512 TX bytes
and 128 RX bytes pending overflow in the model at 115200/3.5 MHz, despite
the empty-FIFO 575-byte figure. Never use that figure as a chunk limit.

For initial RX occupancy R, baud b, CPU frequency f and a receive-free
interval T in CPU T-states, require

`R + ceil(T * b / (10 * f)) + margin <= 512` for 8N1.

Include caller work, interrupt service, contention and wait states in T.
If R is unknown, drain first; if the application ring has no room, stop,
parse existing data or declare a loss explicitly. A larger TX FIFO does not
increase the RX budget. RX service must sustain at least `b/10` bytes/s on
average, not only meet one chunk deadline.

After board checks and successful ESP RTS/CTS negotiation, `next_flow.inc`
lets the hardware throttle arrivals when the FIFO fills. A slower parser can
then reduce throughput instead of overflowing the UART FIFO; the no-flow
occupancy equation above describes the fallback path. Continue receiving during
TX retries, preserve error capture and bound every transaction: FIFO flow does
not provide an application buffer, parser recovery or a network deadline.

The assembled test consumer `tests/next_duplex.inc` drains up to 32 bytes
**before every one-byte TX** into a linear buffer. A ring consumer supplies
only its contiguous free space, updates the producer index and checks errors.
The model preserves all bytes with initial R=128 and R=480, 1024 additional
RX bytes and a 512-byte send at:

| Baud / CPU | Approximate T-states per arriving byte |
|---|---:|
| 9600 / 3.5 MHz | 3646 |
| 115200 / 3.5 MHz | 304 |
| 115200 / 28 MHz | 2431 |
| 2000000 / 28 MHz | 140 |

At 2 Mbaud/28 MHz, use the optional `uart_read_block` (~89 T/byte while
data is queued) or an equally measured application drain: the guarded
single-byte read plus bookkeeping is too slow. This test uses no competing
ISR, physical wait states or full application ring. It demonstrates a
schedule, not a universal safe chunk size or throughput guarantee.

For RX progress **even when TX stalls**, preserve SpecTalkZX's scheduling
outside the writer: service RX with room, attempt TX and publish RX faults.
Select `next_try_write.inc` after the core or full include and call
`uart_try_write` with A = byte. It takes at most one status sample, captures
its RX errors and queues only when there is room. CF=0 means queued; CF=1
with `uart_tx_failed=0` means temporarily full, so keep the byte/pointer and
service RX before retrying. CF=1 with a nonzero latch means a previous
blocking write failed; no status port is touched in that case. Busy never
sets the TX failure latch. A/BC/DE/HL and IFF are preserved; cost is 28 code
bytes and no state. Publish RX faults even when TX succeeds or is busy.

Use an application deadline/poll budget; on exhaustion invoke the existing
TX failure/disconnect path and stop. One TX owner must serialize UART writes.
The core's bounded blocking writer remains available, but its full timeout
can itself exceed the RX FIFO's fill time. One-byte scheduling alone cannot
guarantee RX continuity through a stuck transmitter. The regression services
RX between immediate attempts with TX stuck full and preserves 1024 incoming
bytes at 140 and 304 T-states/byte. It uses a linear buffer with room and no
competing work; an application must establish its own ring and timing budget.

## Next TX completion and effective fast rates

Select `next_tx_idle.inc` after the Next core or full include. `uart_tx_idle`
returns CF=1 when status bit 4 says both FIFO and serializer are idle; CF=0
while bytes remain queued or on the wire. It samples once, keeps RX errors,
destroys A/BC and preserves DE/HL/IFF. Cost: 13 code bytes, no state. It does
not clear `uart_tx_failed` or consume RX. Only use it on cores supporting
that status bit; NextSync guards its wait at core 3.01.10. The caller bounds
the wait, services RX and ensures no writer queues more data before changing
baud or resetting. There is no implicit TX wait inside `uart_set_baud`.

For the two fast divisor tables, optional `next_baud_khz.inc` supplies
`uart_baud_khz_1152000` and `uart_baud_khz_2000000`: eight 16-bit values each,
indexed by `NextReg 0x11 & 7`, with the same clock order as the divisor tables.
Render the selected integer in decimal and append three zeroes when forming
the application's ESP command. For example, timing 7 (HDMI) at the nominal
2 Mbaud setting gives 1929, so ask the ESP for 1929000 before following with
the corresponding divisor. The divisor produces approximately 1928571 baud;
this metadata is rounded to the nearest kHz, not an oscillator measurement.
All 16 combinations are tested against the actual divisor tables; rounding
differs by at most 500 baud and below 0.05%. Both metadata rows cost 32 bytes,
zero code and no writable state; slower rates and AT formatting stay out.

Baud changes still require the consumer's protocol: complete old TX, send the
ESP command at its current rate, handle its response/transition, change the
FPGA divisor, probe the new rate and recover if needed. A missing response
does not prove that the ESP rejected the change. The detailed
[NextSync comparison](external-comparisons.md#1-nextsync-original-and-zx-next-unite-derivative)
also covers flow negotiation, which remains application-owned and disabled
by default in UartZX. No command or hardware flow setting is applied by these
two optional includes.

## Optional Next hardware flow controls

Include `next_flow.inc` after `next_core.inc` or `next.inc`. It supplies
`uart_flow_enable` and `uart_flow_disable`, with no input parameters or success
result. Both target the ESP UART, change only frame-register bit 5 and restore
the previous ESP/Pi selection. They do not latch prescaler bits or change
either baud divisor. Frame size, parity, stop bits and break remain unchanged.
Call after initialization with frame reset clear; neither helper clears FIFOs,
consumes RX/TX data, reads status or clears hardware/software error latches.

The sequence saves IFF, disables maskable interrupts around select/read/modify/
write/restore, and restores IFF on exit. HL/IX/IY and alternate registers are
preserved; AF/BC/DE are destroyed. Cost: 32 code bytes, zero persistent state
and two temporary stack bytes. NMI handlers must not access UART registers
during the sequence. There are no waits, AT commands or capability probes.
The compatible full include and minimal core do not pull this leaf in;
`uart_init` still selects the ESP, resets FIFOs and sets 8N1 with flow off.
For a separate z88dk object, export the selected entry points with PUBLIC as
for the other optional leaves.

The caller owns the operational sequence:

1. Establish known board/pin support. The manual identifies ESP flow on Issue
   4 and no equivalent on Issue 2; reject unknown capability. GPIO routing,
   core support and ESP firmware must agree with the intended connection.
2. Keep FPGA flow off while configuring the ESP for RTS+CTS. If baud changes,
   use the old-rate drain/new-rate probe sequence above. Confirm the command
   and working communication before calling `uart_flow_enable`.
3. Continue servicing RX and bounding TX waits. Flow can legitimately pause
   TX for a long time; `uart_try_write` lets the application keep progressing.
4. On teardown or failed negotiation, call `uart_flow_disable` before the
   application's ESP restore/reset/fallback path. Clearing flow can release
   queued TX bytes: the caller must own that queue/transaction decision.
   This does not clear `uart_tx_failed` or establish a new valid RX stream.

Enabling a bit is not evidence that the board or ESP accepted flow control.
The regression model covers both UARTs' frame/select readback, all 128 frame
configurations with reset clear, both initial UART selections and IFF states,
repeat enable/disable, divisor preservation, unchanged queued traffic/error
state, and interrupt exclusion for every control-port access. It does not
simulate RTS/CTS electrical timing or validate negotiation with a real ESP.

The separate [hardware probe](next-probe.md) did validate RX backpressure on one
real Next on 2026-11-17: 4096 bytes arrived intact at 115200 and 2000000 after a
ten-frame RX pause with flow enabled; both no-flow controls overflowed. All four
2048-byte TX patterns matched on the host. This is a bounded board-specific
experiment, not evidence for every board, sustained duplex or forced TX stalls.

A later [sustained duplex run](next-probe.md#observed-sustained-duplex-run-2026-11-17)
completed five minutes per rate on the same Next: all 1492 chunks passed, totaling
12222464 B into the Next and 3055616 B back, with RX service overlapping payload
TX in every chunk and no UART errors. This extends the evidence to the probe's
paced workload; symmetric saturation, forced CTS stalls, other boards and
consumer adoption still require separate checks.

The library integration consists of selecting `next_flow.inc` with the existing
`next_try_write.inc`, `next_flush_budget.inc`, `next_tx_idle.inc` and optional
baud metadata as needed. Their public entry points, register/IFF contracts and
costs are documented and checked with both assemblers and separate-object
links. Capability checks and ESP AT setup remain explicit caller operations;
the diagnostic NEX supplies a hardware-validation caller. See the
[one-launch battery](next-batch.md) for extended validation of these same leaves.

[`examples/next_flow_uart.inc`](../examples/next_flow_uart.inc) supplies that
selection as one include: core, immediate TX, budgeted recovery, wire-idle query,
flow controls, effective-rate metadata and the 2000000 divisor table. It emits
357 code/read-only bytes; allocate `next_vars.inc` separately in writable RAM
(two bytes). The example adds no entry points or state of its own. Its regression
assembles identical bytes with sjasmplus/z88dk and links code and state in
separate objects. Flow remains opt-in after board/ESP negotiation.

## One receive step on all three backends

[`examples/receive_once.inc`](../examples/receive_once.inc) provides
`receive_step`: HL points to writable room for one byte. Call it directly on
each application iteration. CF=1 stores a byte and advances HL; CF=0 leaves
HL and the destination alone. The caller must bound the destination before
each call and own the overall receive deadline, parsing and recovery.

| Backend | Scheduling and recovery |
|---|---|
| divMMC / ZX-Uno | Read is immediate and preserves IFF. Flush makes 512 fixed polls and returns success; it does not certify silence or the end of a response. |
| Next | Read is immediate and preserves IFF. On `uart_rx_errors != 0`, stop delivery and recover. Use immediate TX attempts to keep servicing RX during TX stalls. |
| AY | Call read even when `uart_ready` reports CF=0: ready sees only the cached second byte and never opens CTS. A read may wait about 3.9 ms with interrupts off and returns with them enabled. Music must keep R7 bit 6 set. |

On AY, an incomplete flush sets `UART_RX_RECOVERY=0x80`; a bad stop bit sets
`UART_RX_FRAMING=0x40`. A raw read can leave both bits set (`0xC0`).
Test for nonzero to gate the stream. A completed flush or reinitialization
clears both bits.
Raw-read users still own error transfer and resynchronization. The example
is assembled with both assemblers and run with each backend, including AY's
ready=false before receiving the ESP's first byte and the differing IFF exits.

## Reset, waits and overlays

Next `next_reset.inc` provides `uart_esp_reset_assert` and
`uart_esp_reset_release`. Each preserves IFF and HL/IX/IY, clobbers AF/BC/D,
serializes the NextReg pair against maskable interrupts and returns without
waiting. The caller owns the hold interval and boot wait; it can use the
resident `_frame_wait` with its existing IY/overlay rules. An NMI handler must
not change NextReg selection during the pair. Assert writes NextReg 2=0x80,
release writes 0: **both ESP and expansion bus reset**. Account for storage
or other expansion devices; do not pulse during overlay I/O that depends on
them. Reinitialize UART state after reset before resuming traffic.

In the current Next overlay path, SpecTalkZX's `_frame_wait` suspends the
overlay, saves IY, uses IY=0x5C3A while interrupts are enabled for HALT, then
restores IY/overlay and returns with interrupts disabled. Keep that path;
do not substitute raw HALTs with an overlay still mapped. The new reset
controls preserve that disabled state. Their saved AF uses two temporary
stack bytes; the legacy frame reset's peak stack grows by two bytes, with
no added persistent state. Keep the reset code and stack resident across
the wait and any overlay suspension.

The compatible `next_esp_reset.inc` includes these controls and retains
`uart_esp_reset`: 25 HALTs, interrupts enabled on entry/return, no boot wait.
It still assumes frame interrupts; another interrupt source shortens the
hold. Include either reset file, not both. divMMC and AY cores never reset
the ESP or wait for boot. AY init still returns with interrupts enabled.

## Evidence limits

Assembly sizes, separate-object links, error/ABI/timing tests and FIFO/AY
models are local verification. No consumer was migrated, no complete
SpecTalkZX link or overlay budget was validated. Physical Next UART RX/TX and
RX flow have the measured evidence linked above. Next RX service
under stalled TX is covered by the linear-buffer model; a real consumer,
especially a full ring, still needs its own regression/hardware evidence.
The [complete Next battery](next-batch.md#completed-repaired-battery-2026-11-17)
passed 44 cases, including actual CPU selections at both UART rates, 50/60 Hz,
the injected recovery checks and two hours of symmetric endurance. No physical
AY/divMMC, bus-reset or competing interrupt-load check, or other VGA/HDMI
clock-timing sweep, is claimed. The hardware runs predate the 2026-11-22 size
pass and were not repeated. Of the routines the probe uses, that pass changed
the code of `uart_init`/`uart_set_baud` (divisor write), `uart_read` and
`uart_read_raw` (the error exit), `uart_flush_budget`, `uart_try_write` and the
flow controls. Model comparisons between `c608493` and `79a94b1` found the same
port accesses in the same order. The subsequent flush correction is covered by
emulator regressions for errors on an empty FIFO and recovery at the sample limit.
Consumer adoption is outside this library
validation. AY's existing provenance/licence qualification in the README remains.
[External comparisons](external-comparisons.md) distinguish
executed routine specimens from inspection, historical documentation and
unvalidated complete applications.
