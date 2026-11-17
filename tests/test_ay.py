"""AY bit-banged backend against a model of the AY port and an ESP sending at
9600 baud (tests/ay_model.py). Every bit is placed in emulated T-states, for
the 48K and the 128K clocks.
"""

import unittest
from itertools import product

from ay_model import BAD_STOP, T_128, T_3_5, AyUart
from harness import Driver

PARTS = ("ay.inc", "ay_speed.inc", "send.inc", "ay_vars.inc")
BUFFER = 0xC000
IDLE = 0xFE  # port A with TX and CTS high
FRAMING = 0x40  # uart_rx_errors after a stop bit read low
COMMAND = b"\r\nAT+UART_DEF=9600,8,1,0,2\r\n"


class NoAy:
    def inp(self, port):
        return 0xFF

    def out(self, port, value):
        pass


class Ay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Driver(PARTS, NoAy())

    def esp(self, **settings):
        """A fresh ESP and AY on the shared driver, after uart_init."""
        d = self.d
        uart = AyUart(**settings)
        uart.clock = d.clock
        uart.interrupts = lambda: d.m.iff1
        self.addCleanup(lambda: self.assertEqual(uart.errors, []))
        d.device = uart
        d.call("uart_init")
        uart.cts_falls = 0  # measure activity after init drives the idle latch
        d.io = 0
        return uart

    def read(self):
        d = self.d
        d.call("uart_read")
        return d.m.a if d.carry else None

    def test_init(self):
        d = self.d
        uart = self.esp()
        uart.regs[14] = 0x00  # TX low: a break towards the ESP
        d.m.memory[d.symbols["uart_second_avail"]] = 1
        d.frames = 0
        took = d.call("uart_init")
        self.assertEqual((uart.regs[7], uart.regs[14]), (0xFC, IDLE))
        self.assertEqual((d.byte("uart_second_avail"), d.frames), (0, 0))
        self.assertTrue(d.m.iff1)
        self.assertLess(took, 200)  # it does not wait

    def test_init_keep_mixer(self):
        d = self.d
        uart = self.esp()
        uart.regs[7] = 0x38  # tones A, B and C on, noise off: a tune playing
        uart.regs[14] = 0x00
        d.m.memory[d.symbols["uart_second_avail"]] = 1
        took = d.call("uart_init_keep_mixer")
        self.assertEqual((uart.regs[7], uart.regs[14]), (0x78, IDLE))
        self.assertEqual(d.byte("uart_second_avail"), 0)
        self.assertTrue(d.m.iff1)
        self.assertLess(took, 200)

    def test_model_port_direction_controls_pins_not_the_latch(self):
        uart = AyUart()
        now = 0
        uart.clock = lambda: now

        def write(register, value):
            uart.out(0xFFFD, register)
            uart.out(0xBFFD, value)

        write(14, 0x72)  # TX, CTS and RX latch low while the port is input
        uart.feed(b"Q")
        self.assertEqual((uart.cts_falls, uart.tx_level(now), uart.inp(0xFFFD)),
                         (0, 1, 0xFF))
        self.assertFalse(uart.frames)
        write(7, 0x40)  # the stored latch now drives the pins
        uart.out(0xFFFD, 14)
        self.assertEqual((uart.cts_falls, uart.tx_level(now), uart.inp(0xFFFD)),
                         (1, 0, 0x72))
        now = 100
        write(7, 0)  # input releases TX and CTS immediately
        self.assertEqual((uart.tx_level(now), uart.cts_high_at), (1, now))
        write(14, IDLE)
        write(7, 0x40)
        self.assertEqual((uart.cts_falls, uart.tx_level(now)), (1, 1))

    def test_music_must_keep_port_a_as_output(self):
        d = self.d
        uart = self.esp()
        d.m.iff1 = d.m.iff2 = False
        uart.out(0xFFFD, 7)
        uart.out(0xBFFD, 0x38)  # music accidentally releases port A
        d.call("uart_write", a=0x55)
        self.assertEqual(uart.sent(), b"")
        uart.feed(b"Q")
        self.assertIsNone(self.read())
        self.assertEqual(uart.cts_falls, 0)
        d.call("uart_init_keep_mixer")
        self.assertEqual(uart.regs[7], 0x78)
        self.assertEqual(self.read(), ord("Q"))

    def test_write(self):
        d = self.d
        uart = self.esp()
        data = bytes([0x00, 0xFF, 0x55, 0xAA, 0x41, 0x0D])
        for byte in data:
            first = len(uart.tx)
            took = d.call("uart_write", a=byte, bc=0x1111, de=0x2222, hl=0x3333)
            self.assertEqual((d.carry, took), (False, 4233))
            self.assertEqual(
                (d.m.a, d.m.bc, d.m.de, d.m.hl), (byte, 0x1111, 0x2222, 0x3333)
            )
            self.assertTrue(d.m.iff1)
            edges = [at for at, _ in uart.tx[first:]]
            self.assertEqual({(at - edges[0]) % 370 for at in edges}, {0})
        for clock in (T_3_5, T_128):
            self.assertEqual(uart.sent(clock), data)
        self.assertEqual((uart.cts_falls, uart.regs[14]), (0, IDLE))

    def test_read_times_out(self):
        d = self.d
        uart = self.esp()
        took = d.call("uart_read", de=0x1234, hl=0x5678)
        self.assertFalse(d.carry)
        self.assertEqual((d.m.de, d.m.hl, uart.regs[14]), (0x1234, 0x5678, IDLE))
        self.assertTrue(d.m.iff1)
        self.assertEqual(took, 13742)  # 3.9 ms
        d.device = NoAy()  # no AY at all: the bus floats high
        self.assertIsNone(self.read())

    def test_read_accepts_both_clocks_with_margin(self):
        d = self.d
        for clock in (T_3_5, T_128):
            for error in (-0.025, 0, 0.025):
                for react in range(0, 60, 7):  # where the start edge falls
                    for byte in (0x00, 0xFF, 0x55, 0xAA, 0x01, 0x80):
                        uart = self.esp(bit=clock * (1 + error), react=react)
                        uart.feed([byte])
                        d.call("uart_read", de=0x1234, hl=0x5678)
                        self.assertEqual(
                            (d.carry, d.m.a), (True, byte), (clock, error, react)
                        )
                        self.assertEqual((d.m.de, d.m.hl), (0x1234, 0x5678))
                        self.assertEqual(uart.regs[14], IDLE)
                        self.assertTrue(d.m.iff1)

    def test_read_takes_a_byte_started_as_it_gives_up(self):
        # CTS rises when a read gives up. A byte the ESP had already started,
        # or starts up to a bit time later, must still arrive whole.
        d = self.d
        for clock in (T_3_5, T_128):
            for error, release in product((-0.025, 0, 0.025), (0, round(clock))):
                for ready in range(12950, 13700, 7):
                    uart = self.esp(bit=clock * (1 + error), release=release)
                    uart.feed(b"OK", delay=ready)
                    got, idle = bytearray(), 0
                    while idle < 2:
                        d.call("uart_read", de=0x1234, hl=0x5678)
                        self.assertEqual((d.m.de, d.m.hl), (0x1234, 0x5678))
                        byte = d.m.a if d.carry else None
                        idle = idle + 1 if byte is None else 0
                        if byte is not None:
                            got.append(byte)
                    self.assertEqual(bytes(got), b"OK", (clock, error, release, ready))
                    self.assertTrue(d.m.iff1)

    def test_second_byte_is_kept(self):
        d = self.d
        for clock in (T_3_5, T_128):
            for error in (-0.025, 0, 0.025):
                uart = self.esp(bit=clock * (1 + error), flow=False)
                uart.feed(b"\x41\xa5", delay=500)  # it does not wait for CTS
                self.assertEqual(self.read(), 0x41)
                d.io = 0
                d.call("uart_ready")
                self.assertTrue(d.carry)
                self.assertEqual(self.read(), 0xA5)
                self.assertEqual(d.io, 0, "the kept byte needs no port access")
                d.call("uart_ready")
                self.assertFalse(d.carry)

    def test_read_is_bounded_on_a_chattering_line(self):
        d = self.d
        uart = self.esp(chatter=True)
        uart.feed(b"U")
        took = d.call("uart_read")  # false starts of a second byte
        self.assertEqual((d.carry, d.m.a), (True, ord("U")))
        self.assertLess(took, 5495 + 40 * 62)
        self.assertEqual((uart.regs[14], d.byte("uart_second_avail")), (IDLE, 0))
        self.assertTrue(d.m.iff1)
        self.assertIsNone(self.read())  # and of a first one

    def test_bad_stop_bit_stops_reception_until_flush(self):
        d = self.d
        for clock in (T_3_5, T_128):
            for error in (-0.025, 0, 0.025):
                uart = self.esp(bit=clock * (1 + error))
                uart.feed([0x41 | BAD_STOP])
                d.call("uart_read", de=0x1234, hl=0x5678)
                self.assertFalse(d.carry, (clock, error))
                self.assertEqual(d.byte("uart_rx_errors"), FRAMING)
                self.assertEqual((d.m.de, d.m.hl, uart.regs[14]), (0x1234, 0x5678, IDLE))
                self.assertTrue(d.m.iff1)
                uart.feed(b"K")
                d.io = 0
                for routine in ("uart_read", "uart_ready"):
                    d.call(routine)
                    self.assertFalse(d.carry)
                self.assertEqual(d.io, 0, "a latched driver must not touch the port")
                d.call("uart_read_raw")  # the raw read carries on
                self.assertEqual((d.carry, d.m.a), (True, ord("K")))
                d.m.memory[d.symbols["uart_second_avail"]] = 1  # kept by a raw read
                d.call("uart_ready")
                self.assertFalse(d.carry)
                d.m.memory[d.symbols["uart_second_avail"]] = 0
                d.call("uart_flush")
                self.assertEqual((d.carry, d.byte("uart_rx_errors")), (False, 0))
                uart.feed(b"Q")
                self.assertEqual(self.read(), ord("Q"))

    def test_bad_stop_bit_on_the_second_byte(self):
        d = self.d
        for clock in (T_3_5, T_128):
            for error in (-0.025, 0, 0.025):
                uart = self.esp(bit=clock * (1 + error), flow=False)
                uart.feed([0x41, 0xA5 | BAD_STOP], delay=500)
                self.assertEqual(self.read(), 0x41, (clock, error))  # it was whole
                self.assertEqual(d.byte("uart_second_avail"), 0)
                self.assertEqual(d.byte("uart_rx_errors"), FRAMING)
                self.assertIsNone(self.read())

    def test_flush_discards_damaged_bytes(self):
        d = self.d
        uart = self.esp()
        uart.feed([0x41, 0x42 | BAD_STOP, 0x43, 0x44 | BAD_STOP])
        d.call("uart_flush")
        self.assertEqual((d.carry, d.byte("uart_rx_errors")), (False, 0))
        self.assertFalse(uart.queue)
        uart.feed(b"K")
        self.assertEqual(self.read(), ord("K"))

    def test_ready_only_knows_the_kept_byte(self):
        d = self.d
        uart = self.esp()
        uart.feed(b"Q")  # held by the ESP: CTS is high
        d.call("uart_ready")
        self.assertFalse(d.carry)
        self.assertEqual((d.io, uart.cts_falls), (0, 0))
        self.assertEqual(self.read(), ord("Q"))

    def test_flush(self):
        d = self.d
        uart = self.esp()
        uart.feed(bytes(range(256)) + bytes(44))
        d.call("uart_flush")
        self.assertFalse(d.carry)
        self.assertFalse(uart.queue)
        uart.feed(b"K")
        self.assertEqual(self.read(), ord("K"))

        uart = self.esp(flow=False)  # ends on a kept byte
        uart.feed(b"abcd", delay=500)
        d.call("uart_flush")
        self.assertEqual(d.byte("uart_second_avail"), 0)

        d.device = NoAy()
        self.assertLess(d.call("uart_flush"), 14000)

    def test_flush_that_ends_at_its_limit_does_not_give_up(self):
        d = self.d
        uart = self.esp()
        uart.feed(bytes(d.symbols["UART_FLUSH_MAX"]))  # then silence
        d.LIMIT = 40_000_000
        try:
            d.call("uart_flush")
        finally:
            del d.LIMIT
        self.assertFalse(d.carry)
        uart.feed(b"K")
        self.assertEqual(self.read(), ord("K"))

    def test_flush_is_bounded_on_a_dead_line(self):
        d = self.d
        uart = self.esp(stuck_low=True)
        d.m.memory[d.symbols["uart_second_avail"]] = 1
        d.LIMIT = 40_000_000
        try:
            took = d.call("uart_flush")
        finally:
            del d.LIMIT
        self.assertLess(took, 18_000_000)  # 5 s
        self.assertEqual(uart.regs[14], IDLE)
        self.assertTrue(d.carry, "it gave up")
        self.assertEqual(d.byte("uart_second_avail"), 0, "nothing kept from before")
        self.assertEqual(d.byte("uart_rx_errors"), 0x80, "recovery is pending")
        uart.stuck_low = False
        uart.feed(b"stale")
        d.io = 0
        self.assertIsNone(self.read())
        d.call("uart_ready")
        self.assertEqual((d.carry, d.io), (False, 0))
        d.call("uart_flush")
        self.assertEqual((d.carry, d.byte("uart_rx_errors")), (False, 0))
        uart.feed(b"K")
        self.assertEqual(self.read(), ord("K"))

    def test_flush_limit_without_framing_also_requires_recovery(self):
        d = self.d
        uart = self.esp()
        uart.feed(bytes(d.symbols["UART_FLUSH_MAX"] + 2))
        d.LIMIT = 40_000_000
        try:
            d.call("uart_flush")
        finally:
            del d.LIMIT
        self.assertEqual((d.carry, d.byte("uart_rx_errors"),
                          d.byte("uart_second_avail")), (True, 0x80, 0))
        d.call("uart_init")
        self.assertEqual(d.byte("uart_rx_errors"), 0)

    def test_read_block(self):
        d = self.d
        data = bytes((7 * i + 3) & 0xFF for i in range(20))
        uart = self.esp()
        uart.feed(data)
        d.call("uart_read_block", hl=BUFFER, b=32)
        self.assertEqual((d.carry, d.m.hl, d.m.d), (False, BUFFER + 20, 12))
        self.assertEqual(bytes(d.m.memory[BUFFER : BUFFER + 20]), data)

        uart.feed(data)
        d.call("uart_read_block", hl=BUFFER, b=8)
        self.assertEqual((d.carry, d.m.hl, d.m.d), (True, BUFFER + 8, 0))
        d.call("uart_read_block", hl=BUFFER, b=0)  # 0 means 256
        self.assertEqual((d.carry, d.m.hl, d.m.d), (False, BUFFER + 12, 244))
        self.assertEqual(bytes(d.m.memory[BUFFER : BUFFER + 12]), data[8:])

        uart = self.esp(flow=False)  # full with a byte still kept
        uart.feed(b"xy", delay=500)
        d.call("uart_read_block", hl=BUFFER, b=1)
        self.assertEqual(
            (d.carry, d.m.hl, d.m.memory[BUFFER]), (True, BUFFER + 1, ord("x"))
        )
        self.assertEqual(self.read(), ord("y"))

        uart = self.esp(stuck_low=True)  # a dead line gives no bytes
        d.call("uart_read_block", hl=BUFFER, b=0)
        self.assertEqual((d.carry, d.m.hl, d.byte("uart_rx_errors")), (False, BUFFER, FRAMING))

    def test_esp_set_9600(self):
        d = self.d
        uart = self.esp()
        uart.reg = 3  # something else selected another AY register
        uart.ports.clear()
        took = d.call("uart_esp_set_9600")
        for clock in (3_500_000, 3_546_900):
            self.assertEqual(uart.sent(clock / 115200), COMMAND)
        edges = [at for at, _ in uart.tx]
        self.assertEqual(min(b - a for a, b in zip(edges[1:], edges[2:])), 30)
        self.assertEqual(uart.ports, {0xFFFD, 0xBEFD})  # OUTI puts B-1 on the bus
        self.assertEqual((uart.cts_falls, uart.regs[14], uart.regs[3]), (0, IDLE, 0))
        self.assertTrue(d.m.iff1)
        self.assertLess(took, 9000)

    def test_send_helpers(self):
        d = self.d
        uart = self.esp()
        d.m.set_memory_block(0xC100, b"AT+GMR\r\n\0")
        d.call("uart_write_string", hl=0xC100)
        self.assertEqual((d.carry, uart.sent()), (False, b"AT+GMR\r\n"))
        d.call("uart_write_block", hl=0xC100, bc=4)
        self.assertEqual((d.carry, d.m.bc, uart.sent()), (False, 0, b"AT+GMR\r\nAT+G"))


if __name__ == "__main__":
    unittest.main()
