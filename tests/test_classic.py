"""Execute real Classic/compatible instructions, including linked UART objects.

The external failure body matches SpecTalkZX 2140e6b. UART ports and frame
waits are modelled; no consumer checkout or hardware is changed.
"""

import re
import tempfile
import unittest
from pathlib import Path

from harness import ORG, ROOT, STACK, Driver, assemble, sjasmplus, source, z80asm
from test_divmmc import ZxUnoUart

EXAMPLE = ROOT / "examples/classic_uart.inc"
EFFECTS = """
_uart_tx_fail:
    xor a
    ld (_connection_state), a
    ld (_sntp_init_sent), a
    ld (_sntp_waiting), a
    ld a, 1
    ld (_uart_tx_failed), a
    ld (_status_bar_dirty), a
    scf
    ret
_frame_wait:
    ld hl, frames
    inc (hl)
    ret
; Consumer string loop and SNTP's two-digit sequence (clock_classic.c).
_uart_send_string:
    ld a, (hl)
    or a
    ret z
    push hl
    ld l, a
    call _ay_uart_send
    pop hl
    ret c
    inc hl
    jr _uart_send_string
classic_timezone:
    sub 10
    ld e, a
    ld l, '1'
    call _ay_uart_send
    ld a, e
    add a, '0'
    ld l, a
    jp _ay_uart_send
"""
STORAGE = """
_uart_tx_failed: defb 0
_connection_state: defb 0
_sntp_init_sent: defb 0
_sntp_waiting: defb 0
_status_bar_dirty: defb 0
frames: defb 0
"""


def symbols(text, linked=False):
    pattern = r"^(\w+)\s+= \$([0-9A-F]+) ;" if linked else r"^(\S+): EQU 0x([0-9A-Fa-f]+)$"
    return {name: int(value, 16) for name, value in re.findall(pattern, text, re.M)}


def classic_link(tmp, compatible):
    """Keep caller, selected UART, external effects and shared state separate."""
    exports = "_ay_uart_init, _ay_uart_send, uartRead"
    if compatible:
        exports += ", uart_write"
    (tmp / "caller.asm").write_text(
        f"SECTION code_user\nORG {ORG}\nEXTERN {exports}\n"
        "call _ay_uart_init\ncall uartRead\ncall _ay_uart_send\nret\n"
        "SECTION effects\nORG 0x9000\nSECTION bss_user\nORG 0xC000\n"
    )
    (tmp / "effects.asm").write_text(
        "SECTION effects\nPUBLIC _uart_tx_fail, _frame_wait\nEXTERN _ay_uart_send\n" + EFFECTS
        + "SECTION bss_user\nPUBLIC _uart_tx_failed\n" + STORAGE
    )
    lines = ["SECTION code_user", f"PUBLIC {exports}",
             "EXTERN _uart_tx_fail, _uart_tx_failed, _frame_wait",
             f'INCLUDE "{EXAMPLE.as_posix()}"']
    if compatible:
        lines.append(f'INCLUDE "{source("divmmc_write.inc")}"')
    assemble(tmp, "linked", lines, "z88dk-z80asm", f"-I{ROOT / 'src'}",
             "-b", "-m", "-split-bin", "-o=linked.bin",
             "caller.asm", "linked.asm", "effects.asm")
    blocks = {ORG: (tmp / "linked_code_user.bin").read_bytes(),
              0x9000: (tmp / "linked_effects.bin").read_bytes(),
              0xC000: (tmp / "linked_bss_user.bin").read_bytes()}
    return blocks, symbols((tmp / "linked.map").read_text(), linked=True)


class ClassicUart(ZxUnoUart):
    def __init__(self, busy=0, absent=False, block_at=None):
        super().__init__()
        self.busy_samples, self.absent, self.block_at = busy, absent, block_at
        self.status_reads = self.data_reads = 0
        self.stacks = []

    def inp(self, port):
        assert port == 0xFD3B
        self.stacks.append(self.stack())
        if self.reg == 0xC7:
            self.status_reads += 1
            value = super().inp(port)
            if self.status_reads <= self.busy_samples or (
                self.block_at is not None and len(self.sent) >= self.block_at
            ):
                value |= 0x40
        else:
            assert self.reg == 0xC6
            self.data_reads += 1
            value = 0xFF if self.absent else super().inp(port)
        return 0xFF if self.absent else value

    def out(self, port, value):
        self.stacks.append(self.stack())
        if port == 0xFD3B:
            assert not self.absent
            assert self.block_at is None or len(self.sent) < self.block_at
        super().out(port, value)


