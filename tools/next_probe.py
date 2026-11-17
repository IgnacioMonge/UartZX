"""Build the Next UART probe, or collect its run-matched diagnostic stream."""

import argparse
import ctypes
import importlib
import ipaddress
import json
import math
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib


EVENTS = ("boot", "ready_rx", "result_rx", "ready_tx", "result_tx", "config", "done", "fatal", "skip",
          "ready_duplex", "result_duplex", "phase_done", "case_begin", "case_done", "fault_ready", "fault_result")
RATES = (115200, 1152000, 2000000)
SOAK_FRAMES = 15000
ROOT = Path(__file__).resolve().parents[1]


def pattern(count):
    data = bytearray()
    state = 1
    for _ in range(count):
        data.append(state)
        state = (state >> 1) ^ (0xB8 if state & 1 else 0)
    return bytes(data)


def parse_record(data, token):
    if len(data) != 32:
        raise ValueError(f"truncated record: {len(data)}/32 bytes")
    if data[:4] != b"UXP1":
        raise ValueError("wrong record magic")
    if struct.unpack_from("<I", data, 28)[0] != token:
        raise ValueError("wrong run token")
    if data[4] >= len(EVENTS) or data[5] >= len(RATES) or data[6] > 1:
        raise ValueError("invalid event, rate or flow")
    record = dict(zip(
        ("event_id", "rate_index", "flow", "result", "raw_board", "video", "core_major", "core_sub", "rx_error", "frame"),
        data[4:14],
    ))
    record.update(zip(("step", "count", "sum16", "bad16", "txbusy16", "elapsed_frames", "reserved"), struct.unpack_from("<7H", data, 14)))
    record.update(event=EVENTS[data[4]], baud=RATES[data[5]], token=token)
    return record


def remaining(deadline):
    duration = deadline - time.monotonic()
    if duration <= 0:
        raise TimeoutError("probe deadline expired")
    return duration


def receive_exact(connection, count, deadline):
    data = bytearray()
    while len(data) < count:
        connection.settimeout(remaining(deadline))
        chunk = connection.recv(count - len(data))
        if not chunk:
            raise EOFError(f"truncated stream: {len(data)}/{count} bytes")
        data.extend(chunk)
    return bytes(data)


def exchange_duplex(connection, payload, count, baud, deadline, paced=True):
    stopped = threading.Event()
    errors = []

    def send():
        started = time.monotonic()
        try:
            for offset in range(0, len(payload), 1024):
                if stopped.wait(max(0, started + offset * 10 / baud - time.monotonic()) if paced else 0):
                    return
                remaining(deadline)
                connection.sendall(payload[offset:offset + 1024])
        except (OSError, TimeoutError) as error:
            errors.append(error)
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    connection.settimeout(remaining(deadline))
    sender = threading.Thread(target=send, name="next-probe-duplex")
    sender.start()
    try:
        actual = receive_exact(connection, count, deadline)
        sender.join(remaining(deadline))
        if sender.is_alive():
            raise TimeoutError("duplex sender exceeded probe deadline")
        if errors:
            raise errors[0]
        return actual
    except (OSError, EOFError):
        if errors:
            raise errors[0]
        raise
    finally:
        stopped.set()
        if sender.is_alive():
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sender.join(1)


def device_ip(client_root, name):
    client = Path(client_root).resolve() / "client"
    expected = client / "bridgezx" / "config.py"
    if not expected.is_file():
        raise ValueError(f"BridgeZX client missing: {expected}")
    sys.path.insert(0, str(client))
    try:
        config = importlib.import_module("bridgezx.config")
        if Path(config.__file__).resolve() != expected:
            raise ValueError("bridgezx.config was imported from a different client")
        try:
            address = config.load_config()["devices"][name]["ip"]
        except (KeyError, TypeError) as error:
            raise ValueError(f"device {name!r} missing from official BridgeZX configuration") from error
        return str(ipaddress.ip_address(address))
    finally:
        sys.path.pop(0)


