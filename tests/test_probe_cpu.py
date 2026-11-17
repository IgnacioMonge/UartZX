"""Execute the NEX probe against an AT fixture, not electrical RTS/CTS proof."""

import re
import subprocess
import tempfile
import unittest
from collections import deque
from pathlib import Path

from harness import Driver, ROOT, STACK, STOP
from test_next import NextUart


def pattern(count):
    state, data = 1, bytearray()
    for _ in range(count):
        data.append(state)
        state = (state >> 1) ^ (0xB8 if state & 1 else 0)
    return bytes(data)


class Esp(NextUart):
    def __init__(self, board=2, video=0):
        super().__init__()
        self.board = board
        self.video = video
        self.command = bytearray()
        self.payload = None
        self.length = 0
        self.reports = []
        self.tx_payloads = []
        self.controlled = deque()
        self.passive = True       # inherited BridgeZX state must be normalized
        self.ipd_info = True

    def inp(self, port):
        # A controlled producer is only a protocol fixture for flow-enabled RX.
        if port == 0x133B and self.controlled and not self.fifo and not self.arrivals:
            self.feed(bytes([self.controlled.popleft()]))
        if port == 0x253B:
            return {5: self.video, 0x0F: self.board, 0x11: 0, 1: 0x32, 0x0E: 0x0A, 7: 3}.get(self.nextreg, 0)
        return super().inp(port)

    def out(self, port, value):
        super().out(port, value)
        if port != 0x133B:
            return
        if self.payload is not None:
            self.payload.append(value)
            if len(self.payload) == self.length:
                payload, self.payload = bytes(self.payload), None
                self.last_payload = payload
                self.reply(b"\r\nSEND OK\r\n")
                if self.length == 32:
                    self.reports.append(payload)
                    self.report_ready(payload)
                    if payload[4] == 1:
                        if self.passive:
                            incoming = b"+IPD,0,4096\r\n"
                        elif self.ipd_info:
                            incoming = b'+IPD,0,4096,"127.0.0.1",1234:' + pattern(4096)
                        else:
                            incoming = b"+IPD,0,4096:" + pattern(4096)
                        if payload[6]:
                            self.controlled.extend(incoming)
                        else:
                            self.feed(incoming)
                else:
                    self.tx_payloads.append(payload)
            return
        self.command.append(value)
        if not self.command.endswith(b"\r\n"):
            return
        command = bytes(self.command)
        self.command.clear()
        if command.startswith(b"AT+CIPSEND=0,"):
            self.length = int(command.split(b",")[1])
            self.payload = bytearray()
            self.reply(b"\r\n> ")
        else:
            if command == b"AT+CIPRECVMODE=0\r\n":
                self.passive = False
            if command == b"AT+CIPDINFO=0\r\n":
                self.ipd_info = False
            self.reply(b"\r\nOK\r\n")
            if command == b"AT+CIPSERVER=1,6145\r\n":
                self.feed(b"0,CONNECT\r\n")


    def reply(self, data):
        self.feed(data)

    def report_ready(self, payload):
        pass


class SoakEsp(Esp):
    """AT interleaving only; RTS/CTS electrical behavior is not modeled."""

    def __init__(self, board=2, *, video=0, corrupt=False, truncate=False, no_rx=False):
        super().__init__(board, video)
        self.segments = deque()
        self.corrupt, self.truncate, self.no_rx = corrupt, truncate, no_rx
        self.busy_left = 0
        self.busy_injected = False
        self.prompt_during_rx = False
        self.send_ok_during_rx = False
        self.ready_ack = b""
        self.ready_ack_during_rx = False

    def report_ready(self, payload):
        if payload[4] != 9 or self.no_rx:
            return
        incoming = bytearray(pattern(8192))
        if self.corrupt:
            incoming[getattr(self, "corrupt_index", 1234)] ^= 1
        if self.truncate:
            del incoming[4096:]
        # Bytewise delivery fragments both prefixes and decimal lengths.
        for start in range(0, len(incoming), 1024):
            body = incoming[start:start + 1024]
            self.segments.append(f"+IPD,0,{len(body)}:".encode() + body)
        self.busy_injected = False
        if self.ready_ack:
            self.controlled.extend(self.segments.popleft())
            self.controlled.extend(self.ready_ack)
            self.ready_ack = b""
            self.ready_ack_during_rx = True

    def reply(self, data):
        if (data == b"\r\nSEND OK\r\n" and self.length == 32
                and self.last_payload[4] == 9 and not self.no_rx):
            self.ready_ack = data
            return
        if self.controlled:
            # AT text cannot split an IPD body; insert at the packet boundary.
            self.controlled.extend(data)
            if b">" in data:
                self.prompt_during_rx = True
            if b"SEND OK" in data:
                self.send_ok_during_rx = True
        else:
            self.feed(data)

    def inp(self, port):
        if port == 0x133B:
            if not self.controlled and self.segments and not self.fifo and not self.arrivals:
                self.controlled.extend(self.segments.popleft())
            if self.length == 2048 and self.payload is not None and len(self.payload) >= 64:
                if not self.busy_injected:
                    self.busy_left = 12
                    self.busy_injected = True
            value = super().inp(port)
            if self.busy_left:
                self.busy_left -= 1
                return value | 2
            return value
        return super().inp(port)


