"""Run the drivers of another revision and of this tree side by side.

    python tools/compare_ports.py <base> {divmmc,next,ay,all} [--cases N] [--seed N] [--bitdelay N]

<base> is a Git revision (its src/ is read from the repository) or a directory
that holds a src/. Both drivers get the same random calls, registers, state
and device behaviour. Any difference in the port accesses (port, value, order,
count), in the documented results, in the state bytes, in a 600-byte buffer or
in the interrupt state is reported, and the exit status is 1.

Next and divMMC devices answer by the order of the reads, so the comparison is
of logic; the T-states each routine gained or lost are listed. The AY device is
a line waveform in time, counted from the first pin write of each driver call
(the gap between two calls belongs to the caller): there every access must also
fall on the same T-state. --bitdelay replaces UART_BIT_DELAY in both trees.

Not covered: uart_esp_reset (it needs frame interrupts; the test suite runs
it). Stack depth is read from a fill pattern: a pushed byte that happens to
equal the pattern is not counted, so depths can differ by a byte or two.

Needs what the tests need: sjasmplus on PATH and the Python z80 package.
"""

import argparse
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from harness import STACK, STOP, Driver  # noqa: E402

BUFFER = 0xC000
FILL = 0xA5
PARTS = {
    "divmmc": ["divmmc.inc", "send.inc", "divmmc_vars.inc"],
    "next": ["next.inc", "next_baud_9600.inc", "next_baud_57600.inc", "next_baud_230400.inc",
             "next_baud_1152000.inc", "next_baud_2000000.inc", "next_write_block.inc",
             "next_try_write.inc", "next_flush_budget.inc", "next_tx_idle.inc",
             "next_baud_khz.inc", "next_flow.inc", "send.inc", "next_vars.inc"],
    "ay": ["ay.inc", "ay_speed.inc", "send.inc", "ay_vars.inc"],
}
STATE = {"divmmc": ["uart_tx_failed"], "next": ["uart_tx_failed", "uart_rx_errors"],
         "ay": ["uart_second_avail", "uart_second_byte", "uart_rx_errors"]}
# routine: what its contract returns or preserves. "A?" is A when CF=1.
ALL = ("a", "bc", "de", "hl")
SPEC = {
    "divmmc": {
        "uart_init": ("de", "hl"), "uart_read": ("A?", "de", "hl"), "uart_read_raw": ("A?", "de", "hl"),
        "uart_ready": ("de", "hl"), "uart_write_fast": ("e", "hl"), "uart_write": ALL,
        "uart_flush": ("hl",), "uart_read_block": ("hl", "d"), "uart_write_string": ("hl", "bc", "de"),
        "uart_write_block": ("hl", "bc", "de"),
    },
    "next": {
        "uart_init": (), "uart_set_baud": (), "uart_read": ("A?", "de", "hl"),
        "uart_read_raw": ("A?", "de", "hl"), "uart_ready": ("de", "hl"), "uart_write": ALL,
        "uart_flush": ("hl",), "uart_flush_budget": ("hl",), "uart_read_block": ("hl", "d"),
        "uart_esp_reset_assert": ("e", "hl"), "uart_esp_reset_release": ("e", "hl"),
        "uart_flow_enable": ("hl",), "uart_flow_disable": ("hl",), "uart_try_write": ALL,
        "uart_tx_idle": ("de", "hl"), "uart_write_block_fast": ("hl", "bc"),
        "uart_latch_rx_error": ("a", "f", "bc", "de", "hl"), "uart_write_string": ("hl", "bc", "de"),
        "uart_write_block": ("hl", "bc", "de"),
    },
    "ay": {
        "uart_init": ("de", "hl"), "uart_init_keep_mixer": ("de", "hl"), "uart_read": ("A?", "de", "hl"),
        "uart_read_raw": ("A?", "de", "hl"), "uart_ready": ("bc", "de", "hl"), "uart_write": ALL,
        "uart_flush": (), "uart_read_block": ("hl", "d"), "uart_esp_set_9600": ("d",),
        "uart_write_string": ("hl", "bc", "de"), "uart_write_block": ("hl", "bc", "de"),
    },
}
WEIGHT = {"uart_flush": 0.15, "uart_esp_set_9600": 0.2, "uart_init": 0.4, "uart_init_keep_mixer": 0.4,
          "uart_read": 6, "uart_read_raw": 3, "uart_write": 2, "uart_read_block": 2,
          "uart_write_block": 0.4, "uart_write_string": 0.4, "uart_write_block_fast": 1.5}