def batch_plan():
    cases = []

    def add(kind=0, rate=0, cpu=3, hz=50, flags=0, nominal_frames=0,
            rx_bytes=0, tx_bytes=0, pause_frames=0):
        cases.append(dict(id=len(cases) + 1, kind=kind, rate=rate, cpu=cpu, hz=hz, flags=flags,
                          nominal_frames=nominal_frames, rx_bytes=rx_bytes, tx_bytes=tx_bytes,
                          pause_frames=pause_frames))

    for cpu in range(4):
        for rate in (0, 2):
            add(rate=rate, cpu=cpu, nominal_frames=1500, rx_bytes=8192, tx_bytes=8192)
    for hz, flags in ((60, 0), (50, 1)):
        for rate in (0, 2):
            add(rate=rate, hz=hz, flags=flags, nominal_frames=1500, rx_bytes=8192, tx_bytes=8192)
    add(kind=1, rate=2, flags=1, rx_bytes=4096, pause_frames=100)
    add(kind=2, pause_frames=25)
    add(kind=3)
    add(kind=4, rate=2, rx_bytes=8192, tx_bytes=8192)
    add(kind=5, rate=2, flags=1, rx_bytes=4096, pause_frames=100)
    add(kind=6, flags=1, rx_bytes=4096, pause_frames=100)
    for rate in (0, 2):
        add(rate=rate, nominal_frames=250, rx_bytes=8192, tx_bytes=8192)
    for _ in range(12):
        for rate in (0, 2):
            add(rate=rate, nominal_frames=15000, rx_bytes=8192, tx_bytes=8192)
    return cases


