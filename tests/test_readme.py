"""The README examples assemble and run: the sjasmplus one to its end, the
z88dk one with its code and state linked to separate addresses."""

import re
import tempfile
import unittest
from pathlib import Path

from harness import ORG, ROOT, Driver, assemble
from test_divmmc import ZxUnoUart
from test_next import FRAMING, NextUart

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
    def run_example(self, answer):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.asm"
            path.write_text(examples()[0])
            uart = Esp()
            uart.answer = answer
            d = Driver((path,), uart)
        took = d.call("start")
        self.assertEqual((uart.sent, uart.errors), (b"AT\r\n", []))
        return took, d

    def test_example_ends_with_or_without_an_answer(self):
        silent, _ = self.run_example(b"")
        self.assertLess(silent, 100_000)
        answered, d = self.run_example(b"OK\r\n")
        self.assertLess(answered, silent)
        self.assertEqual((d.carry, d.m.a), (True, ord("O")))

    def test_z88dk_example_links_code_and_state_apart(self):
        layout = ["    SECTION code_user", f"    ORG {ORG}"]
        layout += ["    SECTION bss_user", f"    ORG {BSS}"]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            assemble(
                tmp, "z88dk", layout + [examples()[1]],
                "z88dk-z80asm", "-b", "-m", "-split-bin", "-o=z88dk.bin", "z88dk.asm",
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
