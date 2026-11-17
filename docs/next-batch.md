# One-launch Next validation battery

Build and collect with the same host tool and official BridgeZX device registry
as the [short and sustained probes](next-probe.md):

```text
python tools/next_probe.py build --batch --output work/next-batch-run
python tools/next_probe.py collect --device next --client-root C:/dev/BridgeZXadv --output work/next-batch-run
```

Start collection, transfer the generated `UXPROBE.NEX`, then launch it once on
the Next. The battery advances through its complete manifest without screen
reading or additional launches. It reconnects automatically for the planned TCP
disconnect. The default execution deadline is 9600 seconds from the first
connection; the initial connection wait has its own bounded budget and does not
consume execution time. Planned reconnections retain the execution deadline.
On Windows it holds
a temporary system-awake request during collection and releases it on exit;
the display may still turn off. Leave the Next and PC powered and connected.

The generated `run.json` contains the ordered cases; `batch_plan.inc` is derived
from that same plan for the NEX. Each case must report its actual CPU/video-mode
readback, results and completion. Every planned case and final DONE are required
before the collector accepts a complete result. Missing cases, unexpected
disconnects and corrupted reports remain failures, even if earlier cases passed.
The collector also checks for EOF and a refused new connection after DONE
when the build manifest enables shutdown verification.
Recoverable failed cases remain failed in the report while bounded UART recovery
allows the remaining cases to run. An incomplete announced TX payload still
aborts: further telemetry cannot safely delimit the missing payload bytes.

## Workload

- Symmetric 8192-byte transfers in both directions, at 115200 and near 2000000,
  with the CPU at 3.5, 7, 14 and 28 MHz.
- Additional 50/60 Hz and unpaced producer-demand cases. Video timing register
  11, core and firmware are left alone; only the saved 50/60 Hz selection in
  peripheral register 05 is temporarily changed.
- An RX service pause with flow enabled, a silent peer timeout and an actual
  TCP reset followed by automatic reconnection and an exact 32-byte data challenge.
- An intentionally changed payload byte to check the diagnostic comparator.
- FIFO overflow with flow off, plus a deliberately wrong FPGA receive divisor
  to provoke framing errors. Both require guarded RX refusal and successful
  bounded flushing before the clean recovery cases.
- Two hours of endurance in five-minute segments, alternating the two UART
  rates: one accumulated hour per rate, with symmetric transfers and flow on.

Each 8192-byte TX uses four ESP `CIPSEND` commands of 2048 bytes, with continuous
pattern state across them. The RX parser stays active while commands, prompts,
TX payload and acknowledgements are processed. The timed CPU matrix and
endurance cases require intact data and measured RX service during payload TX.
Unpaced demand cases record overlap as an observation: the ESP can serialize
incoming IPD bursts before command responses.

The batch chunk deadline is 500 frame interrupts, long enough to exercise the
3.5 MHz parser and still bounded. Every timed endurance segment is shorter than
the 16-bit frame-counter wrap; the complete run may wrap that clock repeatedly.
Host byte totals accumulate independently. Original CPU/peripheral settings are
restored on teardown; the UART finishes at 115200 with flow off. No SD writes,
Wi-Fi credential changes, flash writes or ESP resets are needed.

## Evidence limits

Expected injected faults are recorded separately from unexpected errors. A
payload mismatch is a diagnostic-protocol check, not a claim that the UART
detects arbitrary payload corruption. A planned TCP reset is a real network
fault, not a physical radio outage. Other boards, firmware, physical CTS wiring
faults and AY/divMMC hardware require their own equipment or fixtures.

Maximum producer demand is measured through this AT diagnostic program. Its
payload throughput includes parser and command overhead; it does not establish
continuous line-rate saturation in both directions. Consumer applications,
rings, banking and interrupt ownership are separate integrations.

## Reconnect proof and first-run evidence

The reconnect test requires a newly opened host socket to send 32 pattern bytes.
The Next must receive all 32, match their pattern and sum (3341), and retain zero
UART/protocol errors. CLOSED and CONNECT notices are independent diagnostics;
their presence or order does not establish successful reconnection.

