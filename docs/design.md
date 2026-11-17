# Canonical driver design

What each backend in `src/` takes from the application drivers listed in
`sources.md`, what it leaves out, and why. One section per backend, written
when that backend is designed.

## Shared contract

Every compatible full backend offers the same entry points, so the helpers
in `send.inc` and the applications do not care which one is included.
`<backend>_core.inc` supplies init/read/raw read/write and required safety;
the other routines are optional leaves. The README lists emitted costs and
`integration.md` documents selection, state ownership and SpecTalkZX adoption.

| Entry | In | Out |
|---|---|---|
| `uart_init` | | Driver ready, failure latches clear |
| `uart_read` | | CF=1 and A = byte, or CF=0: none waiting, or the stream is damaged |
| `uart_read_raw` | | As `uart_read`, ignoring earlier receive errors. On the Next and the AY, the read that latches a new error reports no byte |
| `uart_ready` | | CF=1 when `uart_read` would return a byte; nothing is consumed |
| `uart_write` | A = byte | CF=0 sent, CF=1 not sent. Preserves A, BC, DE, HL |
| `uart_flush` | | Discards pending input and clears receive errors on success. CF=1 (Next, AY): it gave up with input still arriving; reads stay refused until a flush completes or init |
| `uart_read_block` | HL = buffer, B = room | HL = next free position, D = room left, CF=1 buffer full |
| `uart_write_string` (`send.inc`) | HL = NUL-terminated text | CF=1 and HL at the unsent byte on failure |
| `uart_write_block` (`send.inc`) | HL = data, BC = length | CF=1, HL and BC at the unsent byte on failure |

`uart_read` preserves DE and HL. A failed `uart_write` latches
`uart_tx_failed` and later writes are refused until `uart_init`. An
application may alias that byte to its own nonzero failure state or transfer
the failure before clearing it, taking over fail-stop and all disconnect
effects. The default divMMC/Next destination stores 1; a nonzero state,
including application state 2, remains unchanged on later sends. divMMC's
fast selection can tail-jump directly to the application's first-failure
handler; see the tested 0/1/2 example in `integration.md`. On Next and AY an
application with its own recovery may use `uart_read_raw` and transfer
`uart_rx_errors` before clearing it. It then owns loss notification, bounded
purge and resynchronization; it need not include `uart_flush`. Errors noticed
during TX must also reach that owner. No routine
uses IX, IY or the alternate registers unless its header says so. divMMC and
Next implement this contract in full. A bit-banged port cannot: the AY
section lists its exceptions.

## divMMC / ZX-Uno (2026-11-14)

### Hardware model

Read from the ZX-Uno core (`mcleod-ideafix/zxuno_spectrum_core`,
`common/zxunouart.v` and `common/uart.v`) and the open divTIESUS CPLD
(`anarsoul/divtiesus_maple`, `cpld/`), whose register decode is the same file
apart from clock wiring.

- **A status read changes nothing.** The status byte is wired straight from
  `{rx_ready, tx_busy}`. Polling never consumes data.
- **The receiver holds one byte.** At the stop bit it sets RX-ready, raises
  RTS and stops sampling the line until the data register is read. So the ESP
  must honour CTS (SpecTalkZX and NetChessZX send
  `AT+UART_CUR=115200,8,1,0,2` for this): without it, back-to-back
  bytes are lost whatever the driver does; with it, nothing is lost however
  slowly the driver reads, and each data read is what lets the next byte
  start.
- **A data write while TX is busy is dropped** without any indication.
- **TX has no flow control input.** Busy lasts one byte (304 T-states at
  3.5 MHz). A longer wait means there is no UART: a floating bus reads `#FF`,
  busy for ever.
- **The baud rate is not programmable.** It is a parameter of the ZX-Uno
  core (115200); the driver has nothing to set.

The decode still declares an unused `leyendo_estado` register, which suggests
older cores reacted to a status read; those cores are not supported.

