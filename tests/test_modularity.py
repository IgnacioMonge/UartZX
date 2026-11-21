"""Selected includes emit exactly their cost, including in a real z88dk link."""

import re
import tempfile
import unittest
from pathlib import Path

from harness import ORG, ROOT, Driver, assemble, sjasmplus, source, z80asm
from test_assembly import BACKENDS
from test_divmmc import NoUart, ZxUnoUart
from test_next import FRAMING, NextUart
from ay_model import AyUart

BSS = 0xC000
CORE = {"divmmc": 78, "next": 185, "ay": 326}
COMPLETE = {"divmmc": 158, "next": 322, "ay": 399}
ALL = {"divmmc": 180, "next": 624, "ay": 771}
STATE = {"divmmc": 1, "next": 2, "ay": 3}
LEAVES = {
    "divmmc": {"ready": 12, "flush": 12, "read_block": 56},
    "next": {"ready": 19, "flush": 42, "read_block": 45, "reset": 23,
             "esp_reset": 31, "baud_9600": 16, "try_write": 28, "flush_budget": 49,
             "tx_idle": 13, "baud_khz": 32, "flow": 32},
    "ay": {"ready": 10, "flush": 38, "read_block": 11},
}


def link(tmp, backend, parts):
    """An application references only init/read/write in a separate object.

    The driver object must contain precisely the selected includes; the
    linker does not remove unreferenced functions inside that object.
    """
    (tmp / "caller.asm").write_text(
        f"SECTION code_user\nORG {ORG}\n"
        "EXTERN uart_init, uart_read, uart_write\n"
        "call uart_init\ncall uart_read\ncall uart_write\nret\n"
        f"SECTION bss_user\nORG {BSS}\n"
    )
    lines = ["SECTION code_user", "PUBLIC uart_init, uart_read, uart_write"]
    lines += [f'INCLUDE "{source(part)}"' for part in parts]
    lines += ["SECTION bss_user", f'INCLUDE "{source(backend + "_vars.inc")}"']
    assemble(
        tmp, "linked", lines, "z88dk-z80asm", f"-I{ROOT / 'src'}",
        "-b", "-m", "-split-bin", "-o=linked.bin", "caller.asm", "linked.asm",
    )
    blocks = {ORG: (tmp / "linked_code_user.bin").read_bytes(),
              BSS: (tmp / "linked_bss_user.bin").read_bytes()}
    symbols = {name: int(value, 16) for name, value in re.findall(
        r"^(\w+)\s+= \$([0-9A-F]+) ;", (tmp / "linked.map").read_text(), re.M
    )}
    return blocks, symbols


