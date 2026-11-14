"""Spectrum Next backend against a UART model built from the Next core HDL
(src/serial/uart.vhd) and ports.txt.

Modelled: 512-byte RX FIFO, 64-byte TX FIFO, sticky overflow and framing bits
cleared by any status read, the per-byte "received after an error" tag, FIFO
reset through the frame register, and the NextReg select/data pair. A write to
a full TX FIFO and a data read from an empty RX FIFO are errors. Timing is
emulated T-states, not hardware.
"""

import unittest
from collections import deque

from harness import Driver

BAUDS = (9600, 57600, 230400, 1152000, 2000000)
PARTS = (
    ("next.inc",)
    + tuple(f"next_baud_{baud}.inc" for baud in BAUDS)
    + ("send.inc", "next_vars.inc")
)
# System clock per video timing (NextReg 0x11), from the core's nextreg.txt.
CLOCKS = (28000000, 28571429, 29464286, 30000000, 31000000, 32000000, 33000000, 27000000)  # fmt: skip
BYTE = 304  # T-states per byte: 115200 baud at 3.5 MHz
BUFFER = 0xC000
OVERFLOW, FRAMING = 0x04, 0x40


class NextUart:
    def __init__(self):
        self.timing = 0
        self.nextreg = 0
        self.steal = 0  # times an "interrupt handler" moves the NextReg selection
        self.reset = []  # (clock, value) written to NextReg 0x02
        self.select = []
        self.frame = []
        self.divisor = 0
        self.fifo = deque()  # (byte, received after an error)
        self.arrivals = deque()  # (clock, byte or None for a framing error)
        self.overflow = self.framing = False
        self.tx_done = deque()  # completion times of queued TX bytes
        self.tx_stuck = False
        self.sent = bytearray()
        self.errors = []

    def feed(self, data, spacing=0):
        """Queue bytes `spacing` T-states apart, the first one now."""
        at = self.arrivals[-1][0] + spacing if self.arrivals else self.clock()
        for byte in data:
            self.arrivals.append((at, byte))
            at += spacing

    def _advance(self):
        now = self.clock()
        while self.arrivals and self.arrivals[0][0] <= now:
            _, byte = self.arrivals.popleft()
            if byte is None:
                self.framing = True
            elif len(self.fifo) >= 512:
                self.overflow = True
            else:
                self.fifo.append((byte, self.overflow or self.framing))
        while self.tx_done and self.tx_done[0] <= now:
            self.tx_done.popleft()

    def _tx_full(self):
        return self.tx_stuck or len(self.tx_done) >= 64

    def inp(self, port):
        self._advance()
        if port == 0x133B:
            status = (
                (0x40 if self.framing else 0)
                | (0x20 if self.fifo and self.fifo[0][1] else 0)
                | (0x10 if not self.tx_done else 0)
                | (0x08 if len(self.fifo) >= 384 else 0)
                | (0x04 if self.overflow else 0)
                | (0x02 if self._tx_full() else 0)
                | (0x01 if self.fifo else 0)
            )
            self.overflow = self.framing = False
            return status
        if port == 0x143B:
            if not self.fifo:
                self.errors.append("data read from an empty RX FIFO")
                return 0
            return self.fifo.popleft()[0]
        if port == 0x243B:
            return self.nextreg
        if port == 0x253B:
            return self.timing if self.nextreg == 0x11 else 0xA5
        return 0xFF

    def out(self, port, value):
        self._advance()
        if port == 0x133B:
            if self._tx_full():
                self.errors.append("write to a full TX FIFO")
            else:
                self.sent.append(value)
                start = self.tx_done[-1] if self.tx_done else self.clock()
                self.tx_done.append(start + BYTE)
        elif port == 0x143B:
            if value & 0x80:
                self.divisor = (self.divisor & 0x7F) | (value & 0x7F) << 7
            else:
                self.divisor = (self.divisor & 0x3F80) | value
        elif port == 0x153B:
            self.select.append(value)
        elif port == 0x163B:
            self.frame.append(value)
            if value & 0x80:
                self.fifo.clear()
                self.tx_done.clear()
                self.overflow = self.framing = False
        elif port == 0x243B:
            self.nextreg = value
            if self.steal:
                self.steal -= 1
                self.nextreg = 0x07
        elif port == 0x253B and self.nextreg == 0x02:
            self.reset.append((self.clock(), value))


class NoUart:
    def inp(self, port):
        return 0xFF

    def out(self, port, value):
        pass