class Quiet(Driver):
    """Driver without frame interrupts, run in slices the tick counter cannot wrap."""

    LIMIT = 600_000_000

    def call(self, name, **registers):
        m = self.m
        for register, value in registers.items():
            setattr(m, register, value)
        m.sp = STACK
        m.memory[STACK:STACK + 2] = STOP.to_bytes(2, "little")
        m.pc = self.symbols[name]
        self.took = 0
        while m.pc != STOP:
            if self.took > self.LIMIT:
                raise AssertionError(f"{name}: did not return, PC={m.pc:04x}")
            m.ticks_to_stop = 40000
            self._slice(m.run)
        took, self.took = self.took, 0
        self.elapsed += took
        return took


class Device:
    """Records every access as (direction, port, value, T-states since the origin)."""

    def begin(self, seed):
        self.rng = random.Random(seed)
        self.trace = []
        self.t0 = None

    def origin(self, port, write):
        return True

    def inp(self, port):
        now = self.clock()
        if self.t0 is None and self.origin(port, False):
            self.t0 = now
        t = None if self.t0 is None else now - self.t0
        value = self.read(port, t)
        self.trace.append(("i", port, value, t))
        return value

    def out(self, port, value):
        now = self.clock()
        self.write(port, value)  # may restart the time origin
        if self.t0 is None and self.origin(port, True):
            self.t0 = now
        self.trace.append(("o", port, value, None if self.t0 is None else now - self.t0))


class NextDevice(Device):
    nextreg = 0

    def begin(self, seed):
        super().begin(seed)
        r = self.rng
        self.p_ready = r.choice([0.0, 0.0003, 0.02, 0.5, 0.97, 1.0])
        self.p_full = r.choice([0.0, 0.0, 0.05, 0.6, 0.999, 1.0])
        self.p_err = r.choice([0.0, 0.0, 0.002, 0.05])
        self.p_other = r.choice([0.0, 0.02, 0.3])  # a handler that uses NextRegs

    def read(self, port, _):
        r = self.rng
        if port == 0x133B:
            value = r.getrandbits(8) & 0xB8
            value |= (r.random() < self.p_ready) | (r.random() < self.p_full) << 1
            value |= (r.random() < self.p_err) << 2 | (r.random() < self.p_err) << 6
            return value
        if port == 0x243B:
            return r.getrandbits(8) if r.random() < self.p_other else self.nextreg
        if port in (0x143B, 0x153B, 0x163B, 0x253B):
            return r.getrandbits(8)
        return 0xFF

    def write(self, port, value):
        if port == 0x243B:
            self.nextreg = value


class DivmmcDevice(Device):
    reg = 0

    def begin(self, seed):
        super().begin(seed)
        r = self.rng
        self.p_ready = r.choice([0.0, 0.02, 0.5, 0.97, 1.0])
        self.p_busy = r.choice([0.0, 0.0, 0.3, 0.99, 1.0])

    def read(self, port, _):
        r = self.rng
        if port == 0xFD3B and self.reg == 0xC7:
            return (r.getrandbits(8) & 0x3F) | (r.random() < self.p_ready) << 7 | (r.random() < self.p_busy) << 6
        return r.getrandbits(8)

    def write(self, port, value):
        if port == 0xFC3B:
            self.reg = value


