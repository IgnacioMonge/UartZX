# Next hardware telemetry probe

> The hardware runs recorded here used the driver code of 2026-11-17. A size
> pass on 2026-11-22 changed bytes in some of the routines the probe includes
> (listed under [evidence limits](integration.md#evidence-limits)) and the
> runs were not repeated; sizes quoted below are those of the run.

`examples/next_probe.asm` builds a standalone, sjasmplus-only `UXPROBE.NEX`.
`tools/next_probe.py` builds a run-matched image and collects its TCP diagnostic
stream. No Python dependencies beyond the standard library are needed on the
host; the build needs sjasmplus on PATH.

There are two builds: the short loss/backpressure matrix and `--soak`, a
sustained duplex run. Both use the same transfer, launch and telemetry path.

## Run

Build into a new directory, preserving that image and its `run.json` together:

```text
python tools/next_probe.py build --output work/next-probe-run
```

Transfer `UXPROBE.NEX` with the official BridgeZX client. BridgeZX transfers
files but does not launch them. Start the collector before launching the NEX:

```text
python tools/next_probe.py collect --device next --client-root C:/dev/BridgeZXadv --output work/next-probe-run --timeout 300
```

The collector reads the named machine's address from the official BridgeZX
configuration. It does not alter that configuration. Exit BridgeZX on the Next
and launch the NEX once, from the file browser or with `.nexload UXPROBE.NEX` in
its directory. The collector connects to the Next on TCP port 6145, so no inbound
PC listener or firewall opening is needed. Both machines must share a reachable
network and the ESP must already be associated with Wi-Fi.

The probe finds an inherited UART rate from the library's baud tables, uses
volatile `AT+UART_CUR`, selects active RX with `AT+CIPRECVMODE=0` and disables
extended IPD headers before opening a temporary diagnostic server. It does not
change credentials, write ESP flash, reset the ESP or write the Next's SD card.
The probe sends DONE before `AT+CIPSERVER=0` because the ESP may close the
active connection when it stops listening. It also issues `AT+CIPCLOSE=0`
for firmware that leaves the client connected, then restores the previous
CPU speed and leaves the UART at 115200 with flow disabled.
For new builds, the collector requires EOF on the original socket and a
refused new connection to port 6145 within a single budget of up to 12 seconds.
Success records `shutdown_verified: true` in `summary.json`. Older build
manifests retain their original completion rules.

A green border indicates successful report emission and ESP server-stop
acknowledgment. Check the host log for the port check and individual results.
Red indicates failure. An abort may require restarting the Next before
returning to another application.

## Measurements and limits

The matrix is 115200 and the video timing's effective rate near 2000000 baud,
each with flow disabled and enabled. Flow is enabled only for recognized board
IDs (NextReg 0F low nibble 2 or 3); other boards report explicit skips.

For RX, the host sends a deterministic 4096-byte pattern while the Next pauses
RX service for ten frames. The no-flow case deliberately exposes FIFO loss and
is recorded as an observation. A flow-enabled RX pass requires all bytes in
order, the expected sum, no mismatch and no UART RX error. TX sends a 2048-byte
pattern; the host compares every byte and records CRC32. A pass also requires
the device's count and sum to match. Busy polls, frame duration, raw board/core/
video values and the frame register are recorded. On a TX mismatch the
collector also records the received payload as hex in `telemetry.jsonl`.

Each 32-byte `UXP1` report carries its build's random token and a phase number;
READY must announce exactly 4096 RX bytes or 2048 TX bytes, and the paired
result must match its phase. The stream has an overall host deadline and each
UART phase has a 150-frame deadline. The token rejects stale images; it is not
authentication. This is a local diagnostic server, with no command shell or
general-purpose control API.

`telemetry.jsonl` is flushed as reports arrive. `summary.json` records assessments,
skips, failures and incomplete streams. `ok` can describe successful supported
rows while `complete` is false because board-dependent rows were skipped.
Missing DONE, missing rows, corrupted TX and failed configuration are failures.
Configuration announcements precede transitions and never prove a pass alone.

## Sustained duplex

```text
python tools/next_probe.py build --soak --output work/next-duplex-run
python tools/next_probe.py collect --device next --client-root C:/dev/BridgeZXadv --output work/next-duplex-run
```

Build, transfer and launch this image once, as above. Keep the PC collector
running until it writes `summary.json`. Soak builds use a longer collection
deadline. Logs are flushed per chunk, so progress and any incomplete run remain
inspectable without reading the Next's screen.

The sustained matrix uses flow enabled at 115200 and near 2000000. Each rate
runs for 15000 frame interrupts in 50 Hz mode, or 18000 in 60 Hz mode, selected
from NextReg 05 bit 2. These are nominal five-minute periods; actual host elapsed
time is also recorded. Frame-rate selection is documented in the
[official Next register reference](https://explorer.specnext.dev/documentation/manual/rev3/chapter/22).

Each chunk concurrently sends 8192 bytes from PC to Next and 2048 bytes from
Next to PC. The PC's sender uses 1024-byte packets paced at their nominal UART
wire time while the receiver reads the return stream. The Next services an
incremental active-IPD parser while waiting for AT replies, sending commands,
retrying busy TX and transmitting payload. The two payloads use independent
pattern state; the host compares every return byte and records CRC32.
The 2048-byte TX command also keeps the workload within the ESP8266
[documented CIPSEND limit](https://docs.espressif.com/projects/esp-at/en/release-v2.2.0.0_esp8266/AT_Command_Set/TCP-IP_AT_Commands.html).

Reports add `ready_duplex`, `result_duplex` and `phase_done` events. A chunk's
result describes RX count, sum, mismatches and UART errors; its reserved word
counts RX bytes consumed while the TX payload loop was active. That counter
proves overlap in the program; simply finishing two opposite transfers would
not. The collector requires positive aggregate overlap at each rate, every
chunk intact, both complete timed phases and DONE. It reports total bytes,
chunks, measured throughput, errors and overlap separately per rate.

Per-chunk counts remain 16-bit, with host totals accumulated separately so long
runs do not wrap byte counts. The frame clock is 16-bit and each phase is shorter
than a wrap. Individual chunks retain a 150-frame deadline, and the host bounds
the entire session. A missing result, short phase, zero overlap, corrupt payload
or failed configuration cannot be reported as a successful soak.

This tests sustained application traffic through the real ESP and UART, with
flow enabled. Packet pacing and AT command overhead are part of the workload;
measured payload throughput is not a claim of saturating the nominal baud in
both directions continuously. It does not force an ESP CTS stall or add a
consumer application, ring buffer or interrupt-driven UART driver.

The CPU tests execute the probe against an AT protocol fixture, including an
overflowing FIFO and a controlled producer. Host tests use loopback TCP. Neither
proves electrical RTS/CTS behavior: only results collected from a real board do.
The short matrix alone does not establish sustained reliability. Neither mode
establishes behavior on other video timings, ESP firmware or all CPU contention
conditions without their own hardware runs.

## Observed hardware run: 2026-11-17

Run token `1590479006`, raw NextRegs `0F=02`, `01=32`, `0E=04`, `11=00`.
The host received DONE and all four matrix rows; no capability skips occurred.

| Baud | Flow | RX after ten paused frames | TX checked by host |
|---|---|---|---|
| 115200 | Off | Overflow `04`; 1460 bytes processed, 990 mismatches; timeout | 2048/2048, exact pattern |
| 115200 | On | 4096/4096, zero mismatches or RX errors | 2048/2048, exact pattern |
| 2000000 | Off | Overflow `04`; 470 bytes processed; timeout | 2048/2048, exact pattern |
| 2000000 | On | 4096/4096, zero mismatches or RX errors | 2048/2048, exact pattern |

Both successful RX sums were `65189`; all TX CRC32 values were `bf495ecb`.
Flow-enabled RX took 25/14 frames respectively, including the ten-frame pause.
The final report was back at 115200, flow off (`frame=18` hex).
This confirms RX backpressure on this board with these settings. It does not
force the ESP to stall Next TX, prove sustained duplex operation or migrate a
consumer. The first run inherited passive RX and was invalid for RX assessment;
the probe now explicitly selects active RX, covered by the CPU regression.

### Shutdown verification: 2026-11-17

The first shutdown-check run (token `2336359729`) lost its TCP connection
before DONE and returned a wrong TX CRC at 2 Mbaud with flow off. Only the
CRC was retained. The revised probe (token `3872607984`, NEX SHA256
`6efb06a40f4c35fdbe8e08a700b35a741e33b6939c8b83bcdb5bfaa919845a74`)
completed all four rows with `ok=true`, `complete=true` and
`shutdown_verified=true`. The collector received DONE at 115200 with flow off,
observed EOF and confirmed port 6145 refused the verification connection.
All four TX payloads matched, including the previously failing row. This
run confirms shutdown on the tested ESP firmware. The earlier TX mismatch
did not recur, so its cause remains undetermined. Raw evidence is retained
locally in `work/next-probe-shutdown-20261117-201827`.

## Observed sustained duplex run: 2026-11-17

Run token `1680807726`, same raw board/core/video values as above. The official
BridgeZX transfer confirmed a 16896-byte NEX; SHA256
`d52f548849e32f13a20a4fd245b088906d7c3775965af104ab02bc59f6a9daee`.
After one manual launch the collector ran unattended through both phases and
received DONE. `ok=true`, `complete=true`, no skips and no failed chunks.

| UART baud | Host-measured phase | Chunks | PC to Next | Next to PC | RX consumed during payload TX |
|---|---|---|---|---|---|
| 115200 | 303.953 s | 402 | 3293184 B | 823296 B | 748665 B |
| 2000000 | 304.016 s | 1090 | 8929280 B | 2232320 B | 2200371 B |

All 1492 chunks had exact counts, matching RX patterns/sums, matching host TX
patterns/CRCs, zero mismatches and zero UART errors. Overlap was positive in
every chunk: 1702..1957 bytes at 115200 and 1680..2019 bytes at 2000000. Totals
were 12222464 B toward the Next, 3055616 B toward the PC and 2949036 B consumed
during TX. Each chunk's 2048-byte return payload had CRC32 `bf495ecb`.

Phase clocks reported 15003 and 15006 frames against targets of 15000. The host
phase measurements include command/report overhead, with RX/TX payload rates
of 10834.517/2708.629 B/s at 115200 and 29371.086/7342.771 B/s at 2000000.
These are workload throughput figures, including AT parsing and packet pacing,
not claims of maximum UART throughput. The final report returned to 115200,
flow off, frame register `18` hex. Collection exited successfully and closed its
socket and sender thread.

Raw evidence is preserved locally in
`work/next-soak-run-01/{run.json,telemetry.jsonl,summary.json,UXPROBE.NEX}`.
Software checks comprise eight CPU/protocol tests, fifteen host tests and a
separate read-only integration review. The 60 Hz duration branch has CPU
coverage; this physical run used the 15000-frame branch. This adds a roughly
ten-minute sustained duplex result on one Next. It does not establish hours of
stability, symmetric maximum load, forced ESP CTS stalls or a consumer's ring,
banking, interrupt and recovery behavior.