A missing UART cannot be told from the receive side: a floating bus that reads
`#FF` has RX-ready set, so `uart_ready` and `uart_read` report bytes for ever.
The "no UART" verdict is a failed `uart_write`; drain loops must be bounded.

### Feature ledger

| Feature | Found in | Decision | Reason |
|---|---|---|---|
| Lean read: status then data, 60 T empty, 103 T with a byte | NetChessZX, SpecTalkZX, BitStream | Taken | Status reads are free of side effects |
| Read preserves DE and HL | NetManZX (its timeout loop depends on it) | Taken | |
| One-byte RX cache: peek with value, stash before a TX wait | BridgeZXadv, NetManZX | Dropped | The receiver already holds the byte; the cache costs 29 T on every read |
| RX moved into a ring while TX waits | BitStream | Dropped | TX waits one byte at most and the receiver holds its byte meanwhile; the driver owns no ring |
| Ready without consuming, status only | NetChessZX, BitStream | Taken | |
| Bounded TX wait | all | Taken, 256 samples | Over four byte times even at 28 MHz. Longer budgets (4096 in NetManZX, 16384 in BitStream) only delay the "no UART" verdict |
| TX failure latch until re-init | SpecTalkZX, BitStream, NetManZX (`io_error`) | Taken | One gate before I/O; first timeout tail-jumps to the assembly-time destination. Default latches 1; application may own immediate effects. States 1/2 never repeat effects |
| Caller retries after a TX timeout | NetChessZX (C layer) | Dropped | On this hardware a timeout is not transient |
| Write preserves A, BC, DE, HL | NetManZX, BridgeZXadv (BC, DE) | Compatible A adapter | Optional 12 B wrapper around one L-input algorithm. Fast entry preserves E/HL for real Classic inline callers and avoids saves |
| Init without HALT, DI or EI | NetChessZX, BitStream | Taken | The driver does not depend on the interrupt setup |
| Init reads the status and data registers unconditionally ("prime") | BridgeZXadv, NetChessZX, SpecTalkZX, BitStream | Replaced by a guarded read: init falls into `uart_read` | In the HDL a status read does nothing and a data read only matters while a byte is waiting. 19 bytes less. Every application has only run on hardware with the unconditional reads |
| Report "no byte" when no UART answers | external audit | Not taken; documented in the header | Three variants were measured: reject status `#FF` (+9 bytes, +12 T-states on every read), gate on `uart_tx_failed` (+15 bytes, +22 T-states, only after a failed write), reject non-zero low status bits when RX-ready is set (+13 bytes, +16 T-states per byte). The last one would make reception depend on six status bits being zero on every CPLD image in the field, to help only a machine with no UART |
| Frame-paced drain inside init | BridgeZXadv, NetManZX, SpecTalkZX | Dropped | The application calls `uart_flush`, once per frame if it wants to wait |
| Flush of fixed length | SpecTalkZX | Taken, 512 reads | A flush that stops at the first empty poll (BridgeZXadv) ends after one byte: the next one is still a frame away |
| Fast bulk receive | BitStream (inline drain into its ring), BridgeZXadv (loop inlined in the application) | Taken as `uart_read_block` into a linear buffer | Ring-agnostic: the application passes the room up to its wrap point |
| Several status samples per idle-count step | BridgeZXadv (eight; +5 % measured on hardware) | Taken, four | Shortens the wait between a byte arriving and its read |
| String and block send | BitStream, NetChessZX (C), NetManZX | Taken, in `send.inc` for all backends | |
| Register constants for consumers that inline their own loop | BridgeZXadv | Taken | |
| C entry points (`_ay_uart_*`, `_net_uart_*`) | NetChessZX, SpecTalkZX, BitStream | Left to each application | Names and ABI differ per application |

An earlier recommendation of 4096 TX polls came from the Next at 9600 baud;
it does not apply to this hardware.

### Numbers

