"""AY-3-8912 port A wired as a serial port, as the AY backend expects it.

Port A (register 14): bit 3 = TX to the ESP, bit 2 = CTS to the ESP (low lets
it send), bit 7 = RX from the ESP. The port is an open-drain output, so RX
reads the line only while its latch bit is 1. Ports are decoded like a 128K:
only A15, A14 and A1 matter, and every address used is recorded.
"""

from collections import deque

T_3_5 = 3_500_000 / 9600  # T-states per bit at 9600 baud, 48K clock
T_128 = 3_546_900 / 9600  # 128K clock


class AyUart:
    def __init__(
        self,
        bit=T_3_5,
        flow=True,
        react=0,
        release=0,
        stuck_low=False,
        chatter=False,
    ):
        self.bit = bit  # T-states per bit as the ESP sends
        self.flow = flow  # the ESP honours CTS
        self.react = react  # T-states from CTS falling to the ESP's start bit
        self.release = release  # T-states after CTS rises it may still start one
        self.stuck_low = stuck_low  # RX shorted to ground
        # After the last frame every sample flips, starting low: each start
        # bit the driver sees is gone when it looks again.
        self.chatter = chatter
        self.flips = 0
        self.cts_falls = 0  # times the driver has let the ESP send
        self.reg = 0
        self.regs = [0] * 16
        self.queue = deque()  # bytes the ESP has to send
        self.frames = []  # (start clock, byte) put on the RX line
        self.next_free = 0  # end of the last frame
        self.cts_low_at = None
        self.cts_high_at = None  # CTS rose after cts_low_at
        self.tx = []  # (clock, level) every time TX changes
        self.ports = set()
        self.interrupts = lambda: False  # set by the test: are interrupts enabled?
        self.errors = []

    def feed(self, data, delay=0):
        """Give the ESP bytes to send, `delay` T-states from now."""
        self._advance()
        self.queue.extend((self.clock() + delay, byte) for byte in data)

    # --- ESP side -----------------------------------------------------
    def _advance(self):
        """Start every frame the ESP was free to start before now."""
        now = self.clock()
        while self.queue:
            fed, byte = self.queue[0]
            start = max(self.next_free, fed)
            if self.flow:
                if self.cts_low_at is None:
                    return
                start = max(start, self.cts_low_at + self.react)
                if self.cts_high_at is not None:
                    if start >= self.cts_high_at + self.release:
                        return
            if start > now:
                return
            self.queue.popleft()
            self.frames.append((start, byte))
            self.next_free = start + 10 * self.bit

    def rx_level(self):
        if self.stuck_low:
            return 0
        now = self.clock()
        if self.chatter and self.frames:
            if now >= self.frames[-1][0] + 10 * self.bit:
                self.flips += 1
                return 1 - self.flips % 2
        for start, byte in reversed(self.frames):
            if start <= now < start + 10 * self.bit:
                index = int((now - start) / self.bit)
                return (
                    0 if index == 0 else 1 if index == 9 else (byte >> (index - 1)) & 1
                )
            if start + 10 * self.bit <= now:
                break
        return 1

    # --- Z80 side -----------------------------------------------------
    def _access(self, port):
        self.ports.add(port)
        if self.interrupts():
            self.errors.append(f"port {port:04x} used with interrupts enabled")

    def inp(self, port):
        self._access(port)
        if port & 0xC002 != 0xC000:
            return 0xFF  # only the register port reads back
        if self.reg != 14:
            return self.regs[self.reg]
        self._advance()
        latch = self.regs[14]
        return (latch & 0x7F) | (0x80 if latch & 0x80 and self.rx_level() else 0)

    def out(self, port, value):
        self._access(port)
        if port & 0xC002 == 0xC000:
            self.reg = value & 0x0F
        elif port & 0xC002 == 0x8000:
            self._advance()
            before = self.regs[self.reg]
            self.regs[self.reg] = value
            if self.reg == 14:
                if (before ^ value) & 0x08 or not self.tx:
                    self.tx.append((self.clock(), (value >> 3) & 1))
                if value & 0x04:
                    if self.cts_low_at is not None and self.cts_high_at is None:
                        self.cts_high_at = self.clock()
                elif before & 0x04 or self.cts_low_at is None:
                    self.cts_low_at = self.clock()
                    self.cts_high_at = None
                    self.cts_falls += 1

    # --- what the ESP would have received -------------------------------
    def tx_level(self, clock):
        level = 1
        for at, value in self.tx:
            if at > clock:
                break
            level = value
        return level

    def sent(self, bit=T_3_5):
        """Decode TX as a receiver at `bit` T-states per bit would."""
        data, clock = bytearray(), 0
        falls = [
            at
            for (at, level), (_, prev) in zip(self.tx[1:], self.tx)
            if prev and not level
        ]
        for start in falls:
            if start < clock:
                continue  # an edge inside the byte just decoded
            byte = sum(self.tx_level(start + (1.5 + i) * bit) << i for i in range(8))
            if not self.tx_level(start + 9.5 * bit):
                raise AssertionError(
                    f"TX framing error in the byte starting at {start}"
                )
            data.append(byte)
            clock = start + 9.5 * bit
        return bytes(data)
