"""divMMC / ZX-Uno backend against a UART model built from the ZX-Uno core HDL.

The model is stricter than an emulator: a status read changes nothing, the
receiver holds one byte until the data register is read, the ESP (honouring
CTS) delivers the next byte one frame after that read, and a data write while
TX is busy is an error. Timing is emulated T-states at 3.5 MHz, not hardware.
"""

import unittest
from collections import deque

from harness import Driver

PARTS = ("divmmc.inc", "send.inc", "divmmc_vars.inc")
FRAME = 304  # T-states per byte: 115200 baud at 3.5 MHz
BUFFER = 0xC000


class ZxUnoUart:
    def __init__(self):
        self.reg = 0
        self.waiting = None  # byte held by the receiver
        self.queue = deque()
        self.next_at = 0  # earliest arrival of the next queued byte
        self.busy_until = 0
        self.sent = bytearray()
        self.errors = []

    def feed(self, data):
        self.queue.extend(data)

    def _receive(self):
        if self.waiting is None and self.queue and self.clock() >= self.next_at:
            self.waiting = self.queue.popleft()

    def inp(self, port):
        if port != 0xFD3B:
            return 0xFF
        self._receive()
        if self.reg == 0xC7:
            return (0x80 if self.waiting is not None else 0) | (
                0x40 if self.clock() < self.busy_until else 0
            )
        if self.reg == 0xC6:
            value, self.waiting = self.waiting, None
            if value is None:
                self.errors.append("data read with nothing waiting")
                return 0
            self.next_at = self.clock() + FRAME
            return value
        return 0xFF

    def out(self, port, value):
        if port == 0xFC3B:
            self.reg = value
        elif port == 0xFD3B and self.reg == 0xC6:
            if self.clock() < self.busy_until:
                self.errors.append("data write while TX busy")
            else:
                self.sent.append(value)
                self.busy_until = self.clock() + FRAME


class NoUart:
    """An unattached port that always reads high."""

    def inp(self, port):
        return 0xFF

    def out(self, port, value):
        pass


class FloatingBus48K(NoUart):
    """Unattached port on a 48K with zero bitmap and attribute bytes."""

    def inp(self, port):
        line, phase = divmod((self.clock() - 14338) % Driver.FRAME, 224)
        if line < 192 and phase < 128 and phase % 8 < 4:
            return 0x00  # one of the ULA's four display fetches
        return 0xFF


