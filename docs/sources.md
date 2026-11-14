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
  and `drivers/next.asm`. Its repository publishes no licence.
- ZX-Uno core (`mcleod-ideafix/zxuno_spectrum_core`): `common/zxunouart.v`,
  `common/uart.v`.
- divTIESUS CPLD (`anarsoul/divtiesus_maple`): `cpld/`.
- ZX Spectrum Next core (`SpectrumNext/ZX_Spectrum_Next_FPGA`):
  `cores/zxnext/src/serial/uart.vhd`, `ports.txt`, `nextreg.txt`.

Not compared: the C layer behind NetChessZX's `uart.h` and the receive loop
BridgeZX inlines in its `net.asm`. Both are application code built on the
driver.