def build(output, soak=False, batch=False):
    if soak and batch:
        raise ValueError("--soak and --batch are mutually exclusive")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("run.json", "UXPROBE.NEX", "probe.bin", "probe.sym", "batch_plan.inc")):
        raise ValueError("output already contains probe artifacts; choose a new directory")
    token = secrets.randbits(32)
    cases = batch_plan() if batch else None
    if batch:
        lines = ["probe_batch_plan:"]
        for case in cases:
            lines.extend((f"    dw {case['id']}",
                          "    db " + ",".join(str(case[key]) for key in ("kind", "rate", "cpu", "hz", "flags")) + ",0",
                          "    dw " + ",".join(str(case[key]) for key in ("nominal_frames", "rx_bytes", "tx_bytes", "pause_frames"))))
        (output / "batch_plan.inc").write_text("\n".join(lines + ["    dw 0", ""]), encoding="ascii")
    subprocess.run([
        "sjasmplus", "--nologo", "--msg=war", f"-DPROBE_TOKEN=0x{token:08X}",
        *(["-DPROBE_SOAK=1", f"-DPROBE_SOAK_FRAMES={SOAK_FRAMES}"] if soak else []),
        *(["-DPROBE_BATCH=1", f"-I{output.as_posix()}"] if batch else []),
        f"-I{(ROOT / 'examples').as_posix()}", f"-I{(ROOT / 'src').as_posix()}",
        "--raw=probe.bin", "--sym=probe.sym", (ROOT / "examples" / "next_probe.asm").as_posix(),
    ], cwd=output, check=True)
    for name in ("UXPROBE.NEX", "probe.bin", "probe.sym"):
        if not (output / name).is_file():
            raise ValueError(f"assembler did not produce {name}")
    run = {"protocol": "UXP1", "token": token}
    if soak:
        run.update(mode="soak", frames_per_rate=SOAK_FRAMES)
    if batch:
        run.update(mode="batch", cases=cases)
    (output / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    return output


def collect(address, output, timeout, port=6145):
    output = Path(output)
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    token = run["token"]
    if run.get("protocol") != "UXP1" or type(token) is not int or not 0 <= token <= 0xFFFFFFFF:
        raise ValueError("invalid run.json protocol or token")
    if run.get("mode") == "batch":
        return collect_batch(address, output, timeout, port, run)
    soak = run.get("mode", "quick") == "soak"
    frames_per_rate = run.get("frames_per_rate")
    if run.get("mode", "quick") not in ("quick", "soak") or (soak and (type(frames_per_rate) is not int or not 1 <= frames_per_rate <= 65535)):
        raise ValueError("invalid run.json mode or frames_per_rate")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if (output / "summary.json").exists():
        raise ValueError("summary.json already exists; preserve the previous collection")
    started = time.monotonic()
    deadline = started + timeout
    summary = {"ok": False, "token": token, "results": [], "skipped": [], "configs": []}
    if soak:
        summary.update(mode="soak", complete=False, frames_per_rate=frames_per_rate, per_rate=[],
                       host_pacing={"packet_bytes": 1024, "bits_per_byte": 10, "tcp_nodelay": True},
                       totals={"rx_bytes": 0, "tx_bytes": 0, "chunks": 0, "overlap_count": 0})
    active_phase = None
    finished_rows = set()
    last_step = 0
    pending = {}
    connection = None
    with (output / "telemetry.jsonl").open("x", encoding="utf-8") as log:
        def emit(event):
            log.write(json.dumps({"host_seconds": round(time.monotonic() - started, 6), **event}) + "\n")
            log.flush()

        try:
            while connection is None:
                try:
                    connection = socket.create_connection((address, port), timeout=min(1, remaining(deadline)))
                    if soak:
                        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError as error:
                    emit({"event": "connect_retry", "error": str(error)})
                    time.sleep(min(0.25, remaining(deadline)))
            emit({"event": "connected"})
            while True:
                record = parse_record(receive_exact(connection, 32, deadline), token)
                emit(record)
                event = record["event"]
                if soak and event == "config":
                    row = (record["rate_index"], record["flow"])
                    if active_phase is not None or pending or row not in ((0, 1), (2, 1)) or row in finished_rows:
                        raise ValueError("unexpected soak configuration")
                    active_phase = {"rate_index": row[0], "flow": row[1], "baud": record["baud"],
                                    "chunks": 0, "rx_bytes": 0, "tx_bytes": 0, "overlap_count": 0,
                                    "chunk_elapsed_frames": 0}
                    summary["configs"].append(record)
                elif soak and event == "ready_duplex":
                    if (pending or active_phase is None or
                            any(record[key] != active_phase[key] for key in ("rate_index", "flow")) or
                            record["step"] <= last_step or record["count"] != 8192 or record["reserved"] != 2048):
                        raise ValueError("unexpected soak duplex ready or payload size")
                    last_step = record["step"]
                    active_phase.setdefault("started", time.monotonic())
                    pending["duplex"] = record
                    payload = pattern(record["count"])
                    record["host_rx_crc32"] = f"{zlib.crc32(payload):08x}"
                    expected = pattern(record["reserved"])
                    actual = exchange_duplex(connection, payload, len(expected), record["baud"], deadline)
                    emit({"event": "host_sent_rx", "count": len(payload), "crc32": record["host_rx_crc32"]})
                    record.update(host_payload_ok=actual == expected, host_tx_crc32=f"{zlib.crc32(actual):08x}",
                                  expected_tx_crc32=f"{zlib.crc32(expected):08x}")
                    emit({"event": "host_received_tx", "count": len(actual), "ok": actual == expected,
                          "crc32": record["host_tx_crc32"], "expected_crc32": record["expected_tx_crc32"]})
                elif soak and event == "result_duplex":
                    ready = pending.pop("duplex", None)
                    if ready is None or any(record[key] != ready[key] for key in ("step", "rate_index", "flow")):
                        raise ValueError("unmatched result_duplex")
                    expected = pattern(ready["count"])
                    complete = (record["result"] == 0 and record["count"] == len(expected) and
                                record["sum16"] == (sum(expected) & 0xFFFF) and record["bad16"] == 0 and
                                record["rx_error"] == 0 and ready["host_payload_ok"] and
                                0 < record["elapsed_frames"] <= 65535 and record["reserved"] <= len(expected))
                    status = "pass" if complete else "fail"
                    summary["results"].append({**record, "status": status, "expected_count": len(expected),
                                               "expected_tx_count": ready["reserved"], "host_rx_crc32": ready["host_rx_crc32"],
                                               "host_tx_crc32": ready["host_tx_crc32"], "expected_tx_crc32": ready["expected_tx_crc32"],
                                               "overlap_count": record["reserved"]})
                    for key, value in (("chunks", 1), ("rx_bytes", record["count"]), ("tx_bytes", ready["reserved"]),
                                       ("overlap_count", record["reserved"])):
                        active_phase[key] += value
                        summary["totals"][key] += value
                    active_phase["chunk_elapsed_frames"] += record["elapsed_frames"]
                    emit({"event": "host_assessment", "direction": "duplex", "step": record["step"], "status": status,
                          "rx_bytes": record["count"], "tx_bytes": ready["reserved"], "overlap_count": record["reserved"]})
                elif soak and event == "phase_done":
                    if (pending or active_phase is None or not active_phase["chunks"] or
                            any(record[key] != active_phase[key] for key in ("rate_index", "flow")) or
                            record["step"] != last_step or record["count"] != active_phase["chunks"] or
                            record["result"] != 0 or record["reserved"] not in (frames_per_rate, frames_per_rate * 6 // 5) or
                            record["elapsed_frames"] < record["reserved"]):
                        raise ValueError("invalid or short soak phase_done")
                    if not active_phase["overlap_count"]:
                        raise ValueError("soak phase has no RX/TX overlap")
                    seconds = time.monotonic() - active_phase.pop("started")
                    active_phase.update(elapsed_frames=record["elapsed_frames"], target_frames=record["reserved"], elapsed_seconds=round(seconds, 6),
                                        rx_bytes_per_second=round(active_phase["rx_bytes"] / seconds, 3),
                                        tx_bytes_per_second=round(active_phase["tx_bytes"] / seconds, 3))
                    summary["per_rate"].append(active_phase)
                    finished_rows.add((record["rate_index"], record["flow"]))
                    active_phase = None
                elif soak and event == "skip":
                    row = (record["rate_index"], record["flow"])
                    if active_phase is not None or pending or row not in ((0, 1), (2, 1)) or row in finished_rows:
                        raise ValueError("unexpected soak skip")
                    summary["skipped"].append(record)
                    finished_rows.add(row)
                elif soak and event == "done":
                    if pending or active_phase is not None or record["result"] != 0 or finished_rows != {(0, 1), (2, 1)}:
                        raise ValueError("done before all soak phases completed")
                    summary["complete"] = not summary["skipped"]
                    summary["ok"] = (summary["complete"] and bool(summary["results"]) and
                                     all(item["status"] == "pass" for item in summary["results"]) and
                                     all(item["result"] == 0 for item in summary["configs"]))
                    break
                elif soak and event not in ("boot", "fatal"):
                    raise ValueError(f"unexpected soak event: {event}")
                elif not soak and event in ("ready_duplex", "result_duplex", "phase_done", "case_begin", "case_done", "fault_ready", "fault_result"):
                    raise ValueError(f"unexpected quick event: {event}")
                elif event in ("ready_rx", "ready_tx"):
                    direction = event[-2:]
                    if direction in pending:
                        raise ValueError(f"duplicate ready_{direction}")
                    count = 4096 if direction == "rx" else 2048
                    if record["count"] != count:
                        raise ValueError(f"invalid ready_{direction} size: expected {count}, got {record['count']}")
                    payload = pattern(count)
                    pending[direction] = record
                    if direction == "rx":
                        connection.settimeout(remaining(deadline))
                        connection.sendall(payload)
                        emit({"event": "host_sent_rx", "count": len(payload), "crc32": f"{zlib.crc32(payload):08x}"})
                    else:
                        actual = receive_exact(connection, len(payload), deadline)
                        record["host_payload_ok"] = actual == payload
                        record["host_crc32"] = f"{zlib.crc32(actual):08x}"
                        record["expected_crc32"] = f"{zlib.crc32(payload):08x}"
                        emit({"event": "host_received_tx", "count": len(actual), "ok": actual == payload,
                              "crc32": record["host_crc32"], "expected_crc32": record["expected_crc32"]})
                elif event in ("result_rx", "result_tx"):
                    direction = event[-2:]
                    ready = pending.pop(direction, None)
                    if ready is None or any(record[key] != ready[key] for key in ("step", "rate_index", "flow")):
                        raise ValueError(f"unmatched {event}")
                    expected = pattern(ready["count"])
                    complete = record["result"] == 0 and record["count"] == len(expected)
                    if direction == "rx":
                        complete = complete and record["sum16"] == (sum(expected) & 0xFFFF) and record["bad16"] == 0 and record["rx_error"] == 0
                        status = "pass" if complete else "fail"
                        if record["flow"] == 0:
                            status = "observed_without_flow"
                    else:
                        complete = complete and ready["host_payload_ok"] and record["sum16"] == (sum(expected) & 0xFFFF) and record["bad16"] == 0
                        status = "pass" if complete else "fail"
                    result = {**record, "status": status, "expected_count": len(expected)}
                    if direction == "tx":
                        result.update(host_crc32=ready["host_crc32"], expected_crc32=ready["expected_crc32"])
                    summary["results"].append(result)
                    emit({"event": "host_assessment", "direction": direction, "step": record["step"], "status": status})
                elif event == "skip":
                    summary["skipped"].append(record)
                elif event == "config":
                    summary["configs"].append(record)
                elif event == "fatal":
                    raise ValueError(f"device fatal result={record['result']} step={record['step']}")
                elif event == "done":
                    if pending or record["result"] != 0 or not summary["results"]:
                        raise ValueError("done with pending stages, failed status or no results")
                    required = {(rate, flow) for rate in (0, 2) for flow in (0, 1)}
                    skipped = {(item["rate_index"], item["flow"]) for item in summary["skipped"]}
                    configured = {(item["rate_index"], item["flow"]) for item in summary["configs"]}
                    if not required <= configured | skipped:
                        raise ValueError("done before all matrix configurations were reported")
                    for rate, flow in required - skipped:
                        events = {item["event"] for item in summary["results"] if (item["rate_index"], item["flow"]) == (rate, flow)}
                        if events != {"result_rx", "result_tx"}:
                            raise ValueError(f"missing RX/TX results for rate={rate}, flow={flow}")
                    summary["complete"] = not bool(required & skipped)
                    summary["ok"] = not any(item["status"] == "fail" for item in summary["results"]) and all(item["result"] == 0 for item in summary["configs"])
                    break
        except (OSError, ValueError, EOFError) as error:
            summary["error"] = str(error)
            emit({"event": "host_error", "error": str(error)})
        finally:
            if connection is not None:
                connection.close()
            summary["elapsed_seconds"] = round(time.monotonic() - started, 6)
            emit({"event": "host_summary", **summary})
            (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def inhibit_sleep(active):
    if sys.platform == "win32":
        if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if active else 0x80000000):
            raise OSError("SetThreadExecutionState failed")


def collect_batch(address, output, timeout, port, run):
    cases = run.get("cases")
    keys = {"id", "kind", "rate", "cpu", "hz", "flags", "nominal_frames", "rx_bytes", "tx_bytes", "pause_frames"}
    if not isinstance(cases, list) or not cases:
        raise ValueError("invalid batch manifest")
    for index, case in enumerate(cases, 1):
        if (not isinstance(case, dict) or set(case) != keys or
                any(type(value) is not int or not 0 <= value <= 65535 for value in case.values()) or
                case["id"] != index or case["kind"] not in range(7) or case["rate"] not in (0, 2) or
                case["cpu"] not in range(4) or case["hz"] not in (50, 60) or case["flags"] not in (0, 1) or
                case["nominal_frames"] * case["hz"] // 50 > 65535 or
                case["pause_frames"] * case["hz"] // 50 > 65535 or
                (case["kind"] == 0 and (not case["nominal_frames"] or not case["rx_bytes"] or not case["tx_bytes"])) or
                (case["kind"] == 4 and (case["rx_bytes"] <= 128 or not case["tx_bytes"])) or
                (case["kind"] in (1, 5, 6) and (not case["rx_bytes"] or case["tx_bytes"] or not case["pause_frames"])) or
                (case["kind"] in (2, 3) and (case["rx_bytes"] or case["tx_bytes"])) or
                (case["kind"] == 2 and not case["pause_frames"])):
            raise ValueError("invalid batch manifest case")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if (output / "summary.json").exists():
        raise ValueError("summary.json already exists; preserve the previous collection")
    started = time.monotonic()
    deadline = started + timeout
    summary = dict(ok=False, complete=False, mode="batch", token=run["token"], results=[], expected_faults=[],
                   cases=[], phases=[], configs=[], skipped=[], totals=dict(rx_bytes=0, tx_bytes=0, chunks=0, overlap_count=0),
                   host_pacing=dict(packet_bytes=1024, bits_per_byte=10, tcp_nodelay=True))
    active = pending = None
    last_step = 0
    connection = None
    sleep_held = False
    with (output / "telemetry.jsonl").open("x", encoding="utf-8") as log:
        def emit(event):
            log.write(json.dumps({"host_seconds": round(time.monotonic() - started, 6), **event}) + "\n")
            log.flush()

        def connect():
            while True:
                stream = None
                try:
                    stream = socket.create_connection((address, port), timeout=min(1, remaining(deadline)))
                    stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    emit({"event": "connected"})
                    return stream
                except OSError as error:
                    if stream is not None:
                        stream.close()
                    emit({"event": "connect_retry", "error": str(error)})
                    time.sleep(min(0.25, remaining(deadline)))

        try:
            inhibit_sleep(True)
            sleep_held = True
            connection = connect()
            deadline = time.monotonic() + timeout
            summary["initial_wait_seconds"] = round(time.monotonic() - started, 6)
            emit({"event": "host_execution_started", "timeout_seconds": timeout})
            while True:
                record = parse_record(receive_exact(connection, 32, deadline), run["token"])
                emit(record)
                event = record["event"]
                metadata = {key: record[key] for key in ("raw_board", "video", "core_major", "core_sub")}
                if metadata != summary.setdefault("device", metadata):
                    raise ValueError("batch device metadata changed")
                if event == "fatal":
                    raise ValueError(f"device fatal result={record['result']} step={record['step']}")
                if event == "boot":
                    if active or summary["cases"] or record["result"] or record["step"] != last_step:
                        raise ValueError("unexpected batch boot")
                    continue
                if event == "config":
                    if record["result"] or pending or record["step"] != last_step:
                        raise ValueError("invalid batch config")
                    summary["configs"].append(record)
                    continue
                if event == "case_begin":
                    index = len(summary["cases"])
                    if active or pending or index >= len(cases):
                        raise ValueError("unexpected batch case_begin")
                    case = cases[index]
                    flow = 0 if case["kind"] in (5, 6) else 1
                    target = case["nominal_frames"] * case["hz"] // 50
                    if (record["count"] != case["id"] or record["sum16"] != case["kind"] | (case["cpu"] << 8) or
                            record["bad16"] != case["hz"] or record["txbusy16"] != case["flags"] or
                            record["elapsed_frames"] != target or record["reserved"] or record["result"] or record["rx_error"] or
                            record["rate_index"] != case["rate"] or record["flow"] != flow or record["step"] != last_step):
                        raise ValueError("batch case_begin differs from ordered manifest/CPU/Hz")
                    active = dict(case, flow=flow, target_frames=target,
                                  expected_rx_bytes=case["rx_bytes"], expected_tx_bytes=case["tx_bytes"], chunks=0, rx_bytes=0, tx_bytes=0,
                                  overlap_count=0, started=time.monotonic(), phase_done=False, result_seen=False,
                                  fault_ready=False, fault_seen=False)
                    continue
                if event == "done":
                    if active or pending or len(summary["cases"]) != len(cases) or record["result"] or record["step"] != last_step:
                        raise ValueError("done before all batch cases completed")
                    summary["complete"] = True
                    summary["ok"] = all(item["status"] == "pass" for item in summary["cases"])
                    break
                if active is None:
                    raise ValueError(f"batch event outside case: {event}")
                if record["rate_index"] != active["rate"] or record["flow"] != active["flow"]:
                    raise ValueError("batch case rate/flow mismatch")
                kind = active["kind"]
                chunk_ready = event in ("ready_rx", "ready_duplex") or event == "fault_ready" and kind == 6
                if record["step"] != last_step + int(chunk_ready):
                    raise ValueError("batch chunk step mismatch")
                if chunk_ready:
                    if pending or active["phase_done"] or active["result_seen"] and kind != 0:
                        raise ValueError("unexpected batch payload ready")
                    last_step = record["step"]
                if event == "ready_duplex" and kind in (0, 4):
                    if record["count"] != case["rx_bytes"] or record["reserved"] != case["tx_bytes"]:
                        raise ValueError("batch duplex payload size differs from manifest")
                    payload = bytearray(pattern(record["count"]))
                    if kind == 4:
                        payload[128] ^= 1
                    pending = {**record, "payload": bytes(payload)}
                    actual = exchange_duplex(connection, payload, record["reserved"], record["baud"], deadline,
                                             paced=not active["flags"])
                    pending.update(host_payload_ok=actual == pattern(record["reserved"]),
                                   host_rx_crc32=f"{zlib.crc32(payload):08x}", host_tx_crc32=f"{zlib.crc32(actual):08x}",
                                   expected_tx_crc32=f"{zlib.crc32(pattern(record['reserved'])):08x}")
                    emit({"event": "host_payload", "case_id": case["id"], "step": last_step,
                          **{key: value for key, value in pending.items() if key.startswith("host_") or key == "expected_tx_crc32"}})
                elif event == "result_duplex" and kind in (0, 4):
                    if not pending or pending["event"] != "ready_duplex":
                        raise ValueError("unmatched batch result_duplex")
                    valid = (record["count"] == len(pending["payload"]) and
                             record["sum16"] == sum(pending["payload"]) & 0xFFFF and
                             record["result"] == (2 if kind == 4 else 0) and record["bad16"] == int(kind == 4) and
                             not record["rx_error"] and pending["host_payload_ok"] and record["elapsed_frames"] > 0 and
                             record["reserved"] <= record["count"])
                    result = {**record, "case_id": case["id"], "status": "pass" if valid else "fail",
                              **{key: value for key, value in pending.items() if key.startswith("host_") or key == "expected_tx_crc32"}}
                    (summary["expected_faults"] if kind == 4 else summary["results"]).append(result)
                    for key, value in (("chunks", 1), ("rx_bytes", record["count"]), ("tx_bytes", pending["reserved"]),
                                       ("overlap_count", record["reserved"])):
                        active[key] += value
                        summary["totals"][key] += value
                    active["result_seen"] = True
                    active["failed"] = active.get("failed", False) or not valid
                    pending = None
                    emit({"event": "host_assessment", "case_id": case["id"], "status": result["status"]})
                elif event == "phase_done" and kind == 0:
                    if (pending or active["phase_done"] or record["count"] != active["chunks"] or
                            record["reserved"] != active["target_frames"]):
                        raise ValueError("invalid batch phase chunk count or target")
                    if not record["result"] and (not active["chunks"] or record["rx_error"] or
                            record["elapsed_frames"] < active["target_frames"] or
                            not active["flags"] and not active["overlap_count"]):
                        raise ValueError("invalid batch phase duration/chunks/overlap")
                    failed = bool(record["result"])
                    active.update(phase_done=True, elapsed_frames=record["elapsed_frames"],
                                  failed=active.get("failed", False) or failed)
                    summary["phases"].append({**record, "case_id": case["id"], "status": "fail" if failed else "pass"})
                elif event == "ready_rx" and kind in (1, 5):
                    if record["count"] != case["rx_bytes"]:
                        raise ValueError("batch RX size differs from manifest")
                    pending = record
                    payload = pattern(record["count"])
                    connection.settimeout(remaining(deadline))
                    connection.sendall(payload)
                    emit({"event": "host_sent_rx", "case_id": case["id"], "count": len(payload), "crc32": f"{zlib.crc32(payload):08x}"})
                    active["rx_bytes"] += len(payload)
                    active["chunks"] += 1
                    summary["totals"]["rx_bytes"] += len(payload)
                    summary["totals"]["chunks"] += 1
                elif event == "result_rx" and kind in (1, 5):
                    if not pending or pending["event"] != "ready_rx":
                        raise ValueError("unmatched batch result_rx")
                    if kind == 1:
                        valid = (not record["result"] and not record["rx_error"] and not record["bad16"] and
                                 record["count"] == case["rx_bytes"] and record["sum16"] == sum(pattern(case["rx_bytes"])) & 0xFFFF)
                        summary["results"].append({**record, "case_id": case["id"], "status": "pass" if valid else "fail"})
                        active["failed"] = not valid
                    active["result_seen"] = True
                    pending = None
                elif event == "fault_ready" and kind in (2, 3, 6):
                    if pending or active["fault_ready"] or record["count"] != case["id"] or record["reserved"] != kind:
                        raise ValueError("invalid batch fault_ready")
                    active["fault_ready"] = True
                    if kind == 3:
                        time.sleep(min(0.1, remaining(deadline)))
                        remaining(deadline)
                        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                              struct.pack("hh" if sys.platform == "win32" else "ii", 1, 0))
                        connection.close()
                        connection = None
                        emit({"event": "host_planned_reset", "case_id": case["id"]})
                        connection = connect()
                        payload = pattern(32)
                        connection.settimeout(remaining(deadline))
                        connection.sendall(payload)
                        active["rx_bytes"] += len(payload)
                        summary["totals"]["rx_bytes"] += len(payload)
                        emit({"event": "host_reconnect_challenge", "case_id": case["id"], "count": len(payload),
                              "crc32": f"{zlib.crc32(payload):08x}"})
                    elif kind == 6:
                        # Let SEND OK finish and the FPGA switch to 9600 before sending.
                        emit({"event": "host_wait_framing", "case_id": case["id"], "wait_seconds": 1.0})
                        delay = min(1.0, remaining(deadline))
                        time.sleep(delay)
                        if delay < 1.0:
                            raise TimeoutError("probe deadline expired before framing payload")
                        payload = pattern(case["rx_bytes"])
                        connection.settimeout(remaining(deadline))
                        connection.sendall(payload)
                        active["rx_bytes"] += len(payload)
                        active["chunks"] += 1
                        summary["totals"]["rx_bytes"] += len(payload)
                        summary["totals"]["chunks"] += 1
                        emit({"event": "host_sent_rx", "case_id": case["id"], "count": len(payload), "crc32": f"{zlib.crc32(payload):08x}"})
                elif event == "fault_result" and kind in (2, 3, 5, 6):
                    if (active["fault_seen"] or record["count"] != case["id"] or
                            kind != 5 and not active["fault_ready"] or kind == 5 and not active["chunks"]):
                        raise ValueError("unmatched batch fault_result")
                    if kind == 5:
                        pending = None
                    valid = record["result"] == 0
                    if kind in (5, 6):
                        valid = (valid and bool(record["rx_error"] & (4 if kind == 5 else 64)) and
                                 record["bad16"] == 1 and record["txbusy16"] == 1 and record["sum16"] <= case["rx_bytes"])
                    elif kind == 3:
                        valid = (valid and record["sum16"] == sum(pattern(32)) and not record["bad16"] and
                                 record["txbusy16"] == 32 and not record["rx_error"] and
                                 record["reserved"] & 255 == 3 and record["reserved"] >> 8 & ~3 == 0)
                        record["reconnect_notice_bits"] = record["reserved"] >> 8
                    else:
                        valid = (valid and record["sum16"] == 1 and not record["rx_error"] and
                                 record["elapsed_frames"] >= case["pause_frames"] * case["hz"] // 50)
                    summary["expected_faults"].append({**record, "case_id": case["id"], "status": "pass" if valid else "fail"})
                    active["failed"] = active.get("failed", False) or not valid
                    active["fault_seen"] = True
                elif event == "case_done":
                    if (pending or record["count"] != case["id"] or
                            any(record[key] for key in ("sum16", "bad16", "txbusy16", "elapsed_frames", "reserved", "rx_error")) or
                            not record["result"] and (kind == 0 and not active["phase_done"] or
                            kind in (1, 4) and not active["result_seen"] or
                            kind in (2, 3, 5, 6) and not active["fault_seen"])):
                        raise ValueError("batch case_done before required results/phase")
                    seconds = time.monotonic() - active.pop("started")
                    active.update(status="fail" if record["result"] or active.get("failed") else "pass", elapsed_seconds=round(seconds, 6))
                    summary["cases"].append(active)
                    emit({"event": "host_case_assessment", **active})
                    active = None
                else:
                    raise ValueError(f"unexpected batch event {event} for kind {kind}")
        except (OSError, ValueError, EOFError) as error:
            summary["error"] = str(error)
            if active:
                summary["failed_case"] = active["id"]
            emit({"event": "host_error", "error": str(error)})
        finally:
            if connection is not None:
                connection.close()
            if sleep_held:
                try:
                    inhibit_sleep(False)
                except OSError as error:
                    summary.update(ok=False, error=str(error))
            summary["elapsed_seconds"] = round(time.monotonic() - started, 6)
            emit({"event": "host_summary", **summary})
            (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--output", required=True, type=Path)
    modes = make.add_mutually_exclusive_group()
    modes.add_argument("--soak", action="store_true")
    modes.add_argument("--batch", action="store_true")
    read = commands.add_parser("collect")
    read.add_argument("--device", required=True)
    read.add_argument("--client-root", required=True, type=Path)
    read.add_argument("--output", required=True, type=Path)
    read.add_argument("--timeout", type=float)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            directory = build(args.output, soak=args.soak, batch=args.batch)
            for name in ("UXPROBE.NEX", "run.json", "probe.bin", "probe.sym"):
                print(directory / name)
            return 0
        address = device_ip(args.client_root, args.device)
        timeout = args.timeout
        if timeout is None:
            run = json.loads((args.output / "run.json").read_text(encoding="utf-8"))
            timeout = {"batch": 9600, "soak": 900}.get(run.get("mode"), 180)
        summary = collect(address, args.output, timeout)
        print(args.output / "summary.json")
        if not summary["ok"]:
            print(summary.get("error", "diagnostic checks failed"), file=sys.stderr)
        return 0 if summary["ok"] else 1
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"probe: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