class Divmmc(unittest.TestCase):
    def setUp(self):
        self.uart = ZxUnoUart()
        self.d = Driver(PARTS, self.uart)

    def tearDown(self):
        self.assertEqual(self.uart.errors, [])

    def test_read(self):
        d = self.d
        self.assertEqual(d.call("uart_read", de=0x1234, hl=0x5678), 60)
        self.assertFalse(d.carry)
        self.uart.feed(b"\x00")  # a zero byte is still a byte
        self.assertEqual(d.call("uart_read"), 103)
        self.assertTrue(d.carry)
        self.assertEqual((d.m.a, d.m.de, d.m.hl), (0, 0x1234, 0x5678))

    def test_ready_does_not_consume(self):
        d = self.d
        self.uart.feed(b"Z")
        for _ in range(3):
            d.call("uart_ready")
            self.assertTrue(d.carry)
        d.call("uart_read")
        self.assertEqual((d.carry, d.m.a), (True, ord("Z")))
        d.call("uart_ready")
        self.assertFalse(d.carry)

    def test_init_discards_stale_byte(self):
        d = self.d
        self.uart.feed(b"?")
        d.call("uart_init")
        d.call("uart_read")
        self.assertFalse(d.carry)

    def test_init_with_nothing_waiting(self):
        d = self.d
        d.m.memory[d.symbols["uart_tx_failed"]] = 1
        self.assertLess(d.call("uart_init"), 90)
        self.assertEqual(d.byte("uart_tx_failed"), 0)  # tearDown: no stray data read

    def test_write_waits_for_tx(self):
        d = self.d
        for byte in b"AT\r\n":
            d.call("uart_write", a=byte, bc=0x1111, de=0x2222, hl=0x3333)
            self.assertFalse(d.carry)
            self.assertEqual(
                (d.m.a, d.m.bc, d.m.de, d.m.hl), (byte, 0x1111, 0x2222, 0x3333)
            )
        self.assertEqual(self.uart.sent, b"AT\r\n")

    def test_write_keeps_a_waiting_byte(self):
        d = self.d
        self.uart.feed(b"K")
        d.call("uart_write", a=1)
        d.call("uart_write", a=2)  # waits a whole frame with RX pending
        d.call("uart_read")
        self.assertEqual((d.carry, d.m.a), (True, ord("K")))

    def test_write_fails_closed_on_a_high_bus(self):
        d = Driver(PARTS, NoUart())
        took = d.call("uart_write", a=0x55, bc=0x1111, de=0x2222, hl=0x3333)
        self.assertTrue(d.carry)
        self.assertLess(took, 12000)  # 256 samples, not a hang
        self.assertEqual(
            (d.m.a, d.m.bc, d.m.de, d.m.hl), (0x55, 0x1111, 0x2222, 0x3333)
        )
        self.assertTrue(d.byte("uart_tx_failed"))
        d.io = 0
        d.call("uart_write", a=0x56)
        self.assertTrue(d.carry)
        self.assertEqual(d.io, 0, "a latched driver must not touch the ports")
        d.call("uart_init")
        self.assertFalse(d.byte("uart_tx_failed"))

    def test_open_48k_bus_can_report_a_successful_write(self):
        d = Driver(PARTS, FloatingBus48K())
        d.elapsed = 14338  # active display
        d.call("uart_write", a=0x55)
        self.assertEqual((d.carry, d.byte("uart_tx_failed")), (False, 0))

    def test_flush_crosses_gaps(self):
        d = self.d
        self.uart.feed(bytes(range(40)))  # one frame apart: a gap after every byte
        d.call("uart_flush")
        self.assertFalse(d.carry)
        self.assertFalse(self.uart.queue)
        d.call("uart_read")
        self.assertFalse(d.carry)
        Driver(PARTS, NoUart()).call("uart_flush")  # bounded with nothing connected

    def test_read_block(self):
        d = self.d
        data = bytes((7 * i + 3) & 0xFF for i in range(300))

        took = d.call("uart_read_block", hl=BUFFER, b=32)  # nothing waiting
        self.assertEqual((d.carry, d.m.hl, d.m.d), (False, BUFFER, 32))
        self.assertLess(took, 80)

        self.uart.feed(data[:20])  # the line goes idle before the buffer fills
        d.call("uart_read_block", hl=BUFFER, b=32)
        self.assertEqual((d.carry, d.m.hl, d.m.d), (False, BUFFER + 20, 12))
        self.assertEqual(bytes(d.m.memory[BUFFER : BUFFER + 20]), data[:20])

        self.uart.feed(data)  # the buffer fills first; B=0 means 256
        took = d.call("uart_read_block", hl=BUFFER, b=0)
        self.assertEqual((d.carry, d.m.hl, d.m.d), (True, BUFFER + 256, 0))
        self.assertEqual(bytes(d.m.memory[BUFFER : BUFFER + 256]), data[:256])
        # Flow control paces the ESP: one frame per byte, plus the time from
        # the byte arriving to its data read (about 52 T-states).
        self.assertLess(took / 256, FRAME + 54)

        # The next byte is still on the wire: an immediate call returns empty.
        d.call("uart_read_block", hl=BUFFER, b=64)
        self.assertEqual((d.carry, d.m.hl), (False, BUFFER))
        d.elapsed += FRAME
        d.call("uart_read_block", hl=BUFFER, b=64)  # the rest was not lost
        self.assertEqual((d.carry, d.m.hl), (False, BUFFER + 44))
        self.assertEqual(bytes(d.m.memory[BUFFER : BUFFER + 44]), data[256:])

    def test_read_block_bounded_without_uart(self):
        d = Driver(PARTS, NoUart())
        d.call("uart_read_block", hl=BUFFER, b=16)
        self.assertEqual((d.carry, d.m.hl), (True, BUFFER + 16))

    def test_send_helpers(self):
        d = self.d
        text = 0xC100
        d.m.set_memory_block(text, b"AT+GMR\r\n\0")
        d.call("uart_write_string", hl=text)
        self.assertEqual((d.carry, d.m.hl), (False, text + 8))
        d.call("uart_write_block", hl=text, bc=2)
        self.assertEqual((d.carry, d.m.bc), (False, 0))
        self.assertEqual(self.uart.sent, b"AT+GMR\r\nAT")

        d = Driver(PARTS, NoUart())
        d.m.set_memory_block(text, b"AT\0")
        d.call("uart_write_string", hl=text)
        self.assertEqual((d.carry, d.m.hl), (True, text))
        d.call("uart_write_block", hl=text, bc=2)
        self.assertEqual((d.carry, d.m.hl, d.m.bc), (True, text, 2))


if __name__ == "__main__":
    unittest.main()
