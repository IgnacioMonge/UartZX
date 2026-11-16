# Integration and SpecTalkZX adoption

UartZX owns register access, bounded TX, fail-stop and RX error capture.
The application owns scheduling, buffers, framing, recovery and disconnect
effects. Selecting smaller includes does not change that boundary.

## Measured scope

Work started at UartZX `bdb1621`, with a clean checkout and 54 passing tests.
SpecTalkZX was read, not modified: `C:/dev/SpecTalkZX dev`, clean `main`,
`2140e6b`. Its current `asm/divmmc_uart.asm` and `asm/next_uart.asm` were
assembled and linked in temporary files with their external symbols supplied
as absolute addresses. This confirms **101 B Classic and 267 B Next code**;
it does not measure their external ring, failure handler or application state.
No SpecTalkZX application binary was rebuilt or linked with UartZX.

UartZX has no published tags or GitHub releases at this check, so its changelog
continues to describe the first release under Unreleased.

| UartZX selection | Code | Required state | Omitted from compatible full include |
|---|---:|---:|---:|
| divMMC core | 75 B | 1 B TX failure | 81 B |
| Next core | 192 B | 1 B TX failure + 1 B RX errors | 147 B |
| AY core | 403 B | 2 B receive cache + 1 B RX errors | 70 B |

The README lists each optional cost and the complete configurations. All
optional includes add zero state. For each checked configuration,
`tests/test_modularity.py` compares both assemblers' bytes and links separate
application and driver objects with code at `0x8100` and BSS at `0xC000`.
Only init/read/write are referenced by the application object. The resulting
code section contains the entire selected driver plus the 10-byte caller;
unreferenced selected helpers remain present. The table excludes that caller.
This is actual assembly/link evidence, not a dead-code-elimination estimate.
Tools used: sjasmplus 1.23.1, z88dk-z80asm 24860-762578732c-20260630,
Python 3.11 and the `z80` emulator.

The old isolated Classic trial of 188 B (+87 B) is historical context, not a
fresh measurement or a lower bound. The adoption specimen below now assembles
to 116 B Classic / 233 B Next code (core + 14-byte send adapter + 27-byte
initialization wrapper). It shares the existing application TX byte; Next
still needs an RX error byte. Both assemblers agree. External `_frame_wait`
and `_uart_tx_fail` are supplied by the application; test substitutes are
outside those counts. Classic's specimen is +15 B against the current
101-byte module. The Next specimen **does not replace** the 267-byte module:
RX service, gap marking and recovery still have to be wired and costed.
These are not final SpecTalkZX link deltas or evidence that its memory guard
passes. In particular, do not budget Classic against a historical free-RAM
figure; its current build guard and overlays must be checked at adoption.

## Includes, ABI and placement

Use one `<backend>_core.inc` and only the required leaves. The existing
`<backend>.inc` and `send.inc` are composition files with the old public
entry points. There is one algorithm per routine, no runtime options and no
preprocessor feature matrix. z88dk must receive `-I <UartZX>/src` for nested
includes; this is the one required build-command adjustment for full includes.
Core-only builds without nested includes do not need that adjustment.

Next initialization still selects the ESP, resets both FIFOs, selects 8N1 and
programs 115200 for the current video timing. Its setter and 16-byte 115200
table are therefore core dependencies. Other baud tables are independent.
Changing baud remains an explicit operation on both peers followed by
application resynchronization; it does not require linking `uart_flush`.

Keep one symbol scope per driver (`MODULE` with sjasmplus, consumer
PUBLIC/EXTERN and SECTION declarations with z88dk). Keep core and leaves
resident and mapped while called; an overlay must not replace them or their
state. State must be writable RAM, initialized on cold start or through
`uart_init`. AY's cache bytes must remain adjacent. Only its optional
`ay_init_keep_mixer.inc` has an include-order constraint: immediately before
`ay_core.inc` for the shared initializer's short jump.

No core uses IX or IY. divMMC and Next preserve the interrupt enable state;
AY timed I/O disables interrupts and returns enabled as before. AY code and
stack still require uncontended memory, including a suitable bank on 128K
machines. Do not infer ISR safety or reentrancy: an interrupt handler must
not concurrently operate the same UART/AY registers or change required banks.

## SpecTalkZX TX adapter and state ownership