- 164 bytes with its state byte, 186 with `send.inc` (the copied BridgeZXadv
  driver was 195).
- Compatible core: 82 code bytes; fast core with default failure destination:
  70. The fast core with an application destination is 63 bytes. Classic's
  complete init/read/send specimen is 90 bytes, below the original 101.
- TX ready: fast L-input 150 T-states, zero internal stack; compatible A-input
  235 T-states, six temporary stack bytes (excluding the caller's return address).
  The compatible API formerly cost 198 T-states and four stack bytes; sharing
  the L-input algorithm trades +7 code bytes/+37 T-states/+2 stack bytes there
  for a Classic path with no integration overhead.
- `uart_read`: 60 T-states empty, 103 with a byte (measured by the test).
- `uart_read_block`: 361 T-states per byte in the model, which is the 304 of
  the wire plus 57 from a byte arriving to its data read.
- `uart_read_block` is new code.

### What adopting it changes in each application

- **BridgeZXadv:** `uartReady` returned the byte and `ui.asm` compares it;
  `net.asm` reads `Uart._is_recv` and `_rx_byte`. Both go: a peek needs a
  private byte on the application side. Init no longer drains.
- **NetManZX:** `src/drivers/zxuno.asm` keeps its `UartImpl` names over the
  driver. A failed `uart_write` sets `Uart.io_error` and clears
  `uart_tx_failed`, because NetManZX clears `io_error` itself once it has
  drained the input and aborted the damaged line. Init keeps its 50-frame
  drain and ends with `uart_flush`; `is_recv`/`byte_buff` go. NetManZX does
  not send `AT+UART_CUR=115200,8,1,0,2`.
- **NetChessZX:** its send returns the result in L and retries; the wrapper
  maps CF and the retries go.
- **SpecTalkZX, BitStream:** the latch is `uart_tx_failed` and a failure
  returns CF instead of jumping to `_uart_tx_fail`; BitStream's TX no longer
  feeds its ring.

## Spectrum Next (2026-11-14)

### Hardware model

Read from the Next core (`cores/zxnext/src/serial/uart.vhd`, `ports.txt`,
`nextreg.txt`).

- **FIFOs.** RX holds 512 bytes, TX 64. A write to a full TX FIFO is dropped.
  A data read from an empty RX FIFO returns 0.
- **Status bit 2 (overflow) and bit 6 (framing or parity)** are sticky and
  cleared at the end of any status read. Whoever reads status must keep what
  it saw; a reader that does not loses the error for everybody.
- **Status bit 5** is a tag stored with each byte ("received while an error
  was pending") and shown for the byte at the head of the FIFO. A status read
  never clears it.
- **Status bit 7 (break)** is the live state of the line, not an event.
- **Frame register bit 7** empties both FIFOs and clears the sticky bits.
  Bit 5 enables hardware flow control, which "the esp only honours on an
  issue 4 board".
- **Select register:** bit 6 picks the UART, bit 4 latches divisor bits
  16..14. Bit 5 is ignored by the core (every application writes `#30`).
- **Divisor** = system clock / baud. The clock depends on the video timing in
  NextReg `#11`: seven VGA settings from 28 to 33 MHz and 27 MHz for HDMI.
- **Reset.** NextReg `#02` bit 7 holds the ESP and the expansion bus in
  reset, so `uart_esp_reset` also resets any device on the bus.

### Feature ledger

| Feature | Found in | Decision | Reason |
|---|---|---|---|
| Inline status test plus a latch routine | BridgeZXadv (`READ_STATUS`) | Taken | Every status read destroys bits 2 and 6 |
| Error mask `#44` (overflow, framing) | BridgeZXadv | Taken | Exactly the bits a status read destroys; the only mask with a hardware gate |
| Bit 5 in the mask (`#64`) | NetManZX, NetChessZX, SpecTalkZX | Dropped | With every status read latching bits 2 and 6, the tag detects nothing new, and it stays set until the byte is read: a read that refuses on it would never move on. No hardware evidence either way |
| Bit 7, break, in the mask (`#E4`) | SpecTalkZX | Dropped | A level, not an event; the start of a break already raises a framing error. How the line reads with the ESP in reset is unknown |
| Errors latch and reads refuse until an explicit discard | BridgeZXadv, NetChessZX (C layer), NetManZX (`io_error`) | Taken | A stream with a hole is never delivered as whole |
| Recover inside the driver: mark a gap in the ring, drain the FIFO | SpecTalkZX | Taken as `uart_flush`; the gap marker stays in the application | The driver owns no ring. SpecTalkZX objected to a latch that only cleared itself when the FIFO ran empty; here the application clears it when it chooses |
| Ignore receive errors | BitStream | Available as `uart_read_raw` | |
| `uart_read_raw` returns the byte on the read that latches an error | external audit | Not taken; documented in the header | Measured: +13 bytes. Only BitStream reads raw, and it loses one poll per error |
| Lean callable read | NetChessZX (80 T-states, no gate) | Taken: 66 T raw, 88 T gated when empty | BridgeZXadv 117, SpecTalkZX 99 |
| One-byte RX cache, peek with value | BridgeZXadv | Dropped | The FIFO keeps the byte; `uart_ready` is status only |
| TX refuses while RX errors are latched | BridgeZXadv, NetChessZX (C layer) | Dropped | RX health does not decide whether a byte can be queued; the application can test `uart_rx_errors` |
| Bounded TX wait with a failure latch | SpecTalkZX, BitStream | Taken, 4096 samples | Over ten byte times at 9600 baud and 28 MHz (the NetManZX sizing). 256 samples (BridgeZXadv, NetChessZX) are under one byte there |
| RX moved into a ring during a TX wait | SpecTalkZX, BitStream | Application-owned | Driver remains ring-independent. A healthy TX wait is at most one byte time, but the full failure timeout is longer. With flow disabled, whole block sends read no input: with both FIFOs empty the model loses input past 575 TX bytes at 115200/3.5 MHz, or 329 at 2000000/28 MHz (218 T/byte loop); fast block send moves the latter to 573 (101 T/byte, no physical wait states). With 128 RX bytes pending even a 512-byte send overflows. `integration.md` specifies occupancy/time budgets, tested RX service before sends, and application polling with RX service for stalled TX |
| Baud tables: 115200, 230400, 2000000 | BridgeZXadv | Taken | 115200 in the driver; every other rate is a 16-byte file an application includes only if it uses it |
| Baud tables: 9600, 57600, 1152000 | NetManZX | Taken | Same |
| 2000000 baud table | BridgeZXadv and NetManZX disagree | The BridgeZXadv values | Nearest divisors; a test checks every table against the documented clocks |
| 230400 derived by halving 115200 | NetChessZX | Dropped | Same values as the table |
| Divisor stored as ready-made port bytes | SpecTalkZX | Dropped | Only pays off with a single rate |
| Timing index masked with `and 7` | NetManZX, SpecTalkZX, BitStream | Taken | The field is three bits, the rest reserved zero |
| DI/EI around the NextReg select and read | BridgeZXadv, NetChessZX, NetManZX | Replaced | `uart_set_baud` reads the selection back and retries, so it never forces interrupts on |
| Drain inside init | BridgeZXadv, SpecTalkZX | Dropped | The frame reset empties the FIFOs |
| Flush | BridgeZXadv (to first empty poll), SpecTalkZX (512 reads; or to empty, 1024 at most) | Taken as a quiet-window flush | At 28 MHz the FIFO looks empty after every byte; a count can end with bytes left when the FIFO was full |
| Flush that gives up says so | none | Added | When `UART_FLUSH_MAX` bytes go by and more keep coming it returns CF=1 and latches `UART_RX_OVERFLOW`, whatever the latch held: reads stay refused until a flush completes, so a caller that ignores CF is not handed the tail of a burst the flush cut. Found by external audits |
| Fast bulk receive | BitStream (drain into its ring), BridgeZXadv (loop inlined in the application) | Taken as `uart_read_block` | Ring-agnostic; new code |
| ESP reset pulse, 25 frames | BridgeZXadv, NetChessZX, NetManZX | Taken | The boot wait and whatever follows stay with the caller |
| `IM 1` before the reset wait | NetChessZX | Left to the application | Specific to its C runtime |
| Baud scan and recovery after a reset | NetManZX | Left to the application | Needs the AT layer; the driver gives the tables, `uart_set_baud` and `uart_flush` |
| 28 MHz turbo | BridgeZXadv, BitStream | Left to the application | Not a UART matter |
| 50/60 Hz detection | NetChessZX | Left to the application | Timeout pacing, not a UART matter |
| Read timeouts counted in frames | NetManZX | Left to the application | Depends on the ROM frame counter and the keyboard |
| Hardware flow controls | NextSync configuration pattern | Optional `next_flow.inc` | 33 B, no state: change only ESP frame bit 5, restore UART selection and preserve IFF. Board/pin capability, ESP negotiation, deadlines and fallback stay in the caller; init keeps flow off |
| Block send with the status test inline | none | Added as `uart_write_block_fast`, optional file `next_write_block.inc` | 101 T-states a byte instead of 218 through `send.inc`, which at 28 MHz cannot keep up with 2000000 baud. Both counts leave out the Next's wait states. Same checks: a not-full status read that keeps the receive errors before every byte, the wait of `uart_write` on a full FIFO, the failure latch. A test runs both senders through the same five cases and compares every result. External audit |

### Numbers

T-states are plain Z80 counts measured by the tests, without the wait states
a Next adds at 28 MHz.

| | Canonical | BridgeZXadv | NetChessZX | SpecTalkZX |
|---|---|---|---|---|
| Read, nothing pending | 88 (66 raw) | 117 | 80 | 99 |
| Read, byte returned | 108 (86 raw) | not counted | not counted | not counted |
| Size | 341 bytes | 360 bytes | 239 bytes | 267 bytes |

- The 341 bytes include the 115200 table, interrupt-state-preserving reset
  controls and two state bytes; the duplex core alone is 194 bytes including
  state. Full code grew by 10 bytes from the pre-modularity 331-byte total.
  Each further rate adds 16. The 360 of BridgeZXadv include three tables.
- Nearest divisors still miss the rate. The worst case of each table:

  | Rate | Worst error | Video timing |
  |---|---|---|
  | 9600 | −0.02 % | HDMI, 27 MHz |
  | 57600 | −0.09 % | 29.46 MHz |
  | 115200 | +0.16 % | 30 MHz |
  | 230400 | −0.39 % | 28 MHz |
  | 1152000 | +1.90 % | HDMI, 27 MHz |
  | 2000000 | −3.57 % | HDMI, 27 MHz: divisor 14, 1,928,571 baud |

  The tests do not show whether the ESP accepts the 3.57 % at 2000000 baud
  over HDMI: the UART model does not derive its byte time from the divisor.
- `uart_write` takes 152 T-states when the FIFO has room.
- `uart_read_block` stores a byte every 98 T-states while the FIFO has data;
  at 2000000 baud and 28 MHz a byte arrives every 140.
- `next_write_block.inc` adds 46 bytes.
- `uart_read_block`, the quiet-window `uart_flush` and the selection
  read-back in `uart_set_baud` are new code.

### What adopting it changes in each application

- **BridgeZXadv:** `_rx_errors` becomes `uart_rx_errors`; `READ_STATUS`
  becomes a macro on its side built from the documented sequence;
  `uartReady` no longer returns the byte; `write` no longer refuses on RX
  errors; `setBaudNNN` becomes `uart_set_baud` with a table, followed by
  `uart_flush`; turbo moves to the application.
- **NetManZX:** `src/drivers/next.asm` keeps its `UartImpl` names over the
  driver. It reads through `uart_read_raw`, reads the status before each
  byte it sends, and moves `uart_rx_errors` and `uart_tx_failed` into
  `Uart.io_error`, which only its input drain clears. Baud scan and
  recovery call `uart_set_baud` with the table files; its hard reset is
  `uart_esp_reset`.
- **NetChessZX:** `_net_uart_rx_error` maps to `uart_rx_errors` (mask `#44`
  instead of `#64`); its reset wrapper keeps `IM 1` and the boot wait; the
  50/60 Hz tick count moves out of the driver.
- **SpecTalkZX:** when a read reports nothing and `uart_rx_errors` is set, it
  marks the gap in its ring and calls `uart_flush`; TX no longer feeds the
  ring.
- **BitStream:** receive errors now stop `uart_read`; it either handles
  `uart_rx_errors` or keeps its behaviour with `uart_read_raw`.

## AY bit-bang (2026-11-14)

### Hardware model

There is no HDL to read. The model (`tests/ay_model.py`) is built from what
the three drivers agree on and from the AY-3-8912 register set.

- **Wiring.** Port A, register 14: bit 3 is TX to the ESP, bit 2 is CTS to
  the ESP (low lets it send), bit 7 is RX. Register 7 bit 6 makes port A an
  output; an input bit reads the pin only while its latch is 1.
- **Ports.** `#FFFD` selects a register and reads it back, `#BFFD` writes it.
  The model decodes A15, A14 and A1 as a 128K does and records every address
  the driver uses.
- **Clock.** A bit at 9600 baud lasts 364.6 T-states at 3.5 MHz and 369.5 at
  the 3.5469 MHz of a 128K. Every timing test runs with both.
- **ESP.** 9600 baud with CTS flow control (`AT+UART_DEF=9600,8,1,0,2`). With
  CTS high it sends nothing; the model can also ignore CTS, which is how a
  second byte shows up.
- **Upstream.** NetManZX's driver comes from netman-zx by Alex Nihirash
  (`drivers/ay.asm`, commit `9cd6173`). It was read to settle what the
  applications changed; it is not redistributed here because that
  repository publishes no licence. Its timed bit loops are in `src/ay.inc`
  almost unchanged; the README licence section records what that means.

### Exceptions to the shared contract

- **`uart_ready`** only knows about a byte the driver already holds. CTS is
  high outside a read, so nothing is on the line to sample. CF=0 does not
  mean the ESP has nothing: an application polls with `uart_read`.
- **`uart_read`** waits up to 3.9 ms for a start bit with interrupts off, and
  uses the alternate registers. It preserves DE and HL like the others.
- **Interrupts** are enabled on return from every routine that touches the
  port. A frame interrupt that falls inside one is lost.
- **`uart_write`** always returns CF=0 and there is no `uart_tx_failed`: the
  port has no status. `uart_rx_errors` holds `UART_RX_FRAMING` (`0x40`) for
  a stop bit read low, or `UART_RX_RECOVERY` (`0x80`) after an incomplete
  flush. Recovery is not a synthetic framing error: a clean stream that
  exceeds the discard budget also needs a later completed flush or init.
- **`uart_read_block` and `uart_flush`** end with a 3.9 ms timeout instead of
  returning at once.
- **Placement.** The code and the stack must be in uncontended memory:
  `#8000`..`#BFFF`, or `#C000` and above with an uncontended RAM page there.
  On a 128K or +2 a contended page (1, 3, 5, 7) at `#C000` also delays every
  access to the register port `#FFFD`, wherever the code is.

### Feature ledger

| Feature | Found in | Decision | Reason |
|---|---|---|---|
| TX bit loop, 370 T-states, two stop bits | upstream, NetManZX, BitStream | Taken unchanged | 9586 baud on a 128K (0.1 % slow), 9459 at 3.5 MHz (1.5 % slow) |
| `uart_write` preserves A | BitStream | Taken | Shared contract |
| `uart_write` returns CF=0 | none | Added | `send.inc` stops on CF=1 |
| RX sampling loops | upstream, NetManZX, BitStream | Taken unchanged up to the eighth data bit | The wait after it is longer, for the stop-bit sample (see below) |
| 33 T-state delay inside start-bit detection | NetManZX | Dropped | It took the place of a debug border pulse (NetManZX `34c1324`); upstream and BitStream have no delay there. NetManZX has only run on hardware with it, so dropping it rests on the model: the driver accepts bit periods of 354.1 to 382.6 T without it and 358.1 to 386.6 with it; the real ones are 364.6 and 369.5, so without it the smaller margin grows from 1.8 % to 2.9 % (3.5 MHz) and from 3.1 % to 3.5 % (128K) |
| Second byte captured after CTS is raised | upstream, NetManZX, BitStream | Taken | The ESP may already have started it |
| A false start of the second byte goes back to waiting without counting | upstream, NetManZX, BitStream | Fixed: it uses up a poll | A line that keeps pulsing held `uart_read` for ever with interrupts off. No bytes, and no change for a real byte. Found by two external audits |
| CTS raised again on a timeout | NetManZX | Taken, followed by `UART_LATE_POLLS` samples (1.5 bit times) | BitStream leaves CTS low, so the ESP sends into nothing; upstream raises it and then still waits for a second byte. NetManZX, and this driver before the fix, return at once, so a byte the ESP starts just before it sees CTS rise is lost, or read from its middle by the next call: 8 of 1988 reply timings in the model, measured on that earlier version. A byte caught by the late samples accepts bit periods of 352.5 to 386.7 T since the stop-bit check (352.5 to 391.4 T before it, same method). `UART_START_POLLS` drops from 250 to 240 to keep the timeout at 3.9 ms. Found by an independent review |
| Main register set restored on every exit | NetManZX | Taken | BitStream returns from two exits with the alternate set selected |
| `uart_read` preserves DE and HL | none | Added | Shared contract. The pushes and pops are outside the timed code |
| Port value read back and modified | upstream, NetManZX, BitStream | Replaced by two constants | In init, upstream and BitStream read the latch through the data port and NetManZX through the register port, and all three write back what they read, TX low included. `#FE` and `#FA` are the values the read-modify-write produced after any write |
| CTS level between reads | low after init (upstream, NetManZX), high (BitStream) | High | The ESP keeps its data until somebody reads |
| Init waits 255 frames (upstream, NetManZX) or 50 (BitStream) | | Dropped | Waiting for the ESP to boot is the application's; stale input goes with `uart_flush` |
| Speed command sent by init | upstream, NetManZX, BitStream | Moved to `ay_speed.inc`, never called by the driver | It rewrites the ESP's flash and changes its rate for every other interface |
| Speed command at 30 T-states per bit | upstream (unrolled, 1680 bytes of code) | Taken, in 80 bytes of code | The 115200 baud of a factory ESP. Nine bits per character are written out and the stop bit carries the loop, so every bit uses the same port address as upstream |
| Speed command in a counted loop | NetManZX (56 T-states per bit), BitStream (65) | Dropped | 62,500 and 53,846 baud at 3.5 MHz: no ESP rate. See below |
| Bit delay in a variable | NetManZX (initialised data), BitStream (set by init) | Constant `UART_BIT_DELAY` | No application changes it; the state becomes zero-initialised |
| Ready by sampling the line | BitStream | Dropped | With CTS high the line is idle; a start bit caught by a sample is already too late to read |
| Block send with interrupts off throughout, IX as constant | BitStream | Dropped | Saves about 4 % per byte; `send.inc` does the job |
| Drain into the application ring | BitStream | Left to the application | `uart_read_block` is ring-agnostic |
| Mixer register written as `#FC` | upstream, NetManZX, BitStream | Taken in `uart_init` | It rewrites the whole register, tones A and B included. Kept because every application does it. `uart_init_keep_mixer` reads the register back and sets bit 6 only, for programs that play music |
| Stop bit checked | none | Added | Upstream and both applications store a byte after its eighth data bit, so a line held low read as a run of `#00` bytes. The stop bit is sampled 9.4 to 9.7 bit times after the start edge, as measured in the model, for the first byte and for a second one. A low one drops the byte and latches `UART_RX_FRAMING` in `uart_rx_errors`; reads stay refused until `uart_flush`, as on the Next. A read that gets a byte takes 152 T-states more, one that times out 22 more, for the gate. With `uart_init_keep_mixer` and the gate in `uart_ready` the driver grew by 80 bytes. External audit |
| Interrupt state preserved | none | Not taken | Every source returns with interrupts enabled |

### The speed command never reached a factory ESP

Upstream sends `AT+UART_DEF=9600,8,1,0,2` with one unrolled `OUTI` per bit:
30 T-states, 116,667 baud at 3.5 MHz, which an ESP at 115200 can read.
NetManZX (since its first commit) and BitStream replaced the unrolled code by
a counted loop of 56 and 65 T-states per bit. Neither rate is one an ESP
listens at, so in both applications the command is noise: they work only
with an ESP that already runs at 9600 baud.

`uart_esp_set_9600` restores the upstream timing. This is a behaviour change
with a persistent effect, which is why the driver never calls it: an
application that does will leave the ESP at 9600 baud for divMMC and Next
software too. At 30 T-states the rate is 1.3 % fast at 3.5 MHz and 2.6 % fast
on a 128K.

### Numbers

T-states measured by the tests on the Python `z80` emulator.

| | Canonical | Copied NetManZX driver |
|---|---|---|
| Size | 476 bytes, plus 360 for `ay_speed.inc` | 656 bytes |
| TX bit | 370 | 370 |
| `uart_write` | 4233 | not measured |
| `uart_read`, byte | 5495 | not measured |
| `uart_read`, timeout | 13742 (3.9 ms) | not measured |
| Bit period accepted | 353.9 to 383.6 | 358.1 to 386.6 |
| Speed command bit | 30 | 56 |

- `tests/test_ay.py` reads at both clocks with the ESP 2.5 % fast and 2.5 %
  slow, for nine positions of the start edge.
- The accepted bit period is now the range that three cases all pass: one
  byte at nine positions of the start edge (353.9 to 387.4 T), a byte pair
  at seven phases (353.5 to 383.6 T) and a byte caught by the late samples
  (352.5 to 386.7 T). Before the stop-bit check the same method gave 354.1
  to 383.3 T. The NetManZX figure comes from the earlier method.
- The whole speed command takes 8805 T-states.

### What adopting it changes in each application

- **NetManZX:** `src/drivers/ay.asm` keeps its `UartImpl` names over the
  driver. Its init calls `uart_init` and keeps the 255-frame wait for the
  ESP; it sends no speed command, so the ESP must already run at 9600 baud
  with CTS flow control, as it had to before. `_baud` and the second-byte
  variables are gone; `uartRead` keeps DE and HL. After a byte with a bad
  stop bit `uart_read` reports nothing until `uart_flush`: its read loop
  must call `uart_flush` when `uart_rx_errors` is set, as on the Next.
- **BitStream:** `ay_uart_ready` and `ay_uart_ready_fast` no longer sample
  the line: its drain polls with `uart_read` or uses `uart_read_block`.
  `ay_uart_send_block` becomes `uart_write_block`. The 50-frame wait and the
  speed command leave init. Its C wrappers stay on its side. Its drain
  must call `uart_flush` when `uart_rx_errors` is set, or reads stay
  refused after a byte with a bad stop bit; `uart_read_raw` skips that.
