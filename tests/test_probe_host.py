"""Host-only probe checks; all sockets stay on loopback."""

import importlib.util
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("next_probe", Path(__file__).resolve().parents[1] / "tools" / "next_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
TOKEN = 0x12345678


def record(event, rate=0, flow=0, step=1, count=0, total=0, bad=0, result=0, rx_error=0, elapsed=0, reserved=0, busy=0, board=3):
    return b"UXP1" + bytes((event, rate, flow, result, board, 0, 3, 2, rx_error, 1)) + struct.pack("<7HI", step, count, total, bad, busy, elapsed, reserved, TOKEN)


class ProbeHost(unittest.TestCase):
    def run_server(self, handler, timeout=1, soak=False, batch_cases=None, reconnect=False, verify_shutdown=False):
        if (soak or batch_cases is not None) and timeout == 1:
            timeout = 3
        errors = []
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(2)

        def serve():
            try:
                with server:
                    connection, _ = server.accept()
                    with connection:
                        connection.settimeout(1)
                        handler(connection, server) if reconnect else handler(connection)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                run = {"protocol": "UXP1", "token": TOKEN, "verify_shutdown": verify_shutdown}
                if soak:
                    run.update(mode="soak", frames_per_rate=10)
                if batch_cases is not None:
                    run.update(mode="batch", cases=batch_cases)
                (output / "run.json").write_text(json.dumps(run))
                summary = probe.collect("127.0.0.1", output, timeout, server.getsockname()[1])
                logs = [json.loads(line) for line in (output / "telemetry.jsonl").read_text().splitlines()]
                self.assertEqual(summary, json.loads((output / "summary.json").read_text()))
        finally:
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(any(thread.name == "next-probe-duplex" for thread in threading.enumerate()))
            server.close()
        if errors:
            raise errors[0]
        return summary, logs

    def test_pattern(self):
        self.assertEqual(probe.pattern(0), b"")
        self.assertEqual(probe.pattern(8), bytes((1, 184, 92, 46, 23, 179, 225, 200)))
        self.assertEqual(len(set(probe.pattern(255))), 255)
        self.assertEqual(probe.pattern(510)[:255], probe.pattern(510)[255:])

    def test_record_boundaries(self):
        data = record(2, rate=2, flow=1, step=65535, count=65535, total=65535, bad=65535, result=3, rx_error=255)
        parsed = probe.parse_record(data, TOKEN)
        self.assertEqual((parsed["baud"], parsed["count"], parsed["step"], parsed["sum16"], parsed["bad16"]), (2000000, 65535, 65535, 65535, 65535))
        for broken, token in ((data[:-1], TOKEN), (data + b"x", TOKEN), (b"oops" + data[4:], TOKEN), (data, TOKEN + 1), (data[:4] + b"\x10" + data[5:], TOKEN)):
            with self.subTest(length=len(broken), token=token), self.assertRaises(ValueError):
                probe.parse_record(broken, token)

    def test_full_matrix_fragmented_records_and_payloads(self):
        def handler(connection, listener):
            connection.sendall(record(0))
            for rate in (0, 2):
                for flow in (0, 1):
                    rx = probe.pattern(4096)
                    tx = probe.pattern(2048)
                    connection.sendall(record(5, rate, flow))
                    ready = record(1, rate, flow, count=len(rx))
                    connection.sendall(ready[:7])
                    connection.sendall(ready[7:])
                    received = probe.receive_exact(
                        connection, len(rx), time.monotonic() + 1
                    )
                    self.assertEqual(received, rx)
                    # Deliberate overflow without flow is an observation, not a pass.
                    result_rx = (
                        record(2, rate, flow, count=len(rx), total=sum(rx) & 0xFFFF)
                        if flow
                        else record(2, rate, flow, count=63, result=3, rx_error=4)
                    )
                    connection.sendall(result_rx + record(3, rate, flow, count=len(tx)))
                    connection.sendall(tx[:11])
                    connection.sendall(
                        tx[11:]
                        + record(4, rate, flow, count=len(tx), total=sum(tx) & 0xFFFF)
                    )
            connection.sendall(record(6))
            listener.close()

        summary, logs = self.run_server(handler, timeout=8, reconnect=True, verify_shutdown=True)
        self.assertTrue(summary["ok"], summary.get("error"))
        self.assertTrue(summary["complete"])
        self.assertTrue(summary["shutdown_verified"])
        self.assertEqual(len(summary["results"]), 8)
        self.assertEqual(sum(item["status"] == "observed_without_flow" for item in summary["results"]), 2)
        self.assertEqual(sum(item["event"] == "host_received_tx" for item in logs), 4)
        self.assertTrue(all(item["host_crc32"] == item["expected_crc32"] for item in summary["results"] if item["event"] == "result_tx"))

    def test_tx_corruption_fails(self):
        def handler(connection):
            for rate in (0, 2):
                connection.sendall(record(8, rate, 1, result=4))
                rx = probe.pattern(4096)
                tx = probe.pattern(2048)
                connection.sendall(record(5, rate) + record(1, rate, count=len(rx)))
                probe.receive_exact(connection, len(rx), time.monotonic() + 1)
                connection.sendall(
                    record(2, rate, count=len(rx), total=sum(rx) & 0xFFFF)
                    + record(3, rate, count=len(tx))
                )
                connection.sendall(
                    b"x"
                    + tx[1:]
                    + record(4, rate, count=len(tx), total=sum(tx) & 0xFFFF)
                )
            connection.sendall(record(6))

        summary, logs = self.run_server(handler)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["complete"])
        self.assertEqual(sum(item["status"] == "fail" for item in summary["results"]), 2)
        captures = [bytes.fromhex(item["payload_hex"]) for item in logs if "payload_hex" in item]
        self.assertEqual(captures, [b"x" + probe.pattern(2048)[1:]] * 2)

    def test_quick_rejects_wrong_payload_sizes_before_transferring(self):
        for event, direction, size in ((1, "rx", 4096), (3, "tx", 2048)):
            for count in (0, size - 1, size + 1):
                with self.subTest(direction=direction, count=count):
                    summary, logs = self.run_server(
                        lambda connection: connection.sendall(
                            record(event, count=count)
                        )
                    )
                    self.assertFalse(summary["ok"])
                    self.assertIn(f"invalid ready_{direction} size", summary["error"])
                    self.assertFalse(
                        any(
                            log["event"] in ("host_sent_rx", "host_received_tx")
                            for log in logs
                        )
                    )

    def test_truncated_wrong_magic_token_and_missing_done(self):
        for data in (record(0)[:5], b"oops" + record(0)[4:], record(0)[:-4] + b"xxxx", record(0)):
            with self.subTest(data=data):
                summary, logs = self.run_server(lambda connection: connection.sendall(data))
                self.assertFalse(summary["ok"])
                self.assertIn("error", summary)
                self.assertEqual(logs[-1]["event"], "host_summary")

    def test_shutdown_rejects_open_connection_or_listener(self):
        connection, peer = socket.socketpair()
        with connection, peer:
            with self.assertRaisesRegex(TimeoutError, "connection stayed open"):
                probe.verify_shutdown(connection, "127.0.0.1", 0, time.monotonic() + 0.05)

            peer.shutdown(socket.SHUT_WR)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                with self.assertRaisesRegex(ValueError, "listener still open"):
                    probe.verify_shutdown(connection, "127.0.0.1", listener.getsockname()[1], time.monotonic() + 1)

    def test_read_timeout_is_bounded(self):
        started = time.monotonic()
        summary, _ = self.run_server(lambda connection: time.sleep(0.2), timeout=0.05)
        self.assertFalse(summary["ok"])
        self.assertLess(time.monotonic() - started, 1)
        self.assertIn("timed out", summary["error"])

    def test_connect_timeout_is_bounded(self):
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            port = closed.getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "run.json").write_text(json.dumps({"protocol": "UXP1", "token": TOKEN}))
            started = time.monotonic()
            summary = probe.collect("127.0.0.1", output, 0.05, port)
            self.assertFalse(summary["ok"])
            self.assertLess(time.monotonic() - started, 0.5)

    def test_build_mode_preserves_quick_default(self):
        for soak in (False, True):
            with self.subTest(soak=soak), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)

                def assemble(command, cwd, check):
                    self.assertTrue(check)
                    self.assertEqual("-DPROBE_SOAK=1" in command, soak)
                    self.assertEqual(f"-DPROBE_SOAK_FRAMES={probe.SOAK_FRAMES}" in command, soak)
                    for name in ("UXPROBE.NEX", "probe.bin", "probe.sym"):
                        (cwd / name).touch()

                with patch.object(probe.subprocess, "run", side_effect=assemble):
                    self.assertEqual(probe.build(output, soak), output.resolve())
                run = json.loads((output / "run.json").read_text())
                self.assertTrue(run["verify_shutdown"])
                self.assertEqual(run.get("mode", "quick"), "soak" if soak else "quick")
                self.assertEqual(run.get("frames_per_rate"), probe.SOAK_FRAMES if soak else None)

    def soak_phase(self, connection, rate, step, overlap=32, corrupt=False, missing_result=False,
                   duration=10, target=10, bad_rx=False, chunks=1, phase_done=True):
        connection.sendall(record(5, rate, 1))
        for index in range(chunks):
            current = step + index
            payload = probe.pattern(8192)
            tx = probe.pattern(2048)
            connection.sendall(record(9, rate, 1, current, count=8192, reserved=2048))
            actual = b"x" + tx[1:] if corrupt else tx
            connection.sendall(actual[:17])
            connection.sendall(actual[17:])
            received = probe.receive_exact(connection, 8192, time.monotonic() + 1)
            self.assertEqual(received, payload)
            if missing_result:
                return
            connection.sendall(record(10, rate, 1, current, count=8192, total=sum(payload) & 0xFFFF,
                                      elapsed=3, reserved=overlap[index] if isinstance(overlap, tuple) else overlap,
                                      bad=int(bad_rx)))
        if phase_done:
            connection.sendall(record(11, rate, 1, step + chunks - 1, count=chunks, elapsed=duration, reserved=target))

    def test_soak_duplex_counts_crcs_duration_and_progress(self):
        def handler(connection, listener):
            connection.sendall(record(0))
            self.soak_phase(connection, 0, 1, overlap=(0, 17), chunks=2)
            self.soak_phase(connection, 2, 3, duration=12, target=12)
            connection.sendall(record(6))
            listener.close()

        summary, logs = self.run_server(handler, timeout=8, soak=True, reconnect=True, verify_shutdown=True)
        self.assertTrue(summary["ok"], summary.get("error"))
        self.assertTrue(summary["complete"])
        self.assertTrue(summary["shutdown_verified"])
        self.assertEqual(summary["totals"], {"chunks": 3, "rx_bytes": 24576, "tx_bytes": 6144, "overlap_count": 49})
        self.assertEqual([item["target_frames"] for item in summary["per_rate"]], [10, 12])
        self.assertEqual([item["chunks"] for item in summary["per_rate"]], [2, 1])
        self.assertTrue(all(item["host_tx_crc32"] == item["expected_tx_crc32"] for item in summary["results"]))
        self.assertEqual(sum(item["event"] == "host_assessment" for item in logs), 3)

    def test_soak_corrupt_rx_or_tx_fails(self):
        for field in ("corrupt", "bad_rx"):
            with self.subTest(field=field):
                def handler(connection):
                    self.soak_phase(connection, 0, 1, **{field: True})
                    self.soak_phase(connection, 2, 2)
                    connection.sendall(record(6))

                summary, _ = self.run_server(handler, soak=True)
                self.assertFalse(summary["ok"])
                self.assertEqual(summary["results"][0]["status"], "fail")

    def test_soak_rejects_zero_overlap_short_phase_and_missing_result(self):
        for kwargs, error in (({"overlap": 0}, "no RX/TX overlap"), ({"duration": 9}, "short soak"),
                              ({"duration": 10, "target": 12}, "short soak"),
                              ({"phase_done": False}, "truncated stream"),
                              ({"missing_result": True}, "truncated stream")):
            with self.subTest(kwargs=kwargs):
                summary, _ = self.run_server(lambda connection: self.soak_phase(connection, 0, 1, **kwargs), soak=True)
                self.assertFalse(summary["ok"])
                self.assertFalse(summary["complete"])
                self.assertIn(error, summary["error"])

    def test_soak_requires_both_phases_and_done(self):
        for ending in (b"", record(6), record(8, 2, 1)):
            with self.subTest(ending=ending):
                def handler(connection):
                    self.soak_phase(connection, 0, 1)
                    connection.sendall(ending)

                summary, _ = self.run_server(handler, soak=True)
                self.assertFalse(summary["ok"])
                self.assertIn("error", summary)

    def test_soak_skipped_board_never_reports_success(self):
        summary, _ = self.run_server(lambda connection: connection.sendall(record(8, 0, 1) + record(8, 2, 1) + record(6)), soak=True)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["complete"])
        self.assertEqual(len(summary["skipped"]), 2)

    def test_duplex_sender_stops_when_receive_fails(self):
        sending = threading.Event()
        released = threading.Event()

        class BrokenSocket:
            def settimeout(self, timeout):
                pass

            def sendall(self, payload):
                sending.set()
                released.wait(1)

            def recv(self, count):
                if not sending.wait(1):
                    raise AssertionError("sender did not start")
                return b""

            def shutdown(self, how):
                released.set()

        started = time.monotonic()
        with self.assertRaises(EOFError):
            probe.exchange_duplex(BrokenSocket(), probe.pattern(8192), 2048, 115200, started + 1)
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertFalse(any(thread.name == "next-probe-duplex" for thread in threading.enumerate()))

    def test_duplex_sends_paced_packets_while_receiving(self):
        packets = []
        sending = threading.Event()

        class DuplexSocket:
            def settimeout(self, timeout):
                pass

            def sendall(self, payload):
                packets.append((time.monotonic(), payload))
                sending.set()

            def recv(self, count):
                if not sending.wait(1) or len(packets) != 1:
                    raise AssertionError("RX must run before the paced sender finishes")
                return probe.pattern(count)

        started = time.monotonic()
        actual = probe.exchange_duplex(DuplexSocket(), probe.pattern(8192), 2048, 115200, started + 2)
        self.assertEqual(actual, probe.pattern(2048))
        self.assertEqual([len(payload) for _, payload in packets], [1024] * 8)
        self.assertEqual(b"".join(payload for _, payload in packets), probe.pattern(8192))
        self.assertGreaterEqual(packets[-1][0] - packets[0][0], 0.55)

    def short_cases(self):
        cases = [dict(case) for case in probe.batch_plan()[:1] + probe.batch_plan()[8:20]]
        for index, case in enumerate(cases, 1):
            case["id"] = index
            if case["nominal_frames"]:
                case["nominal_frames"] = 5
            if case["kind"] in (0, 4):
                case.update(rx_bytes=256, tx_bytes=256)
        return cases

    def batch_begin(self, connection, case, current_step, **changes):
        fields = dict(rate=case["rate"], flow=0 if case["kind"] in (5, 6) else 1, step=current_step,
                      count=case["id"], total=case["kind"] | case["cpu"] << 8, bad=case["hz"],
                      busy=case["flags"], elapsed=case["nominal_frames"] * case["hz"] // 50)
        fields.update(changes)
        connection.sendall(record(12, **fields))

    def batch_case(self, connection, case, step, defect=None, listener=None, notice_bits=3):
        rate, kind = case["rate"], case["kind"]
        flow = 0 if kind in (5, 6) else 1
        self.batch_begin(connection, case, step)
        target = case["nominal_frames"] * case["hz"] // 50
        if kind in (0, 4):
            for _ in range(2 if case["id"] == 1 and kind == 0 else 1):
                step += 1
                connection.sendall(record(9, rate, flow, step, count=case["rx_bytes"], reserved=case["tx_bytes"]))
                tx = probe.pattern(case["tx_bytes"])
                connection.sendall((b"x" + tx[1:]) if defect == "tx" else tx)
                rx = probe.receive_exact(connection, case["rx_bytes"], time.monotonic() + 1)
                expected = bytearray(probe.pattern(case["rx_bytes"]))
                if kind == 4:
                    expected[128] ^= 1
                self.assertEqual(rx, expected)
                if defect == "missing_result":
                    return connection, step
                connection.sendall(record(10, rate, flow, step, count=len(rx), total=(sum(rx) + int(defect == "rx")) & 65535,
                                          bad=int(kind == 4 and defect != "fault"), result=2 if kind == 4 else 0, elapsed=1,
                                          reserved=0 if defect == "overlap" else 32))
            if kind == 0 and defect != "missing_phase":
                connection.sendall(record(11, rate, flow, step, count=2 if case["id"] == 1 else 1,
                                          elapsed=target - int(defect == "duration"), reserved=target))
        elif kind in (1, 5):
            step += 1
            connection.sendall(record(1, rate, flow, step, count=case["rx_bytes"]))
            rx = probe.receive_exact(connection, case["rx_bytes"], time.monotonic() + 1)
            self.assertEqual(rx, probe.pattern(case["rx_bytes"]))
            if kind == 1:
                connection.sendall(record(2, rate, flow, step, count=len(rx), total=sum(rx) & 65535))
            else:
                connection.sendall(record(2, rate, flow, step, count=63, result=3, rx_error=4))
                connection.sendall(record(15, rate, flow, step, count=case["id"], total=63, bad=1,
                                          busy=int(defect != "fault"), rx_error=4))
        else:
            step += int(kind == 6)
            connection.sendall(record(14, rate, flow, step, count=case["id"], reserved=kind))
            if kind == 6:
                started = time.monotonic()
                connection.settimeout(0.8)
                with self.assertRaises(socket.timeout):
                    connection.recv(1)
                rx = probe.receive_exact(connection, case["rx_bytes"], time.monotonic() + 2)
                self.assertGreaterEqual(time.monotonic() - started, 0.98)
                self.assertEqual(rx, probe.pattern(case["rx_bytes"]))
            if kind == 3:
                with self.assertRaises((ConnectionResetError, ConnectionAbortedError)):
                    connection.recv(1)
                connection.close()
                connection, _ = listener.accept()
                connection.settimeout(1)
                challenge = probe.receive_exact(connection, 32, time.monotonic() + 1)
                self.assertEqual(challenge, probe.pattern(32))
            if kind == 2:
                connection.settimeout(0.02)
                with self.assertRaises(socket.timeout):
                    connection.recv(1)
                connection.settimeout(1)
            if kind == 3:
                fields = dict(total=sum(challenge), busy=32, reserved=3 | notice_bits << 8)
                if defect == "challenge":
                    fields.update(total=sum(challenge) + 1, bad=1, result=1)
                elif defect == "missing_challenge":
                    fields.update(total=0, busy=0, result=1)
                elif defect == "unknown_notices":
                    fields.update(reserved=3 | 4 << 8)
                elif defect == "wrong_fault_kind":
                    fields.update(reserved=2)
                connection.sendall(record(15, rate, flow, step, count=case["id"], elapsed=1, **fields))
            else:
                connection.sendall(record(15, rate, flow, step, count=case["id"], total=63 if kind == 6 else 1,
                                          bad=int(kind == 6 and defect != "fault"), busy=int(kind == 6), rx_error=64 if kind == 6 else 0,
                                          elapsed=case["pause_frames"] - int(defect == "fault") if kind == 2 else 1))
        connection.sendall(record(13, rate, flow, step, count=case["id"]))
        return connection, step

    def test_batch_plan_build_and_cli_modes(self):
        cases = probe.batch_plan()
        self.assertEqual(len(cases), 44)
        self.assertEqual([case["id"] for case in cases], list(range(1, 45)))
        self.assertEqual([case["kind"] for case in cases[12:18]], [1, 2, 3, 4, 5, 6])
        for rate in (0, 2):
            self.assertEqual(sum(case["nominal_frames"] for case in cases[20:] if case["rate"] == rate), 180000)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)

            def assemble(command, cwd, check):
                self.assertIn("-DPROBE_BATCH=1", command)
                self.assertIn(f"-I{output.resolve().as_posix()}", command)
                self.assertIn(f"-I{(probe.ROOT / 'examples').as_posix()}", command)
                self.assertIn(f"-I{(probe.ROOT / 'src').as_posix()}", command)
                self.assertNotIn("-DPROBE_SOAK=1", command)
                include = (output / "batch_plan.inc").read_text()
                self.assertTrue(include.startswith("probe_batch_plan:\n    dw 1\n    db 0,0,0,50,0,0\n    dw 1500,8192,8192,0\n"))
                self.assertTrue(include.endswith("    dw 0\n"))
                self.assertEqual(include.count("    db "), 44)
                for name in ("UXPROBE.NEX", "probe.bin", "probe.sym"):
                    (cwd / name).touch()

            with patch.object(probe.subprocess, "run", side_effect=assemble):
                probe.build(output, batch=True)
            run = json.loads((output / "run.json").read_text())
            self.assertEqual((run["mode"], run["cases"]), ("batch", cases))
        with self.assertRaises(ValueError):
            probe.build("unused", soak=True, batch=True)
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            probe.main(["build", "--output", "unused", "--batch", "--soak"])

    def test_batch_all_kinds_reconnect_metadata_totals_and_faults(self):
        cases = self.short_cases()

        def handler(connection, listener):
            try:
                connection.sendall(record(0, step=0))
                step = 0
                for case in cases:
                    connection.sendall(record(5, case["rate"], 0 if case["kind"] in (5, 6) else 1, step))
                    connection, step = self.batch_case(connection, case, step, listener=listener)
                connection.sendall(record(6, step=step))
                listener.close()
            finally:
                connection.close()

        with patch.object(probe, "inhibit_sleep") as sleep:
            summary, logs = self.run_server(handler, timeout=8, batch_cases=cases, reconnect=True, verify_shutdown=True)
        self.assertTrue(summary["shutdown_verified"], summary.get("error"))
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(True), unittest.mock.call(False)])
        self.assertTrue(summary["ok"], summary.get("error"))
        self.assertTrue(summary["complete"])
        self.assertEqual(len(summary["cases"]), len(cases))
        self.assertEqual(len(summary["expected_faults"]), 5)
        self.assertTrue(all(item["status"] == "pass" for item in summary["expected_faults"]))
        self.assertEqual(sum(item["event"] == "connected" for item in logs), 2)
        self.assertEqual(sum(item["event"] == "host_planned_reset" for item in logs), 1)
        framing_id = next(case["id"] for case in cases if case["kind"] == 6)
        framing = [item for item in logs if item.get("case_id") == framing_id and item["event"] in ("host_wait_framing", "host_sent_rx")]
        self.assertEqual([item["event"] for item in framing], ["host_wait_framing", "host_sent_rx"])
        self.assertGreaterEqual(framing[1]["host_seconds"] - framing[0]["host_seconds"], 0.98)
        chunks = sum((2 if case["id"] == 1 else 1) for case in cases if case["kind"] in (0, 1, 4, 5, 6))
        rx = sum(case["rx_bytes"] * (2 if case["id"] == 1 else 1) for case in cases) + 32
        self.assertEqual(sum(item["event"] == "host_reconnect_challenge" for item in logs), 1)
        tx = sum(case["tx_bytes"] * (2 if case["id"] == 1 else 1) for case in cases)
        self.assertEqual(summary["totals"]["chunks"], chunks)
        self.assertEqual(summary["totals"]["rx_bytes"], rx)
        self.assertEqual(summary["totals"]["tx_bytes"], tx)
        self.assertEqual(summary["device"]["raw_board"], 3)
        self.assertEqual([case["cpu"] for case in summary["cases"]], [case["cpu"] for case in cases])
        self.assertEqual([case["hz"] for case in summary["cases"]], [case["hz"] for case in cases])

    def test_batch_corruption_missing_results_phase_overlap_duration(self):
        for defect in ("tx", "rx", "missing_result", "missing_phase", "overlap", "duration"):
            with self.subTest(defect=defect):
                case = self.short_cases()[0]

                def handler(connection):
                    try:
                        _, step = self.batch_case(connection, case, 0, defect=defect)
                        if defect != "missing_result":
                            connection.sendall(record(6, step=step))
                    except (ConnectionResetError, ConnectionAbortedError):
                        if defect in ("rx", "tx"):
                            raise

                with patch.object(probe, "inhibit_sleep") as sleep:
                    summary, _ = self.run_server(handler, batch_cases=[case])
                self.assertFalse(summary["ok"])
                self.assertEqual(sleep.call_args_list, [unittest.mock.call(True), unittest.mock.call(False)])
                if defect in ("rx", "tx"):
                    self.assertTrue(summary["complete"])
                    self.assertEqual(summary["cases"][0]["status"], "fail")
                else:
                    self.assertIn("error", summary)

    def test_batch_rejects_wrong_case_cpu_hz_flags_and_metadata(self):
        case = self.short_cases()[0]
        for changes in ({"count": 2}, {"total": 256}, {"bad": 60}, {"busy": 1}, {"elapsed": 1}, {"board": 4}, {"step": 1}):
            with self.subTest(changes=changes):
                def handler(connection):
                    connection.sendall(record(0, step=0))
                    self.batch_begin(connection, case, 0, **changes)

                with patch.object(probe, "inhibit_sleep"):
                    summary, _ = self.run_server(handler, batch_cases=[case])
                self.assertFalse(summary["ok"])
                self.assertIn("error", summary)

    def test_batch_missing_case_done_and_unexpected_eof_never_reconnect(self):
        case = self.short_cases()[0]
        for payload in (record(6, step=0), record(0, step=0), record(0, step=0)[:4]):
            with self.subTest(payload=payload):
                with patch.object(probe, "inhibit_sleep"):
                    summary, logs = self.run_server(lambda connection: connection.sendall(payload), batch_cases=[case])
                self.assertFalse(summary["ok"])
                self.assertFalse(summary["complete"])
                self.assertEqual(sum(item["event"] == "connected" for item in logs), 1)

    def test_batch_fault_expectations_cannot_be_claimed_without_evidence(self):
        for kind in (2, 4, 5, 6):
            with self.subTest(kind=kind):
                case = next(dict(case, id=1) for case in self.short_cases() if case["kind"] == kind)

                def handler(connection):
                    _, step = self.batch_case(connection, case, 0, defect="fault")
                    connection.sendall(record(6, step=step))

                with patch.object(probe, "inhibit_sleep"):
                    summary, _ = self.run_server(handler, batch_cases=[case])
                self.assertFalse(summary["ok"])
                self.assertTrue(summary["complete"])
                self.assertEqual(summary["expected_faults"][0]["status"], "fail")
                self.assertEqual(summary["cases"][0]["status"], "fail")

    def test_batch_rejects_malformed_manifest(self):
        for changes in ({"id": 2}, {"rate": 1}, {"hz": 59}, {"flags": 2}, {"cpu": True}, {"nominal_frames": 65536}, {"rx_bytes": 0}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                case = dict(self.short_cases()[0], **changes)
                (output / "run.json").write_text(json.dumps(dict(protocol="UXP1", token=TOKEN, mode="batch", cases=[case])))
                with self.assertRaises(ValueError), patch.object(probe.socket, "create_connection") as connect:
                    probe.collect("127.0.0.1", output, 1)
                connect.assert_not_called()

    def test_windows_sleep_inhibition_uses_system_required_only(self):
        with patch.object(probe.sys, "platform", "win32"), patch.object(probe.ctypes, "windll", create=True) as api:
            api.kernel32.SetThreadExecutionState.return_value = 0x80000000
            probe.inhibit_sleep(True)
            probe.inhibit_sleep(False)
            self.assertEqual(api.kernel32.SetThreadExecutionState.call_args_list,
                             [unittest.mock.call(0x80000001), unittest.mock.call(0x80000000)])
            api.kernel32.SetThreadExecutionState.return_value = 0
            with self.assertRaises(OSError):
                probe.inhibit_sleep(True)

    def test_batch_sleep_acquisition_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "run.json").write_text(json.dumps(dict(protocol="UXP1", token=TOKEN, mode="batch", cases=self.short_cases())))
            with patch.object(probe, "inhibit_sleep", side_effect=OSError("sleep inhibition failed")) as sleep:
                summary = probe.collect("127.0.0.1", output, 1)
            self.assertFalse(summary["ok"])
            self.assertIn("sleep inhibition failed", summary["error"])
            sleep.assert_called_once_with(True)

    def test_batch_framing_delay_respects_global_deadline_without_sending(self):
        case = next(dict(case, id=1) for case in self.short_cases() if case["kind"] == 6)

        def handler(connection):
            self.batch_begin(connection, case, 0)
            connection.sendall(record(14, case["rate"], 0, 1, count=1, reserved=6))
            self.assertEqual(connection.recv(1), b"")

        started = time.monotonic()
        with patch.object(probe, "inhibit_sleep"):
            summary, logs = self.run_server(handler, timeout=0.05, batch_cases=[case])
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["failed_case"], 1)
        self.assertIn("deadline expired", summary["error"])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(any(item["event"] == "host_sent_rx" for item in logs))

    def test_batch_reconnect_challenge_notices_are_diagnostic(self):
        case = next(dict(case, id=1) for case in self.short_cases() if case["kind"] == 3)
        for notice_bits in (0, 1, 2, 3):
            with self.subTest(notice_bits=notice_bits):
                def handler(connection, listener):
                    try:
                        connection, step = self.batch_case(connection, case, 0, listener=listener, notice_bits=notice_bits)
                        connection.sendall(record(6, step=step))
                    finally:
                        connection.close()

                with patch.object(probe, "inhibit_sleep"):
                    summary, _ = self.run_server(handler, batch_cases=[case], reconnect=True)
                self.assertTrue(summary["ok"], summary.get("error"))
                self.assertEqual(summary["expected_faults"][0]["reconnect_notice_bits"], notice_bits)
                self.assertEqual(summary["expected_faults"][0]["sum16"], sum(probe.pattern(32)))

    def test_batch_bad_reconnect_proof_fails_but_clean_case_and_done_continue(self):
        reconnect_case = next(dict(case, id=1) for case in self.short_cases() if case["kind"] == 3)
        clean_case = dict(self.short_cases()[0], id=2)
        for defect in ("challenge", "missing_challenge", "unknown_notices", "wrong_fault_kind"):
            with self.subTest(defect=defect):
                def handler(connection, listener):
                    try:
                        connection, step = self.batch_case(connection, reconnect_case, 0, listener=listener, defect=defect)
                        connection, step = self.batch_case(connection, clean_case, step)
                        connection.sendall(record(6, step=step))
                    finally:
                        connection.close()

                with patch.object(probe, "inhibit_sleep"):
                    summary, _ = self.run_server(handler, batch_cases=[reconnect_case, clean_case], reconnect=True)
                self.assertFalse(summary["ok"])
                self.assertTrue(summary["complete"])
                self.assertEqual([case["status"] for case in summary["cases"]], ["fail", "pass"])
                self.assertEqual(summary["expected_faults"][0]["status"], "fail")

    def test_batch_failed_phase_or_case_continues_without_weakening_pass(self):
        for failed_phase in (False, True):
            with self.subTest(failed_phase=failed_phase):
                failed_case = dict(self.short_cases()[0], id=1)
                clean_case = dict(failed_case, id=2)

                def handler(connection):
                    self.batch_begin(connection, failed_case, 0)
                    if failed_phase:
                        connection.sendall(record(11, failed_case["rate"], 1, 0, count=0, result=1,
                                                  reserved=failed_case["nominal_frames"]))
                    connection.sendall(record(13, failed_case["rate"], 1, 0, count=1, result=1))
                    _, step = self.batch_case(connection, clean_case, 0)
                    connection.sendall(record(6, step=step))

                with patch.object(probe, "inhibit_sleep"):
                    summary, _ = self.run_server(handler, batch_cases=[failed_case, clean_case])
                self.assertFalse(summary["ok"])
                self.assertTrue(summary["complete"])
                self.assertEqual([case["status"] for case in summary["cases"]], ["fail", "pass"])
                self.assertEqual(len(summary["phases"]), 2 if failed_phase else 1)
                if failed_phase:
                    self.assertEqual(summary["phases"][0]["status"], "fail")

    def test_batch_initial_connection_wait_preserves_execution_budget(self):
        case = self.short_cases()[0]
        create_connection = probe.socket.create_connection

        def delayed_connect(*args, **kwargs):
            time.sleep(0.15)
            return create_connection(*args, **kwargs)

        def handler(connection):
            time.sleep(0.15)
            _, step = self.batch_case(connection, case, 0)
            connection.sendall(record(6, step=step))

        with patch.object(probe, "inhibit_sleep"), patch.object(probe.socket, "create_connection", side_effect=delayed_connect):
            summary, logs = self.run_server(handler, timeout=0.25, batch_cases=[case])
        self.assertTrue(summary["ok"], summary.get("error"))
        self.assertGreaterEqual(summary["initial_wait_seconds"], 0.14)
        self.assertGreater(summary["elapsed_seconds"], 0.25)
        self.assertEqual(sum(item["event"] == "host_execution_started" for item in logs), 1)

    def test_batch_reconnect_does_not_refresh_execution_deadline(self):
        case = next(dict(case, id=1) for case in self.short_cases() if case["kind"] == 3)
        create_connection = probe.socket.create_connection

        def delayed_connect(*args, **kwargs):
            time.sleep(0.08)
            return create_connection(*args, **kwargs)

        def handler(connection, listener):
            try:
                self.batch_begin(connection, case, 0)
                connection.sendall(record(14, 0, 1, 0, count=1, reserved=3))
                with self.assertRaises((ConnectionResetError, ConnectionAbortedError)):
                    connection.recv(1)
                connection.close()
                connection, _ = listener.accept()
                self.assertEqual(probe.receive_exact(connection, 32, time.monotonic() + 1), probe.pattern(32))
                time.sleep(0.12)
                try:
                    connection.sendall(record(15, 0, 1, 0, count=1, total=sum(probe.pattern(32)), busy=32, reserved=3) +
                                       record(13, 0, 1, 0, count=1) + record(6, step=0))
                except (ConnectionResetError, ConnectionAbortedError):
                    pass
            finally:
                connection.close()

        with patch.object(probe, "inhibit_sleep"), patch.object(probe.socket, "create_connection", side_effect=delayed_connect):
            summary, logs = self.run_server(handler, timeout=0.25, batch_cases=[case], reconnect=True)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["complete"])
        self.assertIn("timed out", summary["error"])
        self.assertEqual(sum(item["event"] == "host_execution_started" for item in logs), 1)

    def test_batch_default_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "run.json").write_text(json.dumps(dict(mode="batch")))
            with patch.object(probe, "device_ip", return_value="127.0.0.1"), patch.object(probe, "collect", return_value={"ok": True}) as collect, patch("builtins.print"):
                self.assertEqual(probe.main(["collect", "--device", "Next", "--client-root", tmp, "--output", tmp]), 0)
            self.assertEqual(collect.call_args.args[2], 9600)


if __name__ == "__main__":
    unittest.main()
