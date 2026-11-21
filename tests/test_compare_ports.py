"""tools/compare_ports.py agrees a tree with itself and sees a changed driver."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness import ROOT

TOOL = ROOT / "tools" / "compare_ports.py"


def compare(base, *arguments):
    return subprocess.run([sys.executable, str(TOOL), str(base), *arguments],
                          capture_output=True, text=True)


class ComparePorts(unittest.TestCase):
    def test_a_tree_is_the_same_as_itself(self):
        done = compare(ROOT, "all", "--cases", "150")
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertEqual(done.stdout.count(" 0 different"), 3)

    def test_one_poll_less_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            shutil.copytree(ROOT / "src", src)
            core = src / "ay_core.inc"
            text = core.read_text()
            self.assertEqual(text.count("UART_START_POLLS    EQU 240"), 1)
            core.write_text(text.replace("UART_START_POLLS    EQU 240", "UART_START_POLLS    EQU 239"))
            done = compare(tmp, "ay", "--cases", "150")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("DIFFERENT", done.stdout)


if __name__ == "__main__":
    unittest.main()