The first physical battery (token `461916424`) passed its first 14 cases,
including all four CPU selections, both UART rates, 50/60 Hz, unpaced demand,
paused RX and silence timeout. It transferred 3145728 B of intact symmetric
duplex payload in each direction, plus 4096 B during the RX-pause test.
The host then reconnected successfully and received valid reports, but the
notification-dependent reconnect check reported failure and stopped the old
batch. No endurance result is claimed from that run.

The firmware's actual UART notification order was not captured. A controlled
CPU reproduction with the old source demonstrated the diagnostic defect:
working connections failed with absent/reversed notices, while ordered notices
could pass before receiving new data. The repaired challenge check covers
absent, reversed, individual and both notices, rejecting absent, stale, corrupt,
truncated and oversized challenges. Regression tests also prove a failed case
remains failed when a clean case and DONE follow. A separate regression preserves
the active execution budget after a long initial connection wait.

Evidence is retained in `work/next-batch-run-01`, the scoped repair diff and
baseline source hashes. The repaired full 44-case image is in
`work/next-batch-run-02`; its completed physical result follows.

## Completed repaired battery: 2026-11-17

The repaired image (token `2099477556`, NEX SHA256
`d46d13a17c538fbddb5bcb8c00647e5e7d0bc9d632c731d9c2afa7b3353f30af`)
completed **44/44 cases**, received DONE and exited the collector with code 0:
`ok=true`, `complete=true`, no unexpected failure. Raw device values were
`0F=02`, `01=32`, `0E=04`, `11=00`.

| Group | Cases | Physical result |
|---|---:|---|
| CPU 3.5/7/14/28 MHz, both UART rates | 8 | Pass |
| 60 Hz, both UART rates at 28 MHz | 2 | Pass |
| Unpaced producer demand, both UART rates | 2 | Pass |
| RX pause, silence, TCP reset/challenge, corruption, overflow, framing | 6 | All expected observations detected |
| Clean transfers after the injected faults | 2 | Pass |
| Five-minute endurance segments alternating UART rates | 24 | Pass |

CPU readback uses the actual speed in NextReg 07 bits 5:4; frame-mode selection
uses NextReg 05 bit 2, per the
[official register reference](https://explorer.specnext.dev/registers).

| Endurance UART rate | Segments | Chunks | Bytes per direction | Host seconds | RX consumed during TX |
|---|---:|---:|---:|---:|---:|
| 115200 | 12 | 3240 | 26542080 | 3652.516 | 23224320 |
| 2000000 | 12 | 7102 | 58179584 | 3649.248 | 50862080 |

Endurance totaled 7301.764 s (2 h 1 min 41.764 s), with 10342 symmetric chunks
and **169443328 B of verified payload** across both directions. Every endurance
payload matched and reported zero mismatches/UART errors. Both rates therefore
have an accumulated hour of evidence within one continuously running process.

The injected corruption produced exactly one mismatch. Overflow reported `04`
and framing reported `40`; both refused guarded reads and completed a successful
flush, followed by clean transfers. The reconnect challenge reported count 32,
sum 3341 and zero errors. Its notice bits were 2 (CONNECT observed at proof time,
CLOSED absent), confirming why notice presence was an unsuitable success gate.

Overall traffic offered was 87994400 B toward the Next and 87982080 B toward the
host. These totals include the deliberately damaged inputs and the 32-byte
challenge; they are not all classified as clean user data. Timed phases summed
379869 frames, proving at least five global 16-bit clock rollovers while the
batch continued correctly. Active execution was 7710.640 s; initial connection
wait was accounted for separately.

DONE reported 115200, flow off, frame register `18` hex, no RX errors and step
10743. The collector released its temporary Windows awake request and closed
the connection. `run.json`, the generated plan, NEX, symbols, raw telemetry and
the complete summary remain in `work/next-batch-run-02`.

Software validation comprises 17 CPU/protocol regressions, 31 host regressions,
an independent repair review, and the final modular-selection test with both
assemblers and separate-object linking. The integration selection is
[`examples/next_flow_uart.inc`](../examples/next_flow_uart.inc): 367 code/read-only
bytes plus two caller-allocated state bytes. This closes the requested Next
library validation and integration; application migration remains separate.
