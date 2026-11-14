"""The sjasmplus example in the README assembles and runs to its end."""

import re
import tempfile
import unittest
from pathlib import Path

from harness import ROOT, Driver
from test_divmmc import ZxUnoUart


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
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        example = re.search(r"```asm\n(.*?)```", text, re.S).group(1)
        example = example.replace('"UartZX/src/', f'"{(ROOT / "src").as_posix()}/')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.asm"
            path.write_text(example)
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


if __name__ == "__main__":
    unittest.main()
