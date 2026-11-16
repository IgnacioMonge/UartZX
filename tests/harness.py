"""Assemble the driver sources and run them on the `z80` package emulator.

Needs sjasmplus and z88dk-z80asm on PATH, and `pip install z80==1.2.0`.
"""

import re
import subprocess
import tempfile
from pathlib import Path

import z80

ROOT = Path(__file__).resolve().parents[1]
ORG = 0x8100  # the AY driver must sit above #8000
STOP = 0x0000  # return address of every call
STACK = 0xFF00
TICKS = 100_000  # the z80 package's own frame: its frame_tick counter wraps here


def source(name):
    """Path of a src/ file; an absolute path is taken as it is."""
    path = Path(name)
    return (path if path.is_absolute() else ROOT / "src" / name).as_posix()


def assemble(tmp, name, lines, *cmd):
    (tmp / f"{name}.asm").write_text("\n".join(lines) + "\n")
    done = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
    # Both assemblers are silent on a clean run; any output is a warning.
    if done.returncode or done.stdout or done.stderr:
        raise AssertionError(f"{cmd[0]} {name}:\n{done.stdout}{done.stderr}")
    return (tmp / f"{name}.bin").read_bytes()


def sjasmplus(tmp, name, includes):
    lines = [f"    org {ORG}"] + [f'    include "{path}"' for path in includes]
    return assemble(
        tmp, name, lines,
        "sjasmplus", "--nologo", "--msg=war", f"--raw={name}.bin", f"--sym={name}.sym", f"{name}.asm",
    )  # fmt: skip


def z80asm(tmp, name, includes):
    lines = [f"    ORG {ORG}"] + [f'    INCLUDE "{path}"' for path in includes]
    return assemble(
        tmp, name, lines, "z88dk-z80asm", "-b", f"-o={name}.bin", f"{name}.asm"
    )


class Driver:
    """The given src/ files assembled at ORG, with a device on the I/O ports.

    `device` gets `clock` (a callable returning elapsed T-states) and must
    offer inp(port) and out(port, value). HALT waits for the next frame
    interrupt, served by a bare `EI : RET` handler at 0x0038. The interrupt
    comes every FRAME T-states of emulated time, counted from the creation of
    the Driver across calls, and its acceptance cycle counts as time.

    `linked` = (blocks, symbols) loads code another linker placed, as
    {address: bytes} and {name: address}, instead of assembling `parts`.
    """

    LIMIT = 8_000_000  # T-states before a call counts as hung
    FRAME = 69888  # T-states between frame interrupts, as on a 48K

    def __init__(self, parts, device, linked=None):
        if linked:
            blocks, self.symbols = linked
        else:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                image = sjasmplus(tmp, "driver", [source(part) for part in parts])
                symbols = (tmp / "driver.sym").read_text()
            blocks = {ORG: image}
            self.symbols = {
                name: int(value, 16)
                for name, value in re.findall(
                    r"^(\S+): EQU 0x([0-9A-Fa-f]+)$", symbols, re.M
                )
            }
        self.m = z80.Z80Machine()
        for address, data in blocks.items():
            self.m.set_memory_block(address, data)
        self.m.set_memory_block(0x0038, bytes([0xFB, 0xC9]))
        self.elapsed = 0  # T-states of finished calls
        self.took = 0  # T-states of the running call, before the current slice
        self.start = self.m.frame_tick  # where the current slice began
        self.frames = 0  # frame interrupts taken
        self.io = 0  # port accesses, for "touches no port" checks
        self.device = device
        device.clock = self.clock
        self.m.set_input_callback(self._inp)
        self.m.set_output_callback(self._out)
        self.m.set_breakpoint(STOP)

    def clock(self):
        return self.elapsed + self.took + (self.m.frame_tick - self.start) % TICKS

    def _slice(self, run):
        """Run the machine and add the T-states it took to the call."""
        self.start = self.m.frame_tick
        run()
        self.took += (self.m.frame_tick - self.start) % TICKS
        self.start = self.m.frame_tick

    def _inp(self, port):
        self.io += 1
        return self.device.inp(port)

    def _out(self, port, value):
        self.io += 1
        self.device.out(port, value)

    def byte(self, name):
        return self.m.memory[self.symbols[name]]

    def word(self, name):
        address = self.symbols[name]
        return int.from_bytes(self.m.memory[address : address + 2], "little")

    def call(self, name, **registers):
        """Run a routine to its RET; returns the T-states it took."""
        m = self.m
        for register, value in registers.items():
            setattr(m, register, value)
        m.sp = STACK
        m.memory[STACK : STACK + 2] = STOP.to_bytes(2, "little")
        m.pc = self.symbols[name]
        self.took = 0
        while m.pc != STOP:
            if self.took > self.LIMIT:
                raise AssertionError(f"{name}: did not return, PC={m.pc:04x}")
            # Run to the next frame boundary. The package's own frame events,
            # every 100000 T-states, only stop run() early.
            m.ticks_to_stop = self.FRAME - (self.elapsed + self.took) % self.FRAME
            self._slice(m.run)
            if m.ticks_to_stop == 0 and m.pc != STOP and m.iff1:
                self.frames += 1
                self._slice(m.on_handle_active_int)
            elif m.halted and not m.iff1:
                raise AssertionError(f"{name}: HALT with interrupts disabled")
        took, self.took = self.took, 0
        self.elapsed += took
        return took

    @property
    def carry(self):
        return bool(self.m.f & 1)