class Classic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = {}
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder)
            for compatible in (False, True):
                lines = [f'    include "{EXAMPLE.as_posix()}"']
                if compatible:
                    lines.append(f'    include "{source("divmmc_write.inc")}"')
                lines += ["specimen_end:", EFFECTS, STORAGE]
                path = tmp / "specimen.inc"
                path.write_text("\n".join(lines))
                includes = [path.as_posix()]
                image = sjasmplus(tmp, "sj", includes)
                assert image == z80asm(tmp, "z80", includes)
                table = symbols((tmp / "sj.sym").read_text())
                assert table["specimen_end"] - ORG == 90 + 12 * compatible
                linked = classic_link(tmp, compatible)
                assert len(linked[0][ORG]) == 10 + 90 + 12 * compatible
                assert len(linked[0][0xC000]) == 6  # all external, not new driver state
                cls.images[compatible] = linked

    def driver(self, compatible=False, **device_options):
        uart = ClassicUart(**device_options)
        d = Driver((), uart, linked=self.images[compatible])
        uart.stack = lambda: d.m.sp
        d.state_reads = d.failures = 0

        def read(address):
            if address == d.symbols["_uart_tx_failed"]:
                d.state_reads += 1
            if address == d.symbols["_uart_tx_fail"]:
                d.failures += 1
            return d.m.memory[address]

        d.m.set_read_callback(read)
        for name in ("_uart_tx_failed", "_uart_tx_fail"):
            d.m.mark_addr(d.symbols[name], d.m.READ_MARK)
        return d, uart

    def invoke(self, d, entry, *, byte=0x5A, compatible=False, iff=False, flags=0xFF):
        d.m.iff1 = d.m.iff2 = iff
        d.m.memory[STACK - 32:STACK] = b"\xA5" * 32
        registers = dict(a=byte, bc=0x1111, de=0x22E7, hl=0x34A6 if compatible else 0x3400 | byte,
                         ix=0x5678, iy=0x9ABC, f=flags,
                         alt_af=0x1234, alt_bc=0x2345, alt_de=0x3456, alt_hl=0x4567)
        took = d.call(entry, **registers)
        m = d.m
        self.assertEqual((m.ix, m.iy, m.sp, m.iff1, m.iff2), (0x5678, 0x9ABC, STACK + 2, iff, iff))
        self.assertEqual((m.alt_af, m.alt_bc, m.alt_de, m.alt_hl), (0x1234, 0x2345, 0x3456, 0x4567))
        self.assertEqual(m.hl, registers["hl"])
        self.assertEqual(m.e, 0xE7)
        if compatible:
            self.assertEqual((m.a, m.bc, m.de), (byte, 0x1111, 0x22E7))
        else:
            self.assertEqual(bytes(m.memory[STACK - 32:STACK]), b"\xA5" * 32)
        return took

    def test_ready_byte_register_flags_stack_and_single_gate(self):
        for compatible in (False, True):
            entry = "uart_write" if compatible else "_ay_uart_send"
            for byte in (0, 0x5A, 0x80, 0xFF):
                for iff in (False, True):
                    for flags in (0, 0xFF):
                        with self.subTest(compatible=compatible, byte=byte, iff=iff, flags=flags):
                            d, uart = self.driver(compatible)
                            uart.feed(b"RX")
                            took = self.invoke(d, entry, byte=byte, compatible=compatible, iff=iff, flags=flags)
                            self.assertEqual(took, 235 if compatible else 150)
                            self.assertFalse(d.carry)
                            self.assertEqual((uart.sent, uart.status_reads, uart.data_reads), (bytes([byte]), 1, 0))
                            self.assertEqual((d.state_reads, d.failures), (1, 0))
                            self.assertEqual(min(uart.stacks), STACK - (6 if compatible else 0))
                            self.assertEqual((uart.waiting, bytes(uart.queue)), (ord("R"), b"X"))
                            self.assertEqual(uart.errors, [])

    def test_busy_timeout_absence_and_one_shot_effects(self):
        for compatible in (False, True):
            entry = "uart_write" if compatible else "_ay_uart_send"
            for busy, absent in ((1, False), (255, False), (256, False), (0, True)):
                with self.subTest(compatible=compatible, busy=busy, absent=absent):
                    d, uart = self.driver(compatible, busy=busy, absent=absent)
                    uart.feed(b"RX")
                    for name in ("_connection_state", "_sntp_init_sent", "_sntp_waiting"):
                        d.m.memory[d.symbols[name]] = 1
                    took = self.invoke(d, entry, compatible=compatible)
                    failed = busy == 256 or absent
                    self.assertLess(took, 12000)
                    self.assertEqual(d.carry, failed)
                    self.assertEqual(uart.status_reads, 256 if failed else busy + 1)
                    self.assertEqual((d.state_reads, d.failures), (1, int(failed)))
                    self.assertEqual(d.byte("_uart_tx_failed"), int(failed))
                    for name in ("_connection_state", "_sntp_init_sent", "_sntp_waiting"):
                        self.assertEqual(d.byte(name), int(not failed))
                    self.assertEqual(d.byte("_status_bar_dirty"), int(failed))
                    self.assertEqual((uart.sent, uart.data_reads), (b"" if failed else b"Z", 0))
                    if failed:
                        for state in (1, 2):
                            d.m.memory[d.symbols["_uart_tx_failed"]] = state
                            d.m.memory[d.symbols["_status_bar_dirty"]] = 0
                            io, reads = d.io, d.state_reads
                            self.invoke(d, entry, compatible=compatible)
                            self.assertEqual((d.carry, d.io, d.failures), (True, io, 1))
                            self.assertEqual(d.state_reads - reads, 1)
                            self.assertEqual((d.byte("_uart_tx_failed"), d.byte("_status_bar_dirty")), (state, 0))
                    self.assertEqual(uart.errors, [])

    def test_preexisting_states_reject_without_io_or_effects(self):
        for compatible in (False, True):
            for state in (1, 2, 0x41, 0xFF):
                with self.subTest(compatible=compatible, state=state):
                    d, uart = self.driver(compatible)
                    d.m.memory[d.symbols["_uart_tx_failed"]] = state
                    self.invoke(d, "uart_write" if compatible else "_ay_uart_send", compatible=compatible)
                    self.assertEqual((d.carry, d.io, d.state_reads, d.failures), (True, 0, 1, 0))
                    self.assertEqual(d.byte("_uart_tx_failed"), state)

    def test_scalar_rx_and_bounded_initialization(self):
        d, uart = self.driver()
        self.assertEqual(d.call("uartRead", de=0x12E7, hl=0x3456), 60)
        self.assertFalse(d.carry)
        for byte in (0, 0x7F, 0x80, 0xFF):
            uart.feed(bytes([byte]))
            d.elapsed += 304
            self.assertEqual(d.call("uartRead"), 103)
            self.assertEqual((d.carry, d.m.a, d.m.de, d.m.hl), (True, byte, 0x12E7, 0x3456))
        for absent in (False, True):
            with self.subTest(absent=absent):
                d, uart = self.driver(absent=absent)
                d.m.memory[d.symbols["_uart_tx_failed"]] = 2
                uart.feed(bytes(range(64)))
                self.assertLess(d.call("_ay_uart_init"), 100000)
                self.assertEqual((d.byte("frames"), uart.status_reads), (10, 523))
                self.assertEqual(d.byte("_uart_tx_failed"), 0)
                self.assertEqual(d.failures, 0)
                if absent:
                    self.assertEqual(uart.data_reads, 523)
                self.assertEqual(uart.errors, [])

    def test_classic_string_failure_and_inline_sntp_e_lifetime(self):
        command = b"CMD arg\r\n"
        for failed_at in (None, 0, 4, len(command) - 1):
            with self.subTest(failed_at=failed_at):
                d, uart = self.driver(block_at=failed_at)
                uart.feed(b"RX")
                d.m.set_memory_block(0xC100, command + b"\0")
                d.call("_uart_send_string", hl=0xC100)
                count = len(command) if failed_at is None else failed_at
                self.assertEqual((d.carry, uart.sent, d.m.hl), (failed_at is not None, command[:count], 0xC100 + count))
                self.assertEqual((d.failures, uart.data_reads), (int(failed_at is not None), 0))
                self.assertEqual(d.m.sp, STACK + 2)
        for number in (10, 13, 19):
            with self.subTest(timezone=number):
                d, uart = self.driver()
                d.call("classic_timezone", a=number)
                self.assertEqual(uart.sent, str(number).encode())
                self.assertEqual(d.m.e, number - 10)
                self.assertFalse(d.carry)

    def test_default_and_compatible_selections_and_send_helpers(self):
        command = b"CMD arg\r\n"
        for parts, size in ((["divmmc_fast.inc", "divmmc_tx_fail.inc"], 70),
                            (["divmmc_core.inc"], 82), (["divmmc.inc"], 163)):
            with self.subTest(parts=parts), tempfile.TemporaryDirectory() as folder:
                tmp = Path(folder)
                includes = [source(p) for p in parts + ["divmmc_vars.inc"]]
                image = sjasmplus(tmp, "sj", includes)
                self.assertEqual(image, z80asm(tmp, "z80", includes))
                self.assertEqual(len(image), size + 1)
                table = symbols((tmp / "sj.sym").read_text())
                self.assertEqual("uart_write" in table, parts != ["divmmc_fast.inc", "divmmc_tx_fail.inc"])
                if "uart_write" not in table:
                    uart = ClassicUart(absent=True)
                    d = Driver((), uart, linked=({ORG: image}, table))
                    uart.stack = lambda: d.m.sp
                    self.assertLess(d.call("uart_write_fast", hl=0x345A, de=0x22E7), 12000)
                    self.assertEqual((d.carry, d.byte("uart_tx_failed"), uart.status_reads), (True, 1, 256))
                    self.assertEqual((d.m.e, d.m.hl, d.m.sp), (0xE7, 0x345A, STACK + 2))
                    for state in (1, 2):
                        d.m.memory[table["uart_tx_failed"]] = state
                        d.io = 0
                        d.call("uart_write_fast")
                        self.assertEqual((d.carry, d.io, d.byte("uart_tx_failed")), (True, 0, state))
                    continue
                for helper in ("uart_write_string", "uart_write_block"):
                    for failed_at in (None, 0, 4, len(command) - 1):
                        uart = ClassicUart(block_at=failed_at)
                        d = Driver(parts + ["send.inc", "divmmc_vars.inc"], uart)
                        uart.stack = lambda: d.m.sp
                        uart.feed(b"RX")
                        d.m.set_memory_block(0xC100, command + b"\0")
                        d.call(helper, hl=0xC100, bc=len(command), de=0x12E7, ix=0x1234, iy=0x5678)
                        count = len(command) if failed_at is None else failed_at
                        self.assertEqual((d.carry, uart.sent, d.m.hl), (failed_at is not None, command[:count], 0xC100 + count))
                        if helper == "uart_write_block":
                            self.assertEqual(d.m.bc, len(command) - count)
                        self.assertEqual((d.m.de, d.m.ix, d.m.iy, d.m.sp), (0x12E7, 0x1234, 0x5678, STACK + 2))
                        self.assertEqual(uart.data_reads, 0)
                        self.assertEqual(d.byte("uart_tx_failed"), int(failed_at is not None))
                        if failed_at is not None:
                            d.io = 0
                            d.call("uart_write", a=ord("!"))
                            self.assertEqual((d.carry, d.io), (True, 0))
                        self.assertEqual(uart.errors, [])


if __name__ == "__main__":
    unittest.main()