class AyDevice(Device):
    reg = 0
    bit = 370  # T-states of a bit at the UART_BIT_DELAY under test

    def origin(self, port, write):
        # Selecting a register moves no pin: the first data write does.
        return write and port & 0xC002 == 0x8000

    def begin(self, seed):
        super().begin(seed)
        self.edges = sorted(self.wave(self.rng))  # when the line toggles; it starts high
        self.regs = {}

    def wave(self, r):
        """Whole frames, glitches, a held line, silence, or a mix."""
        kind = r.choice("FFFFFFGGTLSB")
        bit = self.bit
        edges = []
        if kind == "S":
            return edges
        if kind == "L":
            return [r.uniform(-50, 15000)]
        if kind in "FGB":
            # Early, anywhere in the listening window, or as the read gives up.
            at = r.choice([r.uniform(0, 400), r.uniform(0, 14500), r.uniform(12700, 13900)])
            period = bit * r.choice([0.9854, 0.9986, r.uniform(0.93, 1.07), r.uniform(1.9, 2.05),
                                     r.uniform(3.9, 4.1)])
            for _ in range(r.choice([1, 1, 2, 2, 3])):
                byte = r.getrandbits(8)
                bits = [0] + [(byte >> i) & 1 for i in range(8)] + [0 if r.random() < 0.15 else 1]
                level = 1
                for i, value in enumerate(bits):
                    if value != level:
                        edges.append(at + i * period)
                        level = value
                if level == 0:
                    edges.append(at + 10 * period)
                at += period * (10 + r.choice([0, 0, 0.02, 0.5, 1, 2.5, r.uniform(0, 12)]))
        if kind in "GT":
            at = r.uniform(-100, 3000)
            for _ in range(r.randrange(1, 60)):
                edges.append(at)
                at += r.choice([r.uniform(1, 60), r.uniform(20, 400), r.uniform(300, 2500),
                                r.uniform(2000, 9000)])
        if kind == "B":  # a burst where a byte ends: false starts of a second one
            at = bit * r.uniform(9.5, 15)
            for _ in range(r.randrange(2, 40)):
                edges.append(at)
                at += r.uniform(1, 90)
        return edges

    def level(self, t):
        if t is None:
            return 1
        lo, hi = 0, len(self.edges)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.edges[mid] <= t:
                lo = mid + 1
            else:
                hi = mid
        return 1 - (lo & 1)  # an even number of toggles so far: high

    def read(self, port, t):
        if port & 0xC002 == 0xC000:
            if self.reg == 14:  # only bit 7 is the line: the rest is noise
                return self.level(t) << 7 | self.rng.getrandbits(7)
            return self.regs.get(self.reg, 0xFF)
        return self.rng.getrandbits(8)

    def write(self, port, value):
        if port & 0xC002 == 0xC000:
            self.reg = value
            # Every driver call selects its register first: time and the
            # waveform start again at the pin write that follows.
            self.t0 = None
        elif port & 0xC002 == 0x8000:
            self.regs[self.reg] = value


DEVICES = {"divmmc": DivmmcDevice, "next": NextDevice, "ay": AyDevice}


def base_sources(base, tmp):
    """The src/ directory of a Git revision or of a directory."""
    if (Path(base) / "src").is_dir():
        return Path(base) / "src"

    def git(*arguments):
        return subprocess.run(["git", "-C", str(ROOT), *arguments], check=True, capture_output=True).stdout

    target = Path(tmp) / "revision"
    target.mkdir()
    for name in git("ls-tree", "-r", "--name-only", base, "src").decode().split():
        (target / Path(name).name).write_bytes(git("show", f"{base}:{name}"))
    return target


def with_bit_delay(src, tmp, tag, delay):
    if delay is None:
        return src
    copy = Path(tmp) / tag
    shutil.copytree(src, copy)
    core = copy / "ay_core.inc"
    text, count = re.subn(r"(UART_BIT_DELAY\s+EQU\s+)\d+", rf"\g<1>{delay}", core.read_text())
    if count != 1:
        raise SystemExit(f"{core}: UART_BIT_DELAY EQU not found once")
    core.write_text(text)
    return copy


def run(d, device, backend, routine, seed, regs, state, memory):
    m = d.m
    device.begin(seed)
    for name, value in zip(STATE[backend], state):
        if value is not None:
            m.memory[d.symbols[name]] = value
    m.memory[BUFFER:BUFFER + len(memory)] = memory
    m.memory[STACK - 96:STACK] = bytes([FILL]) * 96
    m.iff1 = m.iff2 = regs.pop("iff")
    m.alt_af = m.alt_bc = m.alt_de = m.alt_hl = 0x5A5A
    took = d.call(routine, **regs)
    result = {"cf": d.carry, "iff": bool(m.iff1), "sp": m.sp, "ix,iy": (m.ix, m.iy),
              "state": [m.memory[d.symbols[name]] for name in STATE[backend]],
              "buffer": bytes(m.memory[BUFFER:BUFFER + 600])}
    for name in SPEC[backend][routine]:
        if name == "A?":
            result[name] = m.a if d.carry else None
        elif name == "f":
            result[name] = m.f & 0xD7  # the documented flags
        else:
            result[name] = getattr(m, name)
    stack = bytes(m.memory[STACK - 96:STACK])
    depth = 96 - next((i for i, byte in enumerate(stack) if byte != FILL), 96)
    return result, list(device.trace), took, depth


