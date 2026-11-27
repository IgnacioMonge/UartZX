"""The README examples assemble and run: the sjasmplus one to its end, the
z88dk one with its code and state linked to separate addresses."""

import re
import tempfile
import unittest
from pathlib import Path

from harness import ORG, ROOT, Driver, assemble, sjasmplus, z80asm
from test_divmmc import FloatingBus48K, NoUart, ZxUnoUart
from test_next import FRAMING, NextUart
from ay_model import AyUart

BSS = 0xC000  # the state, away from the code


def examples():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    src = f'"{(ROOT / "src").as_posix()}/'
    return [
        block.replace('"UartZX/src/', src)
        for block in re.findall(r"```asm\n(.*?)```", text, re.S)
    ]


class Esp(ZxUnoUart):
    """Answers a complete command line, if it has an answer."""

    answer = b""

    def out(self, port, value):
        super().out(port, value)
        if self.answer and self.sent.endswith(b"\r\n"):
            self.feed(self.answer)
            self.answer = b""


class Readme(unittest.TestCase):
    def test_portable_receive_step_for_all_backends(self):
        for backend, device in (("divmmc", ZxUnoUart), ("next", NextUart), ("ay", AyUart)):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                parts = (ROOT / "src" / f"{backend}_core.inc",
                         ROOT / "src" / f"{backend}_ready.inc",
                         ROOT / "examples" / "receive_once.inc",
                         ROOT / "src" / f"{backend}_vars.inc")
                includes = [part.as_posix() for part in parts]
                self.assertEqual(sjasmplus(tmp, "sj", includes), z80asm(tmp, "z80", includes))
                uart = device()
                d = Driver(parts, uart)
                if backend == "ay":
                    uart.interrupts = lambda: d.m.iff1
                d.call("uart_init")
                uart.feed(b"Q")
                d.call("uart_ready")
                self.assertEqual(d.carry, backend != "ay")
                d.m.iff1 = d.m.iff2 = False
                d.call("receive_step", hl=BSS)
                self.assertEqual((d.carry, d.m.hl, d.m.memory[BSS], d.m.iff1),
                                 (True, BSS + 1, ord("Q"), backend == "ay"))
                d.call("receive_step")
                self.assertEqual((d.carry, d.m.hl), (False, BSS + 1))
                self.assertEqual(uart.errors, [])

    def test_minimal_examples(self):
        for backend, block, size in zip(("divmmc", "next", "ay"), examples()[2:], (79, 187, 335)):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                path = tmp / "minimal.inc"
                path.write_text(block)
                image = sjasmplus(tmp, "sj", [path.as_posix()])
                self.assertEqual(len(image), size)
                self.assertEqual(image, z80asm(tmp, "z80", [path.as_posix()]))
        self.assertEqual(len(examples()), 5)

    def run_example(self, uart, start_tick=0):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.asm"
            path.write_text(examples()[0])
            d = Driver((path,), uart)
        d.elapsed = start_tick
        self.assertLess(d.call("start"), 4_000_000)  # bounded even with spurious input
        return d

    def test_example_confirms_only_a_complete_ok_line(self):
        for answer, present in (
            (b"", False),
            (b"O", False),
            (b"OK\r", False),
            (b"NOT OK\r\n", False),
            (b"ERROR\r\n", False),
            (b"OK\r\n", True),
            (b"AT\r\r\n\r\nOK\r\n", True),
        ):
            with self.subTest(answer=answer):
                uart = Esp()
                uart.answer = answer
                d = self.run_example(uart)
                self.assertEqual((uart.sent, uart.errors), (b"AT\r\n", []))
                self.assertEqual(d.carry, present)

    def test_example_rejects_display_bytes_on_an_open_48k_bus(self):
        d = self.run_example(FloatingBus48K(), start_tick=14338)
        self.assertEqual(d.byte("Uart.uart_tx_failed"), 0)  # apparent TX success
        self.assertFalse(d.carry)
        d = self.run_example(NoUart())
        self.assertEqual((d.carry, d.byte("Uart.uart_tx_failed")), (False, 1))

    def test_z88dk_example_links_code_and_state_apart(self):
        layout = ["    SECTION code_user", f"    ORG {ORG}"]
        layout += ["    SECTION bss_user", f"    ORG {BSS}"]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            assemble(
                tmp, "z88dk", layout + [examples()[1]],
                "z88dk-z80asm", f"-I{ROOT / 'src'}", "-b", "-m", "-split-bin", "-o=z88dk.bin", "z88dk.asm",
            )  # fmt: skip
            blocks = {
                ORG: (tmp / "z88dk_code_user.bin").read_bytes(),
                BSS: (tmp / "z88dk_bss_user.bin").read_bytes(),
            }
            symbols = {
                name: int(value, 16)
                for name, value in re.findall(
                    r"^(\w+)\s+= \$([0-9A-F]+) ;", (tmp / "z88dk.map").read_text(), re.M
                )
            }
        self.assertEqual(symbols["uart_tx_failed"], BSS)
        uart = NextUart()
        d = Driver((), uart, linked=(blocks, symbols))
        d.call("uart_init")
        uart.feed([None])  # a framing error
        d.call("uart_read")
        uart.tx_stuck = True
        d.call("uart_write", a=0x55)
        self.assertTrue(d.carry)
        self.assertEqual((d.byte("uart_tx_failed"), d.byte("uart_rx_errors")), (1, FRAMING))
        self.assertEqual(uart.errors, [])


if __name__ == "__main__":
    unittest.main()
