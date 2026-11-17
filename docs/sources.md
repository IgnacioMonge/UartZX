# Sources compared

The canonical drivers were designed against the UART drivers of five
programs, as they stood on 2026-11-14 in each program's development
repository. Those development repositories are not all public, and their
licences differ, so the driver files themselves are not redistributed here:
`design.md` and `baseline-review.md` describe what each one does.

| Program | Files compared | Development commit | Assembler | Licence |
|---|---|---|---|---|
| NetManZX | `src/drivers/ay.asm`, `next.asm`, `zxuno.asm`, `src/modules/uart-common.asm` | `83f18b7` | sjasmplus | MIT |
| NetChessZX | `asm/uart/divmmc_uart.asm`, `next_uart.asm`, `src/spectrum/platform/uart.h` | `576e89e` | z88dk (z80asm, SDCC ABI) | GPL |
| BridgeZX | `server/drivers/divmmc.asm`, `next.asm` | `6f2ec39` | sjasmplus | MIT |
| SpecTalkZX | `asm/divmmc_uart.asm`, `next_uart.asm`, `asm/spectalk_asm/20_rx_ring_uart.asm` | `75331d6` | z88dk (z80asm) | GPLv2 |
| BitStream | `asm/divtiesus_uart.asm`, `next_uart.asm`, `ay_uart.asm` | `59855cd` | z88dk (z80asm) | MIT |

The design documents call the BridgeZX development version `BridgeZXadv`.

Also read, and not redistributed:

- netman-zx by Alex Nihirash, commit `9cd6173`: `drivers/ay.asm`, the origin
  of the NetManZX AY driver, was read in full; it also has `drivers/zxuno.asm`
  and `drivers/next.asm`. Its repository publishes no licence; on
  2026-11-16 it still had none, and `9cd6173` was its latest commit.
- ZX-Uno core (`mcleod-ideafix/zxuno_spectrum_core`): `common/zxunouart.v`,
  `common/uart.v`.
- divTIESUS CPLD (`anarsoul/divtiesus_maple`): `cpld/`.
- ZX Spectrum Next core (`SpectrumNext/ZX_Spectrum_Next_FPGA`):
  `cores/zxnext/src/serial/uart.vhd`, `ports.txt`, `nextreg.txt`.

The revisions of the hardware sources read on 2026-11-14 were not recorded.
On 2026-11-16 the hardware facts below were checked again at these
revisions:

| Source | Revision | Files, with the commit that last changed them |
|---|---|---|
| ZX Spectrum Next core, `master` | `b047011`, core 3.02.03, 2025-11-06 | `cores/zxnext/src/serial/uart.vhd` `c308f93`, `cores/zxnext/ports.txt` `e4d8961`, `cores/zxnext/nextreg.txt` `9eaa35d` |
| ZX-Uno core, `master` | `6302d3b`, 2020-08-22 | `common/zxunouart.v`, `common/uart.v`: `5664e74` |
| divTIESUS CPLD, `master` | `77932b8`, 2025-09-24 | `cpld/`: `77932b8` |

- Next: 512-byte RX and 64-byte TX FIFOs; the status bits, with overflow
  and framing cleared by a read; select bits 6 and 4; frame bit 7 empties
  the FIFOs; divisor = system clock / baud; the NextReg `#11` clocks; NextReg
  `#02` bit 7 resets the ESP and the expansion bus.
- ZX-Uno: the status byte is `{data_received, txbusy, 6'h00}`, a data write
  while TX is busy is ignored, and `uart.v` fixes the rate at 115200 baud
  (`BPS`).
- divTIESUS: registers `#C6` (data) and `#C7` (status), and the same
  `zxunouart.v` apart from its clock inputs.

Not compared: the C layer behind NetChessZX's `uart.h` and the receive loop
BridgeZX inlines in its `net.asm`. Both are application code built on the
driver.

## External comparison follow-up (2026-11-17)

The detailed [individual comparisons](external-comparisons.md) use these pinned
sources. Original external code is kept only as ignored working evidence,
not redistributed with UartZX. A source review is not a complete application
build or physical validation.

| Reference | Revision | Scope |
|---|---|---|
| Jari Komppa NextSync | `d80df1262f19e8cca4ae2a438b089ed800d497a7` | `sync/uart.s`, UART/AT paths in `sync/nextsync.c` |
| ZX-Next-Unite NextSync | `d7d52c159ae549f8b71c6e64459c2a248c2d7219` | `uart.asm`, UART setup, baud/flow negotiation and recovery in `nextsync.c` |
| z88dk plus3 RS232 | `0c92d7bdbd6119a151d03599a72ce1b8635d1703` | get/put/params assembly, `include/rs232.h` |
| TBBlue distribution | `3a892b03738cfb27eee01f33d09186d3215a5a4a` | `src/asm/uart/uartdrv.asm`, container; ESPAT Alpha 10 stub/container, not extended SYS body |
| NXtel | `9d84144fab283896b02070c947603f7f9775382e` | `src/esp.asm`, ESPAT API consumer and historical README |
| newt | `b150c809c7e0bff723fc4cca319c33d495cc96e0` | `uart.c`, `timer.h`, BREAK/cleanup in `main.c` |
| Moon Rabbit ZX | `2db3c17174907536aa2fb6efcb64f23745bd6f5a` | `drivers/ay-uart.asm`, `drivers/uno-uart.asm`, repository licence |
| Next FPGA TX completion | `01e322a39360f4ae89b0c575b7aacae0d2441416` | `cores/zxnext/src/serial/uart.vhd`: status bit 4 combines empty FIFO and idle transmitter |
