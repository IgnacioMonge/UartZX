# External UART implementations compared with UartZX

This review compares the versions cited in the supplied audit, retrieved on
2026-11-17. It does not silently substitute a project's current default branch.
UartZX is commit `9576251` plus this working tree's optional recovery/TX leaves
and AY recovery/model corrections. External source stays outside the product.

The question for each implementation is what UartZX should adopt, retain or
leave to its caller. Whole applications are not interchangeable with a transport
library. Buffer ownership, ABI, error reporting, bounded work, interrupts,
CPU/I/O timing and hardware prerequisites are compared separately. Source
inspection supports control-flow conclusions; measured instruction execution
uses the project's emulator, not physical UARTs. Upstream comments about field
tests are reports by their authors, not measurements reproduced here.

## 1. NextSync: original and ZX-Next-Unite derivative

### Sources and boundaries

Read the original [uart.s](https://github.com/jarikomppa/specnext/blob/d80df1262f19e8cca4ae2a438b089ed800d497a7/sync/uart.s)
and the UART/AT paths in [nextsync.c](https://github.com/jarikomppa/specnext/blob/d80df1262f19e8cca4ae2a438b089ed800d497a7/sync/nextsync.c),
then the derivative's [uart.asm](https://github.com/jclauzel/ZX-Next-Unite/blob/d7d52c159ae549f8b71c6e64459c2a248c2d7219/nextsync/sync/z88dk/uart.asm)
and UART configuration/recovery paths in [nextsync.c](https://github.com/jclauzel/ZX-Next-Unite/blob/d7d52c159ae549f8b71c6e64459c2a248c2d7219/nextsync/sync/z88dk/nextsync.c).
The derivative's transfer framing, checksums, retries, OS memory mapping and
ESP negotiation belong to its application. They cannot be credited to its
small receive routine alone, or charged as mandatory UartZX core costs.

### Receive behavior and memory safety

| Property | Original | Derivative | UartZX |
|---|---|---|---|
| Input/output ABI | Stack pointer argument, HL byte count | Fastcall HL pointer, HL count | HL pointer, B capacity; HL advanced, D remaining |
| Bound on stores | Until RX empty; no capacity parameter | Fixed wall at `inbuf+2048`, from the supplied pointer | Caller capacity 1..256 per block call |
| Buffer placement | Chosen by caller | Private bank mapped at `0x6000`; upper wall `0x6800` | Any writable caller-owned contiguous region |
| Empty input | Returns immediately | Returns immediately | Initial sample returns immediately; after a byte waits up to 96 empty samples |
| RX overflow/framing | Status bits discarded | Status bits discarded | Latched; damaged-stream delivery stops |
| Extra side effects | Border writes for activity | Border writes for activity | No display writes |

The original's outer AT parser checks expected lengths against 2048, but that
does not bound the inner drain: one call can consume more data before the
outer check runs. The derivative fixes that specific boundary. Its pointer
contract still matters: it accepts only pointers inside its own input region;
the subtraction checks pointers past the upper wall, not below the base.
UartZX takes an explicit capacity instead of owning an application region.
Its caller must supply actual room; B=0 means 256, not zero capacity.

Both external readers mask status to availability. Since a status read clears
hardware error flags, later checksum failure cannot recover the UART cause.
Checksums/retries are valuable end-to-end protection, but do not replace loss
notification to an arbitrary stream parser. Keep UartZX's capture and gate.

### Reproduced RX experiments

`work/external-audits/recovery-duplex/compare_nextsync.py` extracts only the
receive functions into temporary specimens. The original's ASxxxx immediate
and two-operand logical syntax is translated without changing instructions;
its stack argument is supplied. The derivative's code is unchanged apart from
declaring its fixed buffer address. This is not a build of either application.

| Experiment | Original | Derivative | UartZX |
|---|---:|---:|---:|
| Steady RX cost, differential count at 100/200 bytes | 93 T/byte | 108 T/byte | 98 T/byte, existing block test (89 since the 2026-11-22 size pass) |
| Always-ready source, 2050 bytes, sentinel at `0x6800` | Stores 2050; overwrites sentinel | Stores 2048; sentinel intact | Caller bounds stores to supplied room |
| Framing flag followed by byte `0x41` | Returns the byte; hardware flag cleared | Same | Latches `0x40`; returns no byte |

The throughput experiment leaves border writes enabled. No interrupt load,
contention or Next physical waits are included. The original's 5 T/byte lead
over UartZX buys neither capacity checks nor error capture; the derivative
has its capacity check but is 10 T/byte slower than UartZX in this test. This
does not establish application-level throughput superiority.

### Flush, TX and completion

The original's hard flush restarts its quiet counter for every byte and has
no global cap. The derivative adds a second counter that never restarts.
That is the same useful separation implemented by `uart_flush_budget`:
quiet completion and maximum work answer different questions. A fixed window
alone cannot certify silence. Unlike the derivative's void flush, UartZX
reports exhaustion and keeps guarded input closed.

The original TX code still writes after its timeout reaches zero while full.
The derivative checks the final full indication and stops without that write.
It resets the wait window for each byte; a finite chunk bounds total work.
Neither C path captures RX errors from those status reads or drains RX while
waiting. UartZX retains explicit failure/remaining-count contracts and offers
`uart_try_write` so the caller can service RX while TX is paused.

Both NextSync variants optionally wait for status bit 4 after sending; their
version check uses core 3.01.10. The consulted [HDL status construction](https://github.com/avlixa/ZX_Spectrum_Next_FPGA/blob/01e322a39360f4ae89b0c575b7aacae0d2441416/cores/zxnext/src/serial/uart.vhd#L546-L551)
defines this as FIFO empty **and** serializer not busy, rather than mere
FIFO room. UartZX now exposes that query in `next_tx_idle.inc` (13 B), preserving
RX errors and IFF. The caller owns the wait deadline, RX service, exclusive TX
ownership and core capability check; no automatic wait is added to writes.

### Baud and flow configuration

The derivative's 16-bit kHz table matches its programmed fast divisors. It
formats that value plus three zeroes into the ESP command, avoiding a runtime
division dependency. UartZX adopts the data representation in optional
`next_baud_khz.inc` (32 B for both fast modes), tested against all 16 divisor/
clock combinations. Its rounding error is at most 500 baud and below 0.05%
here. HDMI's 2 Mbaud divisor produces about 1,928,571 baud; requesting
1,929,000 at the ESP narrows the arithmetic mismatch. Actual oscillator and
ESP compatibility remain hardware questions. No AT command enters the driver.

The flow path is a negotiated state machine, not a register toggle: select
ESP, clear inherited flow, identify the board, configure ESP baud/RTS+CTS,
drain at the old baud, change divisor, probe, then enable FPGA flow. Its board
guard accepts raw IDs 2/3 and rejects unknown IDs. Failed probes use bounded
reset/retry/fallback paths because silence does not prove the ESP rejected
the command. These distinctions are sound reasons to keep negotiation in the
application. Timing/reset anecdotes in comments were not reproduced. A
library helper must not silently arm flow or assume an unknown board works.
The subsequent authorized module, `next_flow.inc`, provides only the two
register controls (32 B), preserving selection/divisors/IFF. It does not
implement or claim to validate that negotiation state machine.

### Decision

Adopt the global-budget pattern, TX completion query and fast-rate metadata.
Retain UartZX's explicit capacity, error capture, fail-stop and application-owned
deadlines/buffers. Do not replace its RX loop with either external loop, import
border effects, or import the whole AT/reset state machine. The flow sequence
is a concrete integration reference; it does not validate flow on our hardware.

## 2. z88dk RS232 plus3: AY serial routines

### Sources and intended connection

Read [rs232_get.c](https://github.com/z88dk/z88dk/blob/0c92d7bdbd6119a151d03599a72ce1b8635d1703/libsrc/target/zx/classic/rs232/plus3/rs232_get.c),
[rs232_put.c](https://github.com/z88dk/z88dk/blob/0c92d7bdbd6119a151d03599a72ce1b8635d1703/libsrc/target/zx/classic/rs232/plus3/rs232_put.c),
[rs232_params.c](https://github.com/z88dk/z88dk/blob/0c92d7bdbd6119a151d03599a72ce1b8635d1703/libsrc/target/zx/classic/rs232/plus3/rs232_params.c)
and the corresponding [public header](https://github.com/z88dk/z88dk/blob/0c92d7bdbd6119a151d03599a72ce1b8635d1703/include/rs232.h).
The `.c` suffix hides hand-written timed assembly. Both get and put cite
Russell Marks's zfst terminal. This establishes a documented reference, not
a complete chain of authorship to Nihirash or permission for unrelated code.

These routines implement a general RS232 cable interface. Their TX waits for
DTR on port A bit 6 before disabling interrupts and transmitting. UartZX's ESP
wiring requires TX/RX/CTS and does not require that incoming DTR signal.
Waiting for DTR is useful on the intended cable, but prevents substitution
on an ESP adapter that leaves it high. Removing the wait changes that contract.

### Receive state, timing and failure paths

| Property | z88dk plus3 path | UartZX AY |
|---|---|---|
| Timing parameter | Runtime `BAUD` word, table through 9600 | Assembly constant; only 9600 validated |
| Buffering | `SERFL` flag followed by second byte | Same small cache shape plus error latch |
| First start detection | Up to 250 polls, triple low sample | 240 polls plus bounded late-start window |
| Stop handling | Delays past it; does not validate high | Explicit check on first and cached second byte |
| False second start | Returns directly to sample without spending the timeout | Retry spends the poll budget |
| Port exit | Raises CTS; port paths finish with EI | Same; guarded failures/cache can avoid port access |
| Data/ABI | C status code and pointer output; uses alternate registers | CF/A result; preserves DE/HL for shared transport ABI |

The loops share recognizable bit-counter and delay structure. They are a
valuable independent timing reference, but the bit schedules are not identical:
z88dk TX has three fine-tuning NOPs where UartZX has four. Changing that byte
changes every bit, so copying a smaller loop is not a harmless optimization.
Runtime speed selection also requires RAM and revalidation of start/late/stop
windows, rather than only importing the baud lookup values.

`compare_z88dk.py` executes the extracted `doread` body with `BAUD=11` and
the same AY/ESP model as UartZX. It translates local-label, numeric and
alternate-AF syntax; it does not compile or claim to validate the C wrapper.

| Extracted-loop experiment | Observed result |
|---|---|
| Valid `0x41` at 9600 | Returned correctly in 5343 T-states |
| `0x41` with low stop bit | Returned `0x41` as success; 6851 T-states |
| RX stuck low | Returned `0x00` as success; 6851 T-states |
| Chattering second start after a valid first byte | Still inside the retry loop after 200000 T-states, interrupts disabled |
| Put with DTR high | No TX and no return within 200000 T-states |

Inspection explains the nontermination cases: the false-start edge goes back
before the decrement, and DTR's loop has no counter. The finite experiment
alone is not a proof of infinity. UartZX's existing regression suite covers
the corresponding chattering line, late starts, bad stop bits, cached bytes
and recovery. Its safeguards should be retained. No physical tolerance margin
or entire-library size comparison follows from these specimens.

### Parameter validation caveats

Two source-level details limit using `rs232_params` as a drop-in reference.
Its parity check performs XOR A followed by AND E, so the value tested is
always zero; it does not reject nonzero parity as its comment implies. Its
rate guard accepts indices below 12, but the active table contains 11 words
(indices 0..10). The header's 19200 constant is index 11, so that request
reads beyond the declared table; the resulting value depends on link layout.
These are inspection findings, not results from a built z88dk application.
They do not invalidate its validated 9600 timing path, nor justify copying
its parameter validation.

### Decision

Use z88dk to document the timed-loop family and cross-check delay/handshake
choices. Keep UartZX's stop checks, bounded false starts, explicit idle level,
late-start coverage and shared ABI. Do not add DTR or runtime baud state to
an ESP driver without a caller that needs them. Support for lower rates may
be valuable later, but the table alone does not validate those rates. No
production AY timed instruction was changed as a result of this comparison.

## 3. NextZXOS UART.DRV and ESPAT

### What was actually inspected

The [Next documentation](https://wiki.specnext.dev/ESP8266-01#Software_Support)
describes UART.DRV as an interrupt-driven NextZXOS driver with up to 16 KiB
of software buffering. The [distribution listing](https://gitlab.com/thesmog358/tbblue/-/tree/master/nextzxos)
contains separate UART.DRV, ESPAT.DRV and ESPAT.SYS artifacts. The pinned
[ESPAT author's README](https://github.com/Threetwosevensixseven/NXtel/blob/9d84144fab283896b02070c947603f7f9775382e/docs/ESPATREADME.TXT)
documents the replacement IRQ path, command/IPD buffers, bank allocation,
clearing status reads and standalone-stub integration. Its title says v0p8
Alpha while later notes discuss other alpha versions; it is historical API
evidence, not a specification of the distribution's current binary.

Further retrieval located [UART.DRV 2.4 source](https://gitlab.com/thesmog358/tbblue/-/blob/3a892b03738cfb27eee01f33d09186d3215a5a4a/src/asm/uart/uartdrv.asm)
and its [container builder](https://gitlab.com/thesmog358/tbblue/-/blob/3a892b03738cfb27eee01f33d09186d3215a5a4a/src/asm/uart/uart_drv.asm),
plus the [ESPAT Alpha 10 stub](https://gitlab.com/thesmog358/tbblue/-/blob/3a892b03738cfb27eee01f33d09186d3215a5a4a/src/asm/espat/espatdrv.asm)
and its container builder. The distribution revision is fixed; source versions
come from file headers. Matching those files against shipped binaries was not
tested. NXtel's `src/espat.asm` is an API consumer, not the IRQ implementation.
ESPAT's extended TSR/SYS body was not obtained, so its packet parser and full
receive implementation remain outside the code verdict.

### The responsibilities differ

| Responsibility | UART.DRV / ESPAT architecture | UartZX |
|---|---|---|
| RX scheduling | Installed interrupt service; standalone ESPAT needs regular service calls | Foreground caller polls; no ISR installed |
| Persistent storage | Software receive buffers; ESPAT also banked command/IPD data | Next core: two state bytes; application owns payload storage |
| Protocol | UART.DRV is serial transport; ESPAT parses/manages ESP channels | Raw byte transport only |
| Environment | NextZXOS driver API; standalone stub still needs mapping/stack/service setup | Shared include syntax and transport ABI across three backends |
| Loss reporting | Historical ESPAT API has timeout/full/overwrite flags cleared by queries | Hardware errors retained until explicit recovery; caller publishes stream loss |

Do not combine UART.DRV's 16 KiB description and ESPAT's separate buffers into
one interchangeable footprint. The ESPAT README describes 8 KiB command
storage and 16 KiB IPD banks, with extra pool/mapping responsibilities. It
replaces the UART handler rather than simply stacking another receive ISR
on the same hardware owner.

### What buffering solves, and what it cannot solve

A software ring can let foreground parsing/display work pause while an ISR
continues draining hardware. It does not increase the time an interrupt can
remain unserviced: bytes still first enter the 512-byte hardware FIFO. For
8N1, an initially empty FIFO represents 44.44 ms at 115200 baud but only
2.56 ms at 2 Mbaud. A 50 Hz service interval is 20 ms, so that interval alone
cannot sustain continuous 2 Mbaud without flow control or more frequent
service. These are arithmetic bounds, not measurements of UART.DRV's handler.

Once bytes reach software, 16 KiB represents roughly 1.42 s at 115200 or
81.92 ms at 2 Mbaud before consumption, assuming all bytes are usable storage.
It absorbs bursts, not an indefinitely slower consumer. Ring fullness must
still produce backpressure or an explicit gap; overwriting unread data cannot
be treated as a whole stream. Long DI sections, SD I/O, changing MMU windows
or a competing status reader remain integration hazards. The author's warning
about missed interrupts therefore matters even with large buffers.

An optional UartZX IRQ adapter would need a single UART/status owner, a defined
resident mapping, bounded handler work, atomic producer/consumer indices,
overflow/error transfer, interrupt-vector ownership and installation/removal
rules. AY cannot share the same autonomous RX model: its receive operation
owns CTS and timed sampling while interrupts are off. A common transport API
does not make one hardware-specific ISR portable across the three backends.

### Concrete UART.DRV and stub findings

UART.DRV reserves a 512-byte relocatable driver image plus relocation metadata;
its data ring is separate. The container marks driver `U` for IM1 service and
requests no automatic extra RAM bank: allocation/mapping must be configured
through its API. The entry contract puts the stack below `0x4000`, requires
interrupts disabled and preserves index/alternate registers. MMU6/7 are saved,
changed for the ring and restored. This is an OS integration contract, not
the cost or ABI of a small polling leaf.

Its IRQ entry samples status and returns immediately without RX-ready. If
ready, it retains only status bit 2 on that first sample. Subsequent drain/
idle polls test only bit 0; TX's bounded `tx_char` tests only bit 1. Under
current clear-on-read status semantics, framing can be lost and an overflow
that appears after the initial sample can be cleared without entering the
software flags. An interrupt-driven architecture does not itself solve error
ownership across all readers.

`compare_uartdrv.py` executes the extracted IRQ/drain body with MMU swaps
stubbed and the documented low stack for a full-ring run. It reproduces:
framing without data returning with no software flag; framing plus `0x41`
delivering that byte with no framing flag; overflow injected on the second
status sample disappearing from both hardware and software flags. A continuous
source fills the 16 KiB ring and sets its software-full flag, but the full
exit bypasses the normal `input_add` publication: both published positions
remain at the prior `0xC000`. Consumers must not treat equal pointers there
as proof of a clean empty stream. This test does not emulate MMU, relocation,
NextZXOS or the packaged binary.

The driver stops draining on ring full, returning CPU time to the foreground.
Its ordinary path waits for 256 empty polls after a byte. That window restarts
on progress; there is no separate sample budget. The ring capacity eventually
bounds writes when its consumer cannot run concurrently, but can still create
a long IRQ under a sustained source. The `tx_char` timeout is 1024 polls and
does not drain RX; because the OS driver executes with interrupts off, its
receive ISR cannot rescue that wait. These are integration costs to measure
before adopting its scheduling pattern.

The ESPAT stub similarly tests ready before saving the initial bit-2 flag,
then pages MMU5/7 and calls an extended service at `0xA018`, preserving IX
around it. It does not contain the complete receive/parser algorithm. Its
command pointer starts at `0xE600`, so an 8 KiB mapped page is not evidence
that all 8 KiB is usable command data. Do not infer actual usable capacity,
parser limits or all status readers from the earlier README alone.

### Decision and remaining evidence

Use an installed driver when a NextZXOS application wants that environment's
buffered/channel API. Consider a separate UartZX adapter only for a consumer
whose measured foreground service cannot meet RX deadlines and whose memory/
interrupt ownership allows it. Keep the current core independent of NextZXOS,
parsing and ring storage. The immediate TX primitive addresses a concrete
foreground scheduling problem without committing every user to an ISR.

Before adopting/reusing these handlers, match source to the selected binary,
obtain ESPAT's extended SYS body, correct/verify current status-bit handling
and full-ring publication, then measure maximum interrupt blackout and mapping
behavior. No driver binary was installed or full OS driver run; the extracted
UART body was tested as described. The concrete findings concern these source
paths under the consulted UART semantics, not every released version.

## 4. NXtel: incremental AT/IPD reception

### Sources and architectural contribution

Read the pinned [esp.asm](https://github.com/Threetwosevensixseven/NXtel/blob/9d84144fab283896b02070c947603f7f9775382e/src/esp.asm),
including `ESPReceiveIPDInit`, `ESPReceiveIPD`, completion processing,
connection/TX paths and both timeout/non-timeout response readers. This is
the direct UART implementation, not NXtel's separate ESPAT API experiment.
It contains Zeus macros and self-modifying instruction operands; the whole
application was not assembled in this review.

The useful idea is explicit persistent parser state. A call samples readiness,
consumes at most one serial byte and dispatches to the current phase: find the
IPD prefix, collect length or store payload. If no byte is ready, it returns
to the application's input loop. A fragmented header therefore need not
force a blocking receive-until-complete routine. The parser's lifetime spans
calls, rather than being hidden in a long transport call.

### Comparison with a UartZX consumer

| Concern | NXtel implementation | Appropriate UartZX boundary |
|---|---|---|
| Parser progress | Self-modifying state targets and pointers | Application-owned parser state; transport returns bytes |
| No data | Returns to keyboard/main loop | Next/divMMC read returns immediately; AY read has its timed window |
| Packet completion | Calls page processing over collected payload | Budget bulk work separately; completion can still be expensive |
| Protocol storage | 2048-byte ESP buffer, length scratch, teletext escape handling | Caller supplies bounded storage; no teletext assumptions in UART |
| IFF | IPD routine executes DI and returns through EI | Next/divMMC core preserve IFF; application chooses critical sections |
| Serial errors | Tests availability without retaining framing/overflow | Capture every status read; invalidate parser on a reported gap |
| TX | Several full/busy waits have no timeout or RX service | Immediate attempt plus application deadline and RX scheduling |

One-byte admission is not a hard latency guarantee for the entire IPD function:
on completion it calls `ProcessESPBufferToPage`, which walks the payload and
updates page state. Likewise, an otherwise responsive main loop can still be
stopped by `ESPReceiveWaitOK`, `ESPReceiveWaitPrompt`, connection output or a
stuck `ESPSendChar`. The separate timeout reader shows an attempt to address
some waits; it does not bound all the others. The timeout helper restores a
saved stack pointer on failure, which is tightly coupled to that call tree.
UartZX's CF/remaining-pointer contracts avoid importing this nonlocal exit.

### Bounds and stream integrity

The length-capture path appends into its scratch buffer without a visible
capacity check in that path, and payload storage advances a pointer without
checking the 2048-byte wall. Its decimal conversion also depends on a finite
digit-multiplier table. Those are caller/protocol assumptions to verify before
reusing the parser with arbitrary ESP output. This source-level observation
does not claim an exploit or failure in normal NXtel traffic; no malformed-
packet execution of the Zeus application was performed.

More fundamentally, both RX and TX status reads can consume hardware error
indications. A parser that continues after a missing byte can misinterpret a
payload byte as framing. An adapter around UartZX must publish a loss event
and reset/resynchronize parser state before accepting another response.
Keeping prefix/length state is useful only while the underlying stream remains
valid; it is not a substitute for the driver's RX error latch.

### Decision and test boundary

Adopt the scheduling principle in consumers: retain AT/IPD state across bounded
foreground steps, keep keyboard/UI work reachable and separately budget packet
completion. Retain UartZX's transport-only scope and use block RX where line
rate requires it. Do not import Zeus self-modification, teletext escapes,
unbounded waits or the stack-rewind timeout mechanism into the shared driver.

This comparison is by inspection of the concrete assembly paths. There is no
claim that NXtel's full parser, MMU/UI code or malformed-input behavior was run
in the UartZX harness. A consumer migration would need its own fragmented-
header, capacity, gap/reset, cancellation and completion-latency tests; no
SpecTalkZX or NXtel files were changed here.

## 5. newt: frame-clock timeouts and cancellation

### Sources and concrete behavior

Read [uart.c](https://github.com/chris-y/newt/blob/b150c809c7e0bff723fc4cca319c33d495cc96e0/uart.c),
[timer.h](https://github.com/chris-y/newt/blob/b150c809c7e0bff723fc4cca319c33d495cc96e0/timer.h)
and `user_break`/cleanup in [main.c](https://github.com/chris-y/newt/blob/b150c809c7e0bff723fc4cca319c33d495cc96e0/main.c).
The UART layer is short C code around direct Next ports. Its distinguishing
feature is checking the user's BREAK key during polling and referring RX
timeouts to the ROM frame counter instead of assuming a particular CPU speed.

| Operation | Bound/cancellation | Consequence |
|---|---|---|
| Flush RX | Until FIFO empty; BREAK checked after each byte | Responsive cancellation, but no total work/silence guarantee |
| TX string/block | Waits while full; BREAK checked | User can abort, but no automatic deadline or RX drain |
| RX one byte | BREAK plus frame-counter difference greater than 250 | Approximate five-second idle timeout at 50 Hz, then process exit |
| RX line | Repeated byte reads, newline or capacity stop | Each byte gets a fresh timeout; there is no shared line deadline |

`user_break` terminates the process rather than returning a transport status.
That can be a sensible dot-command policy with registered cleanup, but a
shared driver cannot terminate an arbitrary application on silence or key
input. Its caller may need to redraw UI, retry, disconnect one socket or
retain other channels. The same separation applies to keyboard scanning.

### Clock and capacity details that matter before reuse

The timer macro reads three separate bytes at ROM FRAMES addresses 23672..74.
It represents a 24-bit counter, while RX stores a `uint32_t` start value.
Plain 32-bit subtraction is not automatically modulo-24-bit subtraction:
start `0xFFFFFE` and now `1` are three ticks apart, but a 32-bit unsigned
difference is `0xFF000003`, enough to trigger the timeout. This is an
arithmetic edge case, not an observed target-binary failure. The macro also
does not express a coherent three-byte snapshot against frame interrupts.
A reused timer policy needs a defined wrap width and snapshot method.

Counting 250 ticks assumes 50 Hz. At 60 Hz the same threshold is about
4.17 seconds, and a strict greater-than comparison waits for a further tick.
If interrupts are disabled or the ROM counter is not maintained, the deadline
does not advance. Conversely, increasing CPU frequency does not shorten a
working frame-based deadline as it shortens an instruction-count budget.
That distinction is the useful lesson; neither kind of bound is universally
better without the consumer's interrupt/timing contract.

`uart_rx_line` reserves a terminating NUL by subtracting one from capacity,
but enters its receive/store loop at least once. With capacity one it writes
the byte and then a NUL beyond that one-byte region; with zero, subtraction
underflows. Thus its visible implementation requires a capacity precondition
larger than one. These are source deductions; the complete z88dk dot command
was not built or run. Its direct status readers also discard RX error bits,
so copying the polling style would lose UartZX's persistent gap detection.

### Decision

Adopt frame/time-based deadlines and cancellation in an application adapter
when that application's clock and UI contract make them reliable. Keep a
global operation deadline separate from inter-byte progress and reset only
the latter. Use `uart_try_write` to check cancellation/deadlines and service
RX between attempts. Keep error capture, capacity preconditions and failure
returns in the transport; do not import `exit`, ROM clock assumptions or
keyboard dependencies into the core. No C driver or timer state is added to
UartZX. This comparison is source inspection and arithmetic, not a target
execution benchmark or a claim that newt fails in its normal use.

## 6. Moon Rabbit ZX: related AY and ZX-Uno drivers

### Sources, relationship and API

Read the pinned [AY driver](https://github.com/nihirash/moon-rabbit-zx/blob/2db3c17174907536aa2fb6efcb64f23745bd6f5a/drivers/ay-uart.asm)
and [ZX-Uno driver](https://github.com/nihirash/moon-rabbit-zx/blob/2db3c17174907536aa2fb6efcb64f23745bd6f5a/drivers/uno-uart.asm).
They are another application use of the Nihirash routine family, not an
independent clean-room design. Matching loops show technical relationship;
they do not establish the full historical derivation. This repository also
has its own [licence text](https://github.com/nihirash/moon-rabbit-zx/blob/2db3c17174907536aa2fb6efcb64f23745bd6f5a/LICENSE),
which differs from the absence of a licence recorded for netman-zx. No code
is imported and no licence conclusion is transferred between repositories.

Both drivers expose a public `read` wrapper that loops until its raw reader
returns carry. That is a deliberately blocking interface. A caller that needs
UI/deadline progress must call `uartRead` or wrap it in its own bounded loop;
using public `read` as UartZX's polling API would change behavior.

### AY: what the family shares and where UartZX diverges

Moon Rabbit keeps runtime `_baud=11`, a two-byte cache pair and timed AY port
loops. Init sets the mixer, makes CTS low, enables interrupts and waits for
255 HALTs; it does not perform a bounded receive drain during that wait.
That initialization owns boot pacing and interrupt assumptions which UartZX
deliberately leaves to the application. UartZX establishes an idle port and
returns without HALT or AT traffic, with an optional mixer-preserving entry.

As with the z88dk reference, Moon Rabbit delays around stop bits without
validating them, and a false second start can return to the sample before
spending the timeout. `compare_moon.py` runs the original AY source after
syntax-only module/numeric/alternate-AF conversion in the same AY model:
`0x41` with a bad stop is returned as success, a grounded RX returns `0x00`,
and a chattering second start remains in the retry loop after 200000 T-states
with interrupts disabled. These conditions are covered by UartZX regressions.

The TX bit loop has three fine-tuning NOPs, and the end of write exchanges the
register sets. Its ABI therefore cannot be inferred from UartZX's preservation
rules. This review does not claim a calibrated full TX comparison or substitute
its instruction counts into UartZX. The useful role is provenance and a second
view of the historical design, not a reason to remove later safety work.

### ZX-Uno: cached readiness versus the current hardware contract

| Property | Moon Rabbit ZX-Uno | UartZX divMMC/ZX-Uno |
|---|---|---|
| Status | Bit 7 RX-ready, bit 6 TX-busy | Same wire bits, documented stable status read |
| RX state | `poked_byte`, `byte_buff`, `is_recv` (3 bytes) | No RX cache; one required TX failure byte |
| TX-side RX handling | Records an available indication in `is_recv` | Does not consume RX while checking TX |
| TX wait | Until busy clears | Bounded; later sends refused after failure |
| Init drain | 255 raw-read attempts | Optional fixed 512-poll flush, caller owns startup wait |
| Read contract | Public blocking wrapper; raw paths can clobber HL | Immediate polling, DE/HL preserved |

The TX routine remembers readiness rather than consuming the pending byte.
With the current hardware's non-destructive status read and one held RX byte,
that flag is redundant: the next data-ready sample can observe the same byte.
The declared `byte_buff` has a read path, but this file never writes a received
byte into it or sets its `poked_byte` availability flag. It is not evidence
of a working second software receive slot. Keeping those bytes would add state
without increasing hardware capacity or protecting a new caller requirement.

When a UART is absent and the bus floats high, the TX-busy test has no deadline.
UartZX's existing missing-device and fail-stop tests cover that operational
case. The comparison does not assume that old cores had identical status-read
side effects; current hardware evidence, rather than source age, is the reason
the canonical driver can omit the legacy readiness/cache state.

### Decision

Retain the historical relationship in the source record and the explicit
hardware distinctions. Keep UartZX's immediate reads, state reduction,
bounded TX, idle initialization, RX stop checks and bounded false starts.
Do not copy its public blocking read, startup HALTs or unused cache storage.
AY RX edge cases were executed; ZX-Uno control flow/state ownership was
inspected against the already-tested current model. No complete Moon Rabbit
build, emulator application run or physical timing sweep is claimed.

## Adoption and evidence limits

The operational additions are optional Next leaves: TX idle (13 code bytes)
and effective fast-rate metadata (32 data bytes), with zero new persistent
state. The earlier global-budget flush and immediate TX leaves also match
concrete needs found in the comparison. Existing timed AY loops remain intact.
The later optional flow-control leaf adds 32 code bytes and no state; callers
explicitly enable or disable it after handling board/ESP prerequisites.

The six comparisons are separate decisions, not votes for one globally best
driver. NextSync, z88dk AY, the extracted UART.DRV body and Moon Rabbit AY
have bounded local execution experiments; NXtel and newt are source/logic
comparisons; ESPAT's extended SYS implementation and the external applications'
physical behavior remain untested. A later [UartZX hardware probe](next-probe.md)
validated RX flow and short RX/TX patterns on one real Next at 115200 and 2 Mbaud.
The later [complete battery](next-batch.md) passed 44 hardware cases and two hours
of symmetric endurance on that Next, including recovery after injected faults.
Its AT setup belongs to the diagnostic program; the library introduces no
automatic IRQ adapter, flow negotiation, AT parser or consumer migration.
Those require a concrete consumer contract
and validation at the layer that owns clocks, banks, buffers and recovery.