def assemble_probe(*defines, plan=None):
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        if plan is not None:
            (directory / "batch_plan.inc").write_text("probe_batch_plan:\n" + "\n".join(
                f"    dw {case[0]}\n    db {','.join(map(str, case[1:6]))},0\n"
                f"    dw {','.join(map(str, case[6:]))}" for case in plan) + "\n    dw 0\n")
        done = subprocess.run([
            "sjasmplus", "--nologo", "--msg=war", "--raw=probe.bin", "--sym=probe.sym",
            *(f"-D{value}" for value in defines),
            f"-I{directory.as_posix()}",
            f"-I{(ROOT / 'examples').as_posix()}", f"-I{(ROOT / 'src').as_posix()}",
            (ROOT / "examples/next_probe.asm").as_posix(),
        ], cwd=directory, text=True, capture_output=True)
        if done.returncode or done.stdout or done.stderr:
            raise AssertionError(done.stdout + done.stderr)
        code = (directory / "probe.bin").read_bytes()
        symbols = {name: int(value, 16) for name, value in re.findall(
            r"^(\S+): EQU 0x([0-9A-Fa-f]+)$", (directory / "probe.sym").read_text(), re.M)}
        return code, symbols


class BatchEsp(SoakEsp):
    """Change CPU/frame/wire clocks together; faults are injected, not electrical proof."""

    def __init__(self, blocked_cpu=None, *, reconnect_notices=b"\r\n0,CLOSED\r\n0,CONNECT\r\n",
                 reconnect_challenge=pattern(32), fragment_challenge=False):
        super().__init__()
        self.regs = {5: 0xA1, 7: 2}
        self.blocked_cpu = blocked_cpu
        self.reconnect_notices = reconnect_notices
        self.reconnect_challenge = reconnect_challenge
        self.fragment_challenge = fragment_challenge
        self.case_kind = 0
        self.corrupt_index = 128
        self.driver = None
        self.pending_framing = False
        self.framing_divisors = []

    def inp(self, port):
        if port == 0x253B and self.nextreg in self.regs:
            if self.nextreg == 7:
                actual = self.regs[7] if self.blocked_cpu is None else self.blocked_cpu
                return (actual << 4) | self.regs[7]
            return self.regs[self.nextreg]
        return super().inp(port)

    def out(self, port, value):
        if port == 0x253B and self.nextreg in self.regs:
            self.regs[self.nextreg] = value
            self.video = self.regs[5]
        super().out(port, value)
        if port == 0x143B and value & 0x80 and self.pending_framing and self.divisor == 2917:
            self.pending_framing = False
            self.framing_divisors.append(self.divisor)
            self.feed([None] + list(pattern(4096)), spacing=10)
        if self.driver is not None:
            scale = 1 << (self.regs[7] if self.blocked_cpu is None else self.blocked_cpu)
            self.driver.FRAME = int(69888 * scale * (5 / 6 if self.regs[5] & 4 else 1))
            self.byte = max(1, self.divisor * 10 * scale // 8)

    def report_ready(self, payload):
        event = payload[4]
        if event == 12:
            self.case_kind = payload[18]
            self.corrupt = self.case_kind == 4
            return
        if event == 14:
            if self.case_kind == 3:
                self.controlled.extend(self.reconnect_notices)
                if self.reconnect_challenge is not None:
                    body = self.reconnect_challenge
                    packets = (body[:13], body[13:]) if self.fragment_challenge else (body,)
                    for packet in packets:
                        self.controlled.extend(f"+IPD,0,{len(packet)}:".encode() + packet)
            elif self.case_kind == 6:
                self.pending_framing = True
            return
        super().report_ready(payload)


class BatchDriver(Driver):
    """Bound slices below the emulator's own 100k tick wrap at faster CPUs."""

    def call(self, name, **registers):
        m = self.m
        for register, value in registers.items():
            setattr(m, register, value)
        m.sp = STACK
        m.memory[STACK:STACK+2] = STOP.to_bytes(2, "little")
        m.pc = self.symbols[name]
        self.took = 0
        next_irq = self.elapsed + self.FRAME
        while m.pc != STOP:
            if self.took > self.LIMIT:
                raise AssertionError(f"{name}: did not return, PC={m.pc:04x}")
            m.ticks_to_stop = min(50_000, max(1, next_irq - self.clock()))
            self._slice(m.run)
            if self.clock() >= next_irq:
                if m.pc != STOP and m.iff1:
                    self.frames += 1
                    self._slice(m.on_handle_active_int)
                next_irq += self.FRAME
            elif m.halted and not m.iff1:
                raise AssertionError(f"{name}: HALT with interrupts disabled")
        took, self.took = self.took, 0
        self.elapsed += took
        return took


class BatchCpu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Same ABI as the host manifest; short phases cover every transition.
        records = [(0, rate, cpu, hz, 0, 5, 8192, 8192, 0)
                   for cpu in range(4) for rate in (0, 2) for hz in (50, 60)]
        records += [(1, 2, 3, 50, 1, 0, 4096, 0, 100),
                    (2, 0, 3, 50, 0, 0, 0, 0, 25),
                    (3, 0, 3, 50, 0, 0, 0, 0, 0),
                    (4, 2, 3, 50, 0, 0, 8192, 8192, 0),
                    (5, 2, 3, 50, 1, 0, 4096, 0, 100),
                    (6, 0, 3, 50, 1, 0, 4096, 0, 100),
                    (0, 0, 3, 50, 0, 5, 8192, 8192, 0),
                    (0, 2, 3, 50, 0, 5, 8192, 8192, 0)]
        cls.plan = [(i, *record) for i, record in enumerate(records, 1)]
        cls.code, cls.symbols = assemble_probe("PROBE_BATCH=1", plan=cls.plan)

    def driver(self, **options):
        esp = BatchEsp(**options)
        driver = BatchDriver((), esp, linked=({0x8000: self.code}, self.symbols))
        esp.driver = driver
        driver.LIMIT = 2_000_000_000
        return driver, esp

    def test_all_cases_symmetric_payloads_faults_and_restore(self):
        driver, esp = self.driver()
        at = self.symbols["probe_ticks"]
        driver.m.memory[at:at+2] = (65500).to_bytes(2, "little")
        driver.call("probe_run")
        self.assertFalse(driver.carry, [(r[4], r[7], int.from_bytes(r[16:18], "little"))
                                        for r in esp.reports])
        begins = [r for r in esp.reports if r[4] == 12]
        dones = [r for r in esp.reports if r[4] == 13]
        self.assertEqual(len(begins), len(self.plan))
        self.assertEqual(len(dones), len(self.plan))
        self.assertTrue(all(r[7] == 0 for r in dones))
        for report, case in zip(begins, self.plan):
            ident, kind, rate, cpu, hz, flags, frames, *_ = case
            self.assertEqual((int.from_bytes(report[16:18], "little"), report[18],
                              report[19], int.from_bytes(report[20:22], "little"),
                              int.from_bytes(report[22:24], "little")),
                             (ident, kind, cpu, hz, flags))
            self.assertEqual(int.from_bytes(report[24:26], "little"), frames * (6 if hz == 60 else 5) // 5)
        chunks = [r for r in esp.reports if r[4] == 10]
        self.assertEqual(b"".join(esp.tx_payloads), pattern(8192) * len(chunks))
        self.assertTrue(all(len(p) == 2048 for p in esp.tx_payloads))
        self.assertTrue(all(int.from_bytes(r[26:28], "little") == 8192
                            for r in esp.reports if r[4] == 9))
        faults = [r for r in esp.reports if r[4] == 15]
        self.assertEqual(len(faults), 4)
        self.assertTrue(all(r[7] == 0 for r in faults))
        overflow, framing = faults[-2:]
        for report, mask in ((overflow, 4), (framing, 0x40)):
            self.assertTrue(report[12] & mask)
            self.assertEqual(int.from_bytes(report[20:22], "little"), 1)
            self.assertEqual(int.from_bytes(report[22:24], "little"), 1)
        corrupt = next(r for r in chunks if r[7] == 2)
        altered = bytearray(pattern(8192))
        altered[128] ^= 1
        self.assertEqual((int.from_bytes(corrupt[18:20], "little"),
                          int.from_bytes(corrupt[20:22], "little")),
                         (sum(altered) & 65535, 1))
        phases = [r for r in esp.reports if r[4] == 11]
        self.assertTrue(all(int.from_bytes(r[24:26], "little") >=
                            int.from_bytes(r[26:28], "little") for r in phases))
        self.assertTrue(all(int.from_bytes(r[26:28], "little") > 0
                            for r in chunks if r[7] == 0))
        self.assertEqual(esp.regs, {5: 0xA1, 7: 2})
        self.assertEqual(esp.reports[-1][4], 6)
        self.assertEqual(esp.errors, [])
        self.assertEqual(esp.framing_divisors, [2917])

    def test_actual_cpu_mismatch_fails_before_payload_and_restores(self):
        driver, esp = self.driver(blocked_cpu=1)
        driver.call("probe_run")
        self.assertTrue(driver.carry)
        begin = next(r for r in esp.reports if r[4] == 12)
        done = next(r for r in esp.reports if r[4] == 13)
        self.assertEqual(begin[19], 1)
        self.assertEqual(done[7], 1)
        self.assertFalse(any(r[4] in (6, 9) for r in esp.reports))
        self.assertEqual(esp.regs, {5: 0xA1, 7: 2})

    def test_missing_expected_framing_fails_and_restores_matching_baud(self):
        code, symbols = assemble_probe("PROBE_BATCH=1", plan=[
            (1, 6, 0, 3, 50, 1, 0, 4096, 0, 5)])
        esp = BatchEsp()
        driver = BatchDriver((), esp, linked=({0x8000: code}, symbols))
        esp.driver = driver
        driver.LIMIT = 50_000_000
        original = esp.report_ready
        esp.report_ready = lambda report: None if report[4] == 14 else original(report)
        driver.call("probe_run")
        self.assertFalse(driver.carry)
        self.assertEqual(esp.reports[-1][4], 6)
        self.assertEqual(next(r for r in esp.reports if r[4] == 15)[7], 1)
        self.assertEqual(next(r for r in esp.reports if r[4] == 13)[7], 1)
        self.assertEqual(esp.divisor, 243)
        self.assertEqual(esp.regs, {5: 0xA1, 7: 2})

    def test_batch_deadline_word_wrap_preserves_registers(self):
        driver, _ = self.driver()
        memory = driver.m.memory
        for symbol, value in (("probe_started", 65500), ("probe_ticks", 463)):
            at = self.symbols[symbol]
            memory[at:at+2] = value.to_bytes(2, "little")
        driver.call("probe_expired", hl=0x1234, de=0x5678)
        self.assertTrue(driver.carry)
        self.assertEqual((driver.m.hl, driver.m.de), (0x1234, 0x5678))
        at = self.symbols["probe_ticks"]
        memory[at:at+2] = (464).to_bytes(2, "little")
        driver.call("probe_expired", hl=0x1234, de=0x5678)
        self.assertFalse(driver.carry)
        self.assertEqual((driver.m.hl, driver.m.de), (0x1234, 0x5678))

    def test_unexpected_missing_payload_reports_failure_and_recovers(self):
        code, symbols = assemble_probe("PROBE_BATCH=1", plan=[
            (1, 0, 0, 0, 50, 0, 5, 8192, 8192, 0)])
        esp = BatchEsp()
        esp.no_rx = True
        driver = BatchDriver((), esp, linked=({0x8000: code}, symbols))
        esp.driver = driver
        driver.LIMIT = 100_000_000
        driver.call("probe_run")
        self.assertTrue(driver.carry)
        result = next(r for r in esp.reports if r[4] == 10)
        self.assertEqual(result[7], 1)
        self.assertGreaterEqual(int.from_bytes(result[24:26], "little"), 500)
        self.assertEqual(next(r for r in esp.reports if r[4] == 13)[7], 1)
        self.assertFalse(any(r[4] == 6 for r in esp.reports))
        self.assertEqual(esp.divisor, 243)
        self.assertEqual(esp.regs, {5: 0xA1, 7: 2})


class ReconnectCpu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.symbols = assemble_probe("PROBE_BATCH=1", plan=[
            (1, 3, 0, 0, 50, 0, 0, 0, 0, 0),
            (2, 0, 0, 0, 50, 0, 5, 8192, 8192, 0)])

    def run_reconnect(self, **options):
        esp = BatchEsp(**options)
        driver = BatchDriver((), esp, linked=({0x8000: self.code}, self.symbols))
        esp.driver = driver
        driver.LIMIT = 100_000_000
        driver.call("probe_run")
        self.assertFalse(driver.carry)
        self.assertEqual(esp.regs, {5: 0xA1, 7: 2})
        self.assertEqual(esp.divisor, 243)
        self.assertEqual(esp.errors, [])
        return esp, next(r for r in esp.reports if r[4] == 15)

    def test_challenge_proves_reconnect_independent_of_notice_presence_or_order(self):
        cases = ((b"\r\n0,CLOSED\r\n0,CONNECT\r\n", 3),
                 (b"", 0), (b"\r\n0,CONNECT\r\n0,CLOSED\r\n", 3),
                 (b"\r\n0,CLOSED\r\n", 1), (b"\r\n0,CONNECT\r\n", 2))
        for notices, bits in cases:
            with self.subTest(notices=notices):
                esp, result = self.run_reconnect(reconnect_notices=notices, fragment_challenge=True)
                self.assertEqual((result[7], result[12]), (0, 0))
                values = [int.from_bytes(result[i:i+2], "little") for i in (16, 18, 20, 22, 26)]
                self.assertEqual(values, [1, sum(pattern(32)), 0, 32, 3 | bits << 8])
                self.assertEqual([r[7] for r in esp.reports if r[4] == 13], [0, 0])
                self.assertEqual(esp.reports[-1][4], 6)
                self.assertEqual(int.from_bytes(result[14:16], "little"), 0)

    def test_missing_wrong_truncated_and_oversize_challenges_fail_then_continue_clean(self):
        wrong = bytearray(pattern(32))
        wrong[7] ^= 1
        for challenge in (None, bytes(wrong), pattern(31), pattern(33), b""):
            with self.subTest(challenge=challenge):
                esp, result = self.run_reconnect(reconnect_challenge=challenge)
                self.assertEqual(result[7], 1)
                self.assertEqual([r[7] for r in esp.reports if r[4] == 13], [1, 0])
                self.assertEqual(esp.reports[-1][4], 6)
                clean = [r for r in esp.reports if r[4] == 10]
                self.assertEqual(len(clean), 1)
                self.assertEqual(clean[0][7], 0)
                self.assertEqual(b"".join(esp.tx_payloads), pattern(8192))
                if challenge is None:
                    self.assertGreaterEqual(int.from_bytes(result[24:26], "little"), 500)
                    self.assertEqual(int.from_bytes(result[22:24], "little"), 0)
                elif len(challenge) == 32:
                    self.assertEqual(int.from_bytes(result[20:22], "little"), 1)

    def test_failed_phase_after_complete_payload_continues_clean_case_and_done(self):
        code, symbols = assemble_probe("PROBE_BATCH=1", plan=[
            (1, 0, 0, 0, 50, 0, 1500, 8192, 8192, 0),
            (2, 0, 0, 0, 50, 0, 5, 8192, 8192, 0)])
        esp = BatchEsp()
        original = esp.report_ready
        case_id = [0]
        def report_ready(report):
            if report[4] == 12:
                case_id[0] = int.from_bytes(report[16:18], "little")
            if report[4] == 9 and case_id[0] == 1:
                esp.corrupt = True
            original(report)
        esp.report_ready = report_ready
        driver = BatchDriver((), esp, linked=({0x8000: code}, symbols))
        esp.driver = driver
        driver.LIMIT = 100_000_000
        driver.call("probe_run")
        self.assertFalse(driver.carry)
        self.assertEqual([r[7] for r in esp.reports if r[4] == 13], [1, 0])
        phases = [r for r in esp.reports if r[4] == 11]
        self.assertEqual([r[7] for r in phases], [1, 0])
        self.assertLess(int.from_bytes(phases[0][24:26], "little"), 1500)
        self.assertEqual(int.from_bytes(phases[0][26:28], "little"), 1500)
        self.assertEqual(esp.reports[-1][4], 6)
        self.assertEqual(b"".join(esp.tx_payloads), pattern(8192) * 2)
        self.assertEqual(esp.errors, [])

    def test_challenge_cannot_reuse_previous_case_receive_state(self):
        esp = BatchEsp(reconnect_notices=b"", reconnect_challenge=None)
        driver = BatchDriver((), esp, linked=({0x8000: self.code}, self.symbols))
        esp.driver = driver
        driver.LIMIT = 100_000_000
        for symbol, value in (("probe_soak_rx_count", 32),
                              ("probe_soak_rx_sum", sum(pattern(32)))):
            at = self.symbols[symbol]
            driver.m.memory[at:at+2] = value.to_bytes(2, "little")
        driver.call("probe_run")
        result = next(r for r in esp.reports if r[4] == 15)
        self.assertEqual(result[7], 1)
        self.assertEqual(int.from_bytes(result[22:24], "little"), 0)
        self.assertEqual(esp.reports[-1][4], 6)


class ProbeCpu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.symbols = assemble_probe()

    def driver(self, board=2):
        esp = Esp(board)
        driver = Driver((), esp, linked=({0x8000: self.code}, self.symbols))
        driver.LIMIT = 120_000_000
        return driver, esp

    def test_full_matrix_reports_and_payloads(self):
        driver, esp = self.driver()
        driver.call("probe_run")
        self.assertFalse(driver.carry)
        self.assertEqual(esp.errors, [])
        self.assertEqual(esp.reports[0][4], 0)
        self.assertEqual(esp.reports[-1][4], 6)
        ready = {}
        rows = set()
        for report in esp.reports:
            event, rate, flow, result = report[4:8]
            step = int.from_bytes(report[14:16], "little")
            count, total, bad = [int.from_bytes(report[i:i+2], "little") for i in (16, 18, 20)]
            if event in (1, 3):
                ready[event] = (rate, flow, step)
            elif event in (2, 4):
                self.assertEqual(ready[event-1], (rate, flow, step))
                rows.add((event, rate, flow))
                if event == 2 and not flow:
                    self.assertNotEqual(report[12] & 4, 0)
                else:
                    length = 4096 if event == 2 else 2048
                    self.assertEqual((result, count, total, bad), (0, length, sum(pattern(length)) & 65535, 0))
        self.assertEqual(len(rows), 8)
        self.assertEqual(esp.tx_payloads, [pattern(2048)] * 4)
        self.assertEqual(esp.frame_registers[0] & 0x20, 0)

    def test_unknown_board_skips_flow_without_arming_it(self):
        driver, esp = self.driver(0)
        driver.call("probe_run")
        self.assertFalse(driver.carry)
        self.assertEqual([r[5] for r in esp.reports if r[4] == 8], [0, 2])
        self.assertFalse(any(value & 0x20 for value in esp.frame))
        self.assertEqual(len(esp.tx_payloads), 2)

    def test_expired_read_rejects_continuously_available_input(self):
        driver, esp = self.driver()
        driver.m.memory[self.symbols["probe_ticks"]] = 150
        esp.feed(b"noise")
        driver.call("probe_get")
        self.assertFalse(driver.carry)
        self.assertEqual(len(esp.sent), 0)


class SoakCpu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.symbols = assemble_probe("PROBE_SOAK=1", "PROBE_SOAK_FRAMES=300")

    def driver(self, **options):
        esp = SoakEsp(**options)
        driver = Driver((), esp, linked=({0x8000: self.code}, self.symbols))
        driver.LIMIT = 150_000_000
        return driver, esp

    def test_full_duplex_matrix_interleaves_ipd_prompts_send_ok_and_busy(self):
        driver, esp = self.driver()
        driver.call("probe_run")
        self.assertFalse(driver.carry)
        self.assertEqual(esp.errors, [])
        self.assertTrue(esp.ready_ack_during_rx)
        self.assertTrue(esp.prompt_during_rx)
        self.assertTrue(esp.send_ok_during_rx)
        ready, results, phases = [], [], []
        for report in esp.reports:
            event = report[4]
            values = [int.from_bytes(report[i:i + 2], "little") for i in (14, 16, 18, 20, 22, 24, 26)]
            step, count, total, bad, busy, elapsed, reserved = values
            if event == 9:
                self.assertEqual((count, reserved), (8192, 2048))
                ready.append((report[5], report[6], step))
            elif event == 10:
                self.assertEqual((report[7], report[12], count, total, bad),
                                 (0, 0, 8192, sum(pattern(8192)) & 65535, 0))
                self.assertGreater(reserved, 0)
                self.assertGreater(busy, 0)
                self.assertLess(elapsed, 150)
                results.append((report[5], report[6], step))
            elif event == 11:
                self.assertEqual(report[7], 0)
                self.assertEqual(reserved, 300)
                self.assertGreaterEqual(elapsed, reserved)
                self.assertEqual(count, len([r for r in results if r[0] == report[5]]))
                phases.append((report[5], report[6]))
        self.assertEqual(ready, results)
        self.assertEqual(phases, [(0, 1), (2, 1)])
        self.assertEqual(esp.tx_payloads, [pattern(2048)] * len(results))
        self.assertEqual(esp.reports[-1][4], 6)

    def test_unknown_board_skips_both_soak_rates(self):
        driver, esp = self.driver(board=0)
        driver.call("probe_run")
        self.assertFalse(driver.carry)
        self.assertEqual([r[5] for r in esp.reports if r[4] == 8], [0, 2])
        self.assertFalse(any(value & 0x20 for value in esp.frame))
        self.assertFalse(esp.tx_payloads)

    def test_corruption_and_missing_rx_report_failure_and_abort(self):
        for options in ({"corrupt": True}, {"truncate": True}, {"no_rx": True}):
            with self.subTest(options=options):
                driver, esp = self.driver(**options)
                driver.call("probe_run")
                self.assertTrue(driver.carry)
                results = [r for r in esp.reports if r[4] == 10]
                self.assertEqual(len(results), 1)
                self.assertNotEqual(results[0][7], 0)
                if options.get("corrupt"):
                    self.assertEqual(int.from_bytes(results[0][20:22], "little"), 1)
                else:
                    self.assertEqual(results[0][7], 1)
                    self.assertGreaterEqual(int.from_bytes(results[0][24:26], "little"), 150)
                self.assertFalse(any(r[4] == 6 for r in esp.reports))
                self.assertEqual(esp.frame_registers[0] & 0x20, 0)

    def test_first_payload_service_does_not_claim_overlap(self):
        driver, esp = self.driver()
        memory = driver.m.memory
        memory[self.symbols["probe_soak_parse_state"]] = 3
        memory[self.symbols["probe_soak_packet_left"]] = 1
        memory[self.symbols["probe_soak_rx_pattern"]] = 1
        esp.feed(b"\x01")
        driver.call("probe_soak_put", a=0x55)
        self.assertFalse(driver.carry)
        self.assertEqual(driver.word("probe_soak_rx_count"), 1)
        self.assertEqual(driver.word("probe_soak_overlap"), 0)
        self.assertEqual(esp.sent, b"U")

    def test_60hz_target_and_16bit_irq(self):
        driver, esp = self.driver(video=4)
        driver.call("probe_run")
        self.assertFalse(driver.carry)
        for report in esp.reports:
            if report[4] == 11:
                self.assertEqual(int.from_bytes(report[26:28], "little"), 360)
                self.assertGreaterEqual(int.from_bytes(report[24:26], "little"), 360)
        # The run crosses low-byte tick wrap; IRQ must preserve interrupted HL/AF.
        self.assertGreater(driver.word("probe_ticks"), 512)


if __name__ == "__main__":
    unittest.main()