class Modularity(unittest.TestCase):
    def check_configuration(self, backend, parts, code):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            includes = [source(part) for part in parts + (backend + "_vars.inc",)]
            image = sjasmplus(tmp, "sj", includes)
            self.assertEqual(image, z80asm(tmp, "z80", includes))
            self.assertEqual(len(image), code + STATE[backend])
            linked = link(tmp, backend, parts)
            blocks, symbols = linked
            self.assertEqual(len(blocks[ORG]), code + 10)  # caller costs 10 bytes
            self.assertEqual(len(blocks[BSS]), STATE[backend])
            for label in ("uart_ready", "uart_flush", "uart_read_block",
                          "uart_try_write", "uart_flush_budget", "uart_tx_idle",
                          "uart_flow_enable", "uart_flow_disable",
                          "uart_esp_reset", "uart_init_keep_mixer",
                          "uart_write_string", "uart_write_block"):
                present = bool(re.search(rf"^{label}: EQU", (tmp / "sj.sym").read_text(), re.M))
                self.assertEqual(label in symbols, present)
            return linked

    def test_next_flow_selection_example(self):
        parts = ((ROOT / "examples" / "next_flow_uart.inc").as_posix(),)
        self.check_configuration("next", parts, 355)

    def test_minimal_and_complete_assemble_and_link(self):
        for backend in CORE:
            for mode, code in (("_core", CORE[backend]), ("", COMPLETE[backend])):
                with self.subTest(backend=backend, mode=mode):
                    parts = (backend + mode + ".inc",)
                    linked = self.check_configuration(backend, parts, code)
                    device = {"divmmc": ZxUnoUart, "next": NextUart, "ay": AyUart}[backend]()
                    d = Driver((), device, linked=linked)
                    if backend == "ay":
                        device.interrupts = lambda: d.m.iff1
                    d.call("uart_init", ix=0x1234, iy=0x5678)
                    d.call("uart_write", a=0x55, bc=0x1111, de=0x2222, hl=0x3333)
                    self.assertEqual((d.carry, d.m.a, d.m.bc, d.m.de, d.m.hl),
                                     (False, 0x55, 0x1111, 0x2222, 0x3333))
                    self.assertEqual((d.m.ix, d.m.iy, d.m.sp), (0x1234, 0x5678, 0xFF02))
                    device.feed(b"Q")
                    d.call("uart_read")
                    self.assertEqual((d.carry, d.m.a, d.m.de, d.m.hl),
                                     (True, ord("Q"), 0x2222, 0x3333))
                    self.assertEqual((d.m.ix, d.m.iy, d.m.sp), (0x1234, 0x5678, 0xFF02))
                    if mode:
                        for label in ("uart_ready", "uart_flush", "uart_read_block",
                                      "uart_try_write", "uart_flush_budget",
                                      "uart_flow_enable", "uart_flow_disable",
                                      "uart_esp_reset", "uart_init_keep_mixer"):
                            self.assertNotIn(label, d.symbols)
                    self.assertEqual(device.errors, [])

    def test_independent_leaves_and_costs(self):
        for backend, leaves in LEAVES.items():
            for leaf, cost in leaves.items():
                with self.subTest(backend=backend, leaf=leaf):
                    self.check_configuration(
                        backend, (backend + "_core.inc", backend + "_" + leaf + ".inc"),
                        CORE[backend] + cost,
                    )
        for leaf, cost in (("string", 10), ("block", 12)):
            with self.subTest(send=leaf):
                self.check_configuration("divmmc", ("divmmc_core.inc", "send_" + leaf + ".inc"), CORE["divmmc"] + cost)
        self.check_configuration("ay", ("ay_init_keep_mixer.inc", "ay_core.inc"), CORE["ay"] + 14)
        # The fast sender does not pull in the generic block/string sender.
        self.check_configuration("next", ("next_core.inc", "next_write_block.inc"), CORE["next"] + 46)

    def test_minimal_safety_does_not_depend_on_optional_code(self):
        d = Driver(("divmmc_core.inc", "divmmc_vars.inc"), NoUart())
        self.assertLess(d.call("uart_write", a=0x55), 12000)
        self.assertEqual((d.carry, d.byte("uart_tx_failed")), (True, 1))
        d.io = 0
        d.call("uart_write", a=0x56)
        self.assertEqual((d.carry, d.io), (True, 0))
        uart = NextUart()
        d = Driver(("next_core.inc", "next_vars.inc"), uart)
        d.call("uart_init")
        uart.feed([None, 0x41])
        d.call("uart_write", a=0x55)
        self.assertEqual((d.carry, d.byte("uart_rx_errors")), (False, FRAMING))
        d.call("uart_read")
        self.assertFalse(d.carry)
        # Application policy can recover without uart_flush or a quiet wait.
        d.call("uart_read_raw")
        self.assertEqual((d.carry, d.m.a), (True, 0x41))
        self.assertEqual(d.byte("uart_rx_errors"), FRAMING)
        uart.tx_stuck = True
        self.assertLess(d.call("uart_write", a=0x56), 400000)
        self.assertEqual((d.carry, d.byte("uart_tx_failed")), (True, 1))
        uart.tx_stuck = False
        d.io = 0
        d.call("uart_write", a=0x57)
        self.assertEqual((d.carry, d.io), (True, 0))
        uart = AyUart(stuck_low=True)
        d = Driver(("ay_core.inc", "ay_vars.inc"), uart)
        uart.interrupts = lambda: d.m.iff1
        d.call("uart_init")
        d.call("uart_read")
        self.assertEqual((d.carry, d.byte("uart_rx_errors")), (False, FRAMING))
        d.io = 0
        d.call("uart_read")
        self.assertEqual((d.carry, d.io), (False, 0))
        self.assertEqual(uart.errors, [])

    def test_all_optional_code_has_no_additional_state(self):
        for backend, parts in BACKENDS.items():
            self.check_configuration(backend, parts[:-1], ALL[backend])

    def test_spectalk_state_alias_preserves_failure_effects(self):
        for backend, device in (("divmmc", NoUart()), ("next", NextUart())):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                text = "uart_tx_failed EQU _uart_tx_failed\n"
                text += f'    include "{source(backend + "_core.inc")}"\n'
                text += f'    include "{Path(__file__).with_name("spectalk_adapter.inc").as_posix()}"\n'
                text += "specimen_end:\n"
                # Consumer effects, outside the measured UART specimen.
                text += "_frame_wait: ret\n_uart_tx_fail:\n"
                text += "    ld a, (disconnects)\n    inc a\n    ld (disconnects), a\n"
                text += "    ld a, 1\n    ld (_uart_tx_failed), a\n    scf\n    ret\n"
                text += "_uart_tx_failed: defb 0\ndisconnects: defb 0\n"
                if backend == "next":
                    text += "uart_rx_errors: defb 0\n"
                path = tmp / "specimen.inc"
                path.write_text(text)
                includes = [path.as_posix()]
                self.assertEqual(sjasmplus(tmp, "sj", includes), z80asm(tmp, "z80", includes))
                d = Driver((path,), device)
                self.assertEqual(d.symbols["specimen_end"] - ORG, CORE[backend] + 41)
                self.assertEqual(d.symbols["uart_tx_failed"], d.symbols["_uart_tx_failed"])
                if backend == "next":
                    device.tx_stuck = True
                d.call("_ay_uart_send", l=0x55)
                self.assertEqual((d.carry, d.byte("uart_tx_failed"), d.byte("disconnects")),
                                 (True, 1, 1))
                for state in (1, 2):
                    d.m.memory[d.symbols["_uart_tx_failed"]] = state
                    d.io = 0
                    d.call("_ay_uart_send", l=0x56)
                    self.assertEqual((d.carry, d.io, d.byte("uart_tx_failed"), d.byte("disconnects")),
                                     (True, 0, state, 1))


if __name__ == "__main__":
    unittest.main()
