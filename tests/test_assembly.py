"""Each backend assembles to the same bytes under sjasmplus and z88dk-z80asm.

Run: python -m unittest discover tests
"""

import tempfile
import unittest
from pathlib import Path

from harness import sjasmplus, source, z80asm

# backend: includes in layout order.
BACKENDS = {
    "divmmc": ("divmmc.inc", "send.inc", "divmmc_vars.inc"),
    "next": (
        "next.inc",
        "next_baud_9600.inc",
        "next_baud_57600.inc",
        "next_baud_230400.inc",
        "next_baud_1152000.inc",
        "next_baud_2000000.inc",
        "send.inc",
        "next_vars.inc",
    ),
    "ay": ("ay.inc", "ay_speed.inc", "send.inc", "ay_vars.inc"),
}


class Assembly(unittest.TestCase):
    def test_backends(self):
        for backend, parts in BACKENDS.items():
            with self.subTest(backend), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                includes = [source(part) for part in parts]
                ours = sjasmplus(tmp, "sj", includes)
                self.assertTrue(ours)
                self.assertEqual(
                    ours, z80asm(tmp, "z80asm", includes), "assemblers disagree"
                )


if __name__ == "__main__":
    unittest.main()