def compare(base, backend, cases, seed, delay):
    rng = random.Random(seed)
    timed = backend == "ay"
    with tempfile.TemporaryDirectory() as tmp:
        drivers = []
        for tag, src in (("base", base_sources(base, tmp)), ("tree", ROOT / "src")):
            src = with_bit_delay(src, tmp, tag, delay if timed else None)
            device = DEVICES[backend]()
            if timed:
                device.bit = 26 * (delay or 11) + 84
            drivers.append((Quiet([(src / part).as_posix() for part in PARTS[backend]], device), device))
    routines = [r for r in SPEC[backend] if all(r in d.symbols for d, _ in drivers)]
    missing = [r for r in SPEC[backend] if r not in routines]
    weights = [WEIGHT.get(r, 1) for r in routines]
    took_delta, depth_delta, peak, bad = {}, {}, Counter(), 0
    for case in range(cases):
        routine = rng.choices(routines, weights)[0]
        device_seed = rng.getrandbits(32)
        regs = {"a": rng.getrandbits(8), "bc": rng.getrandbits(16), "de": rng.getrandbits(16),
                "hl": rng.getrandbits(16), "ix": rng.getrandbits(16), "iy": rng.getrandbits(16),
                "f": rng.getrandbits(8), "iff": rng.random() < 0.5}
        memory = bytes(rng.getrandbits(8) or 1 for _ in range(600))
        if routine == "uart_read_block":
            regs["hl"] = BUFFER
        if routine in ("uart_write_string", "uart_write_block", "uart_write_block_fast"):
            regs["hl"] = BUFFER
            regs["bc"] = length = rng.choice([0, 1, 2, 5, 40, 300])
            memory = memory[:length] + b"\0" + memory[length + 1:]
        if routine == "uart_set_baud":  # a table of 14-bit divisors, as the port takes
            regs["hl"] = BUFFER
            memory = bytes(byte & 0x3F if i & 1 else byte for i, byte in enumerate(memory))
        if routine == "uart_flush_budget":
            regs["de"] = rng.choice([0, 1, 2, 100, 2047, 2048, 2049, 4096, 9000, 65535])
        state = [rng.choice([None, 0, 0, 0, 0, 1, 1, 2, 0x40, 0x44, 0x80, rng.getrandbits(8)])
                 for _ in STATE[backend]]
        if timed:  # uart_second_avail only ever holds 0 or 1
            state[0] = rng.choice([None, 0, 0, 1])
        (r0, t0, k0, s0), (r1, t1, k1, s1) = [
            run(d, device, backend, routine, device_seed, dict(regs), state, memory)
            for d, device in drivers]
        if not timed:
            t0, t1 = [e[:3] for e in t0], [e[:3] for e in t1]
        problems = []
        if r0 != r1:
            problems.append("results: " + str({k: (r0[k], r1[k]) for k in r0 if r0[k] != r1[k] and k != "buffer"}
                                               or "buffer contents"))
        if t0 != t1:
            at = next((i for i, (a, b) in enumerate(zip(t0, t1)) if a != b), min(len(t0), len(t1)))
            problems.append(f"port access {at} of {len(t0)}/{len(t1)}: "
                            f"{t0[max(0, at - 2):at + 3]} against {t1[max(0, at - 2):at + 3]}")
        if problems:
            bad += 1
            if bad <= 6:
                print(f"DIFFERENT case {case} {routine} seed={device_seed} regs={regs} state={state}")
                for problem in problems:
                    print("   ", problem[:900])
        took_delta.setdefault(routine, Counter())[k1 - k0] += 1
        depth_delta.setdefault(routine, Counter())[s1 - s0] += 1
        peak[routine] = max(peak[routine], s1)
    print(f"{backend}: {cases} cases, {bad} different" + (f", bit delay {delay}" if timed and delay else "")
          + (f"; not in both: {', '.join(missing)}" if missing else ""))
    for routine in routines:
        if routine in took_delta:
            dt, ds = took_delta[routine], depth_delta[routine]
            print(f"  {routine:24s} n={sum(dt.values()):5d}  T-states {min(dt):+d}..{max(dt):+d} "
                  f"(most {dt.most_common(1)[0][0]:+d})  stack {min(ds):+d}..{max(ds):+d}, peak {peak[routine]}")
    return bad


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("base", help="Git revision, or a directory holding src/")
    parser.add_argument("backend", choices=[*PARTS, "all"])
    parser.add_argument("--cases", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--bitdelay", type=int, help="UART_BIT_DELAY for both AY trees")
    options = parser.parse_args()
    backends = list(PARTS) if options.backend == "all" else [options.backend]
    bad = sum(compare(options.base, backend, options.cases, options.seed, options.bitdelay)
              for backend in backends)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