class Next(unittest.TestCase):
    def setUp(self):
        self.uart = NextUart()
        self.d = Driver(PARTS, self.uart)

    def tearDown(self):
        self.assertEqual(self.uart.errors, [])

    def errors(self, d=None):
        return (d or self.d).byte("uart_rx_errors")

    def table(self, baud):
        address = self.d.symbols[f"uart_baud_{baud}"]
        memory = self.d.m.memory
        return [
            memory[address + 2 * i] | memory[address + 2 * i + 1] << 8 for i in range(8)
        ]

    def test_baud_tables_are_nearest_divisors(self):
        for baud in (115200,) + BAUDS:
            for clock, divisor in zip(CLOCKS, self.table(baud)):
                best = min(
                    (clock // baud, clock // baud + 1),
                    key=lambda d: abs(clock / d - baud),
                )
                self.assertEqual(divisor, best, (baud, clock))
                self.assertLess(divisor, 1 << 14)  # bits 16..14 stay zero

    def test_init_programs_the_uart(self):
        d = self.d
        for timing in range(8):
            self.uart.timing = timing
            self.uart.select.clear()
            self.uart.frame.clear()
            self.uart.feed(b"stale")
            d.m.memory[d.symbols["uart_tx_failed"]] = 1
            d.m.memory[d.symbols["uart_rx_errors"]] = OVERFLOW
            d.call("uart_init")
            self.assertEqual(self.uart.select, [0x30])
            self.assertEqual(self.uart.frame, [0x98, 0x18])
            self.assertEqual(self.uart.divisor, self.table(115200)[timing])
            self.assertEqual((d.byte("uart_tx_failed"), self.errors()), (0, 0))
            d.call("uart_read")
            self.assertFalse(d.carry)

    def test_set_baud(self):
        d = self.d
        for baud in BAUDS:
            for timing in (0, 4, 7):
                self.uart.timing = timing
                d.call("uart_set_baud", hl=d.symbols[f"uart_baud_{baud}"])
                self.assertEqual(self.uart.divisor, self.table(baud)[timing])

    def test_set_baud_survives_a_handler_using_nextregs(self):
        d = self.d
        self.uart.timing = 7
        self.uart.steal = 3
        d.call("uart_set_baud", hl=d.symbols["uart_baud_115200"])
        self.assertEqual(self.uart.divisor, 234)
        Driver(PARTS, NoUart()).call("uart_init")  # bounded without NextRegs

    def test_read(self):
        d = self.d
        self.assertEqual(d.call("uart_read_raw", de=0x1234, hl=0x5678), 66)
        self.assertFalse(d.carry)
        self.assertEqual(d.call("uart_read"), 88)
        self.assertFalse(d.carry)
        self.uart.feed(b"\x00Q")
        self.assertEqual(d.call("uart_read_raw"), 86)
        self.assertEqual((d.carry, d.m.a), (True, 0))
        self.assertEqual(d.call("uart_read"), 108)
        self.assertEqual(
            (d.carry, d.m.a, d.m.de, d.m.hl), (True, ord("Q"), 0x1234, 0x5678)
        )

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

    def test_overflow_stops_reception_until_flush(self):
        d = self.d
        self.uart.feed(bytes(600))  # 88 more than the FIFO holds
        d.call("uart_read")  # the read that sees the error reports no byte
        self.assertEqual((d.carry, self.errors()), (False, OVERFLOW))
        d.io = 0
        for routine in ("uart_read", "uart_ready"):
            d.call(routine)
            self.assertFalse(d.carry)
        d.call("uart_read_block", hl=BUFFER, b=8)
        self.assertEqual((d.carry, d.m.hl, d.m.d), (False, BUFFER, 8))
        self.assertEqual(d.io, 0, "a latched driver must not touch the ports")
        d.call("uart_read_raw")  # the raw read carries on
        self.assertTrue(d.carry)
        d.call("uart_flush", hl=0x4321)
        self.assertEqual((self.errors(), len(self.uart.fifo), d.m.hl), (0, 0, 0x4321))
        self.uart.feed(b"K")
        d.call("uart_read")
        self.assertEqual((d.carry, d.m.a), (True, ord("K")))

    def test_framing_error_is_latched_by_every_status_reader(self):
        for routine in ("uart_read", "uart_read_raw", "uart_ready", "uart_write"):
            with self.subTest(routine):
                uart = NextUart()
                d = Driver(PARTS, uart)
                uart.feed([None, 0x41])
                d.call(routine, a=0x55)
                self.assertEqual(self.errors(d), FRAMING)
                if routine == "uart_write":  # sending does not depend on RX
                    self.assertEqual((d.carry, uart.sent), (False, b"\x55"))
                else:
                    self.assertFalse(d.carry)
                self.assertEqual(uart.errors, [])

    def test_flush_takes_a_full_fifo_and_its_stragglers(self):
        d = self.d
        self.uart.feed(bytes(512))
        # Enough stragglers to outlast the drain: the FIFO looks empty between them.
        self.uart.feed(bytes(400), spacing=BYTE)
        d.call("uart_flush")
        self.assertFalse(self.uart.arrivals or self.uart.fifo)
        self.assertEqual(self.errors(), 0)
        Driver(PARTS, NoUart()).call("uart_flush")  # bounded on a floating bus

    def test_flush_reports_input_that_never_stops(self):
        d = self.d
        self.uart.feed(bytes(600))
        d.call("uart_read")
        self.assertEqual(self.errors(), OVERFLOW)
        self.uart.feed(bytes(5000), spacing=BYTE)  # more than UART_FLUSH_MAX
        d.call("uart_flush", hl=0x4321)
        self.assertTrue(d.carry, "it gave up")
        self.assertEqual((self.errors(), d.m.hl), (OVERFLOW, 0x4321))
        d.call("uart_read")
        self.assertFalse(d.carry)  # still refused
        d.call("uart_flush")  # the rest of the burst, then silence
        self.assertEqual((d.carry, self.errors()), (False, 0))
        self.assertFalse(self.uart.arrivals or self.uart.fifo)

    def test_write(self):
        d = self.d
        for byte in b"AT\r\n":
            took = d.call("uart_write", a=byte, bc=0x1111, de=0x2222, hl=0x3333)
            self.assertEqual((d.carry, took), (False, 152))
            self.assertEqual(
                (d.m.a, d.m.bc, d.m.de, d.m.hl), (byte, 0x1111, 0x2222, 0x3333)
            )
        self.assertEqual(self.uart.sent, b"AT\r\n")

    def test_write_waits_for_room(self):
        d = self.d
        data = bytes(range(100))  # more than the 64-byte TX FIFO
        for byte in data:
            d.call("uart_write", a=byte, de=0x2222)
            self.assertEqual((d.carry, d.m.de), (False, 0x2222))
        self.assertEqual(self.uart.sent, data)

    def test_write_fails_closed(self):
        d = self.d
        self.uart.tx_stuck = True
        d.call("uart_write", a=0x55, bc=0x1111, de=0x2222, hl=0x3333)
        self.assertTrue(d.carry)
        self.assertEqual(
            (d.m.a, d.m.bc, d.m.de, d.m.hl), (0x55, 0x1111, 0x2222, 0x3333)
        )
        self.assertTrue(d.byte("uart_tx_failed"))
        self.uart.tx_stuck = False
        d.io = 0
        d.call("uart_write", a=0x56)
        self.assertTrue(d.carry)
        self.assertEqual(d.io, 0, "a latched driver must not touch the ports")
        d.call("uart_init")
        d.call("uart_write", a=0x57)
        self.assertEqual((d.carry, self.uart.sent), (False, b"\x57"))

    def test_read_block(self):
        d = self.d
        data = bytes((5 * i + 1) & 0xFF for i in range(300))

        took = d.call("uart_read_block", hl=BUFFER, b=32)  # nothing waiting
        self.assertEqual((d.carry, d.m.hl, d.m.d), (False, BUFFER, 32))
        self.assertLess(took, 120)

        self.uart.feed(data[:20], spacing=BYTE)  # arrives while the block runs
        d.call("uart_read_block", hl=BUFFER, b=32)
        self.assertEqual((d.carry, d.m.hl, d.m.d), (False, BUFFER + 20, 12))
        self.assertEqual(bytes(d.m.memory[BUFFER : BUFFER + 20]), data[:20])

        self.uart.feed(data)  # already in the FIFO; B=0 means 256
        took = d.call("uart_read_block", hl=BUFFER, b=0)
        self.assertEqual((d.carry, d.m.hl, d.m.d), (True, BUFFER + 256, 0))
        self.assertEqual(bytes(d.m.memory[BUFFER : BUFFER + 256]), data[:256])
        self.assertLess(took / 256, 99)
        d.call("uart_read_block", hl=BUFFER, b=64)
        self.assertEqual((d.carry, d.m.hl), (False, BUFFER + 44))
        self.assertEqual(bytes(d.m.memory[BUFFER : BUFFER + 44]), data[256:])

    def test_read_block_stops_at_an_error(self):
        d = self.d
        self.uart.feed(b"abcde")
        self.uart.feed([None, 0x7A], spacing=4 * BYTE)  # after the five are stored
        d.call("uart_read_block", hl=BUFFER, b=32)
        self.assertEqual((d.carry, d.m.hl, self.errors()), (False, BUFFER + 5, FRAMING))
        self.assertEqual(bytes(d.m.memory[BUFFER : BUFFER + 5]), b"abcde")

    def test_esp_reset(self):
        d = self.d
        d.m.iff1 = d.m.iff2 = True
        d.call("uart_esp_reset")
        (start, hold), (end, release) = self.uart.reset
        self.assertEqual((hold, release, d.frames), (0x80, 0x00, 25))
        self.assertGreater(end - start, 24 * Driver.FRAME)
        self.assertTrue(d.m.iff1)

    def test_send_helpers(self):
        d = self.d
        d.m.set_memory_block(0xC100, b"AT+GMR\r\n\0")
        d.call("uart_write_string", hl=0xC100)
        self.assertEqual((d.carry, self.uart.sent), (False, b"AT+GMR\r\n"))


if __name__ == "__main__":
    unittest.main()