SpecTalkZX passes a byte in L; `uart_write` takes A and preserves A/BC/DE/HL.
A bare `ld a,l` adapter is insufficient on failure. SpecTalkZX's
`_uart_tx_failed` means 0=healthy, 1=pending report, 2=already reported.
Its `_uart_tx_fail` immediately clears connection/SNTP state and writes 1;
the main loop later disconnects, reports the error and changes 1 to 2.
Classic UartZX can latch **0x41**, and Next latches 1. Never expose 0x41 to
code that checks `== 1`, or clear/overwrite state 2 on subsequent sends.

The least-state option is to alias the driver TX latch to the existing byte
and **omit its allocation in `<backend>_vars.inc`**. With z88dk, import
`_uart_tx_failed` and define `DEFC uart_tx_failed = _uart_tx_failed` in the
driver module. For Next, allocate `uart_rx_errors` separately, or alias it
only after checking the RX ownership rules below. For a single sjasmplus
scope, the equivalent is `uart_tx_failed EQU _uart_tx_failed`.

The tested send adapter is:

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

The application gate preserves 1 and 2 and prevents repeated failure effects.
The first failed write reaches `_uart_tx_fail` even when UartZX has already
written the shared byte. That normalizes Classic's 0x41 and runs all immediate
effects once. Later writes remain stopped. At deliberate recovery,
`uart_init` clears the shared latch; reset/boot readiness and application
connection state must also be reset in the consumer's existing order.
Do not route around this adapter from string/block send paths: either keep
their existing loop through the adapter, or wrap a UartZX bulk send's CF
and RX error results into the same application effects before returning.

If aliasing is unsuitable, retain the driver byte and transfer failures into
the application's state/effects on every send path. Retaining the driver
latch until deliberate reinit is simplest. Clear it earlier only after the
application gate has taken over fail-stop; never continue a partially sent
command. This costs one additional byte of state and extra transfer code.

`tests/spectalk_adapter.inc` includes the send adapter and a specimen init
wrapper with the existing ten `_frame_wait` calls and 512 bounded startup
reads. The tests check 0x41-to-1 normalization, effects once, and refusal in
states 1 and 2. They do not implement the real C disconnect path or frame wait.

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
optional explicit discard policy: on Next it waits for a quiet window and
may give up with reads still gated. `uart_init` is not a substitute for RX
recovery either: it also clears fail-stop, resets TX FIFO and resets baud.

Do not blindly alias `uart_rx_errors` to `_next_uart_gap`. They currently
have different representations and lifetimes: a hardware bitmap versus a
transaction/parser notification. Reuse storage only if all reads accept a
nonzero bitmap, all consumers use raw reads, and no clear loses an unreported
gap. Otherwise retain the one-byte capture latch and explicitly transfer
it; the additional byte preserves a different responsibility.

## Simultaneous Next RX and TX

The driver does not own a ring or call application code. A generic block
send does not receive while sending; a faster sender changes the time budget
but does not remove this obligation. With equal-rate traffic, 512 TX bytes
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

At 2 Mbaud/28 MHz, use the optional `uart_read_block` (~98 T/byte while
data is queued) or an equally measured application drain: the guarded
single-byte read plus bookkeeping is too slow. This test uses no competing
ISR, physical wait states or full application ring. It demonstrates a
schedule, not a universal safe chunk size or throughput guarantee.

For RX progress **even when TX stalls**, preserve SpecTalkZX's scheduling
outside the writer: service RX with room, read TX status with error capture,
publish RX faults, and only call `uart_write` once room is available. Use an
application deadline/poll budget; on exhaustion invoke `_uart_tx_fail` and
stop. With one TX owner, the FIFO cannot become fuller between the room
sample and the write. Polling repeatedly must continue to service RX; every
status read must latch errors. The core's bounded blocking writer remains
the safety net, but its full timeout can itself exceed the RX FIFO's fill
time. One-byte scheduling alone cannot guarantee RX continuity through a
stuck transmitter. No callback was added: application scheduling already has
the ring room, overlay ownership and recovery information the driver lacks.

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
SpecTalkZX link or overlay budget was validated, and no physical UART,
turbo/wait-state, bus-reset or interrupt-load test was run. Next RX service
under stalled TX and a full ring still needs a consumer implementation and
its own regression/hardware evidence. AY's existing provenance/licence
qualification in the README remains. Hardware adoption is pending.
