"""Run Icarus Verilog (iverilog + vvp) and Yosys, returning structured results.

Every tool runs inside a caller-supplied work directory, with file names relative to
it, under a timeout. On timeout the whole process group is killed, because iverilog
and yosys start child processes of their own. Captured output is capped (start and
end are kept) so a runaway simulation cannot fill memory.

Problems with the *design* (syntax errors, failed tests, hangs) come back as results.
Problems with the *environment* (a tool binary missing) raise EDAToolError, so a bad
config cannot masquerade as a bad design.

Testbench convention: a testbench reports its score by printing one line

    RESULT: <passed>/<total> passed

before calling $finish. The vvp exit code is not enough on its own: it is 0 even when
a testbench forgets $finish or never checks anything.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence

from vlsi_agent.config import ToolsConfig

VERILOG_STANDARD = "2012"  # iverilog -g2012; yosys reads with -sv to match
COMPILED_NAME = "sim.vvp"
SYNTH_SCRIPT_NAME = "synth.ys"
SYNTH_LOG_NAME = "synth.log"
SYNTH_STAT_NAME = "synth_stat.json"

_SAFE_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]*$")
_SAFE_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_RESULT_LINE = re.compile(r"^RESULT: (\d+)/(\d+) passed\s*$", re.MULTILINE)
_DIAGNOSTIC = re.compile(
    r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):\s*(?:(?P<severity>error|warning|sorry):\s*)?(?P<message>.+)$"
)


class EDAToolError(RuntimeError):
    """A tool could not be started at all (missing or not executable)."""


@dataclass(frozen=True)
class ToolRun:
    """One external tool invocation."""

    cmd: tuple[str, ...]
    returncode: int | None  # None when the tool was killed for running too long
    output: str  # stdout and stderr merged; middle omitted if over the cap
    duration_s: float
    timed_out: bool
    truncated: bool


@dataclass(frozen=True)
class Diagnostic:
    file: str
    line: int
    severity: str  # "error", "warning" or "note"
    message: str


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    diagnostics: tuple[Diagnostic, ...]
    run: ToolRun

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "error")


@dataclass(frozen=True)
class SimResult:
    completed: bool  # vvp exited with code 0 before the timeout
    passed: int | None  # from the RESULT line; None if the testbench printed none
    total: int | None
    vcd_files: tuple[str, ...]
    run: ToolRun

    @property
    def all_passed(self) -> bool:
        return self.completed and bool(self.total) and self.passed == self.total


@dataclass(frozen=True)
class SynthResult:
    ok: bool
    cells: int | None  # None when synthesis failed
    cells_by_type: dict[str, int]
    latches: int  # latches usually mean an incomplete if/case in combinational logic
    warnings: tuple[str, ...]
    run: ToolRun


# --------------------------------------------------------------------------- running


class _CappedOutput:
    """Collects a byte stream, keeping only the first and last `limit // 2` bytes."""

    def __init__(self, limit: int):
        self.half = max(limit // 2, 1)
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self._lock = threading.Lock()  # text() may run while a stray reader is still feeding

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            self.total += len(chunk)
            room = self.half - len(self.head)
            if room > 0:
                self.head += chunk[:room]
                chunk = chunk[room:]
            if chunk:
                self.tail += chunk
                if len(self.tail) > self.half:
                    del self.tail[: len(self.tail) - self.half]

    def drain(self, stream: BinaryIO) -> None:
        for chunk in iter(lambda: stream.read1(65536), b""):
            self.feed(chunk)

    def text(self) -> tuple[str, bool]:
        with self._lock:
            head, tail, total = bytes(self.head), bytes(self.tail), self.total
        omitted = total - len(head) - len(tail)
        if omitted <= 0:
            return (head + tail).decode(errors="replace"), False
        middle = f"\n... [{omitted} bytes of output omitted] ...\n"
        return head.decode(errors="replace") + middle + tail.decode(errors="replace"), True


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_tool(cmd: Sequence[str], cwd: Path, timeout_s: float, max_output_bytes: int) -> ToolRun:
    """Run a command with no stdin, a timeout, and capped merged output."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(cmd),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group, so a timeout kills the children too
        )
    except OSError as e:
        raise EDAToolError(f"cannot start {cmd[0]}: {e}") from e

    capture = _CappedOutput(max_output_bytes)
    reader = threading.Thread(target=capture.drain, args=(proc.stdout,), daemon=True)
    reader.start()
    timed_out = False
    try:
        returncode: int | None = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = None
    _kill_group(proc)  # also reaps any children left behind after a normal exit
    proc.wait()
    reader.join(timeout=5)
    stray = reader.is_alive()
    if not stray:
        # Only close when the reader is done: close() waits for a blocked read to return,
        # which would hang until a stray process holding the pipe exits on its own.
        proc.stdout.close()

    output, truncated = capture.text()
    if timed_out:
        output += f"\n[killed: still running after {timeout_s:g} s]"
    if stray:
        output += "\n[warning: a process outside the tool's process group kept its output open]"
    return ToolRun(tuple(cmd), returncode, output, time.monotonic() - start, timed_out, truncated)


def _check_workdir(workdir: Path) -> Path:
    workdir = Path(workdir)
    if not workdir.is_dir():
        raise NotADirectoryError(f"work directory does not exist: {workdir}")
    return workdir


def _check_files(workdir: Path, names: Sequence[str]) -> None:
    if not names:
        raise ValueError("no source files given")
    for name in names:
        if not _SAFE_FILE.match(name) or ".." in Path(name).parts:
            raise ValueError(f"unsafe file name {name!r}: use plain relative names like 'design.v'")
        if not (workdir / name).is_file():
            raise FileNotFoundError(f"{name} not found in {workdir}")


def _check_module(name: str) -> None:
    if not _SAFE_MODULE.match(name):
        raise ValueError(f"invalid module name {name!r}")


def _max_bytes(tools: ToolsConfig) -> int:
    return tools.max_log_kb * 1024


# --------------------------------------------------------------------------- parsing


def parse_diagnostics(output: str) -> tuple[Diagnostic, ...]:
    """Pull `file:line: [error|warning:] message` lines out of iverilog output."""
    found = []
    for line in output.splitlines():
        m = _DIAGNOSTIC.match(line.strip())
        if not m:
            continue
        severity = m["severity"]
        message = m["message"].strip()
        if severity == "sorry":  # iverilog's word for "unsupported construct"
            severity = "error"
        elif severity is None:
            severity = "error" if "error" in message.lower() else "note"
        found.append(Diagnostic(m["file"], int(m["line"]), severity, message))
    return tuple(found)


def parse_result_line(output: str) -> tuple[int | None, int | None]:
    """Return (passed, total) from the last RESULT line, or (None, None)."""
    matches = _RESULT_LINE.findall(output)
    if not matches:
        return None, None
    passed, total = matches[-1]
    return int(passed), int(total)


# --------------------------------------------------------------------------- tools


def compile_verilog(tools: ToolsConfig, workdir: Path, sources: Sequence[str],
                    top: str | None = None) -> CompileResult:
    """Compile sources with iverilog into sim.vvp inside workdir."""
    workdir = _check_workdir(workdir)
    _check_files(workdir, sources)
    if top is not None:
        _check_module(top)
    output = workdir / COMPILED_NAME
    output.unlink(missing_ok=True)  # a stale file must not look like a successful compile

    cmd = [tools.iverilog, f"-g{VERILOG_STANDARD}", "-Wall", "-Wno-timescale", "-o", COMPILED_NAME]
    if top is not None:
        cmd += ["-s", top]
    cmd += list(sources)
    run = run_tool(cmd, workdir, tools.compile_timeout_s, _max_bytes(tools))
    ok = run.returncode == 0 and output.is_file()
    return CompileResult(ok, parse_diagnostics(run.output), run)


def simulate(tools: ToolsConfig, workdir: Path, plusargs: Sequence[str] = ()) -> SimResult:
    """Run the compiled sim.vvp with vvp and read the testbench's RESULT line."""
    workdir = _check_workdir(workdir)
    if not (workdir / COMPILED_NAME).is_file():
        raise FileNotFoundError(f"{COMPILED_NAME} not found in {workdir}; compile first")
    for vcd in workdir.glob("*.vcd"):  # only report waveforms from this run
        vcd.unlink()

    cmd = [tools.vvp, "-n", COMPILED_NAME, *plusargs]  # -n: $stop ends the run instead of prompting
    run = run_tool(cmd, workdir, tools.sim_timeout_s, _max_bytes(tools))
    passed, total = parse_result_line(run.output)
    vcds = tuple(sorted(p.name for p in workdir.glob("*.vcd")))
    completed = run.returncode == 0 and not run.timed_out
    return SimResult(completed, passed, total, vcds, run)


def synthesize(tools: ToolsConfig, workdir: Path, sources: Sequence[str], top: str) -> SynthResult:
    """Synthesize with Yosys' generic `synth` flow and report cell counts and warnings."""
    workdir = _check_workdir(workdir)
    _check_files(workdir, sources)
    _check_module(top)
    stat_file = workdir / SYNTH_STAT_NAME
    stat_file.unlink(missing_ok=True)

    script = [f"read_verilog -sv {name}" for name in sources] + [
        f"hierarchy -check -top {top}",
        f"synth -top {top}",
        "check",
        f"tee -q -o {SYNTH_STAT_NAME} stat -json",
    ]
    (workdir / SYNTH_SCRIPT_NAME).write_text("\n".join(script) + "\n")
    # -q: console gets only warnings and errors; the full log goes to synth.log
    cmd = [tools.yosys, "-q", "-l", SYNTH_LOG_NAME, "-s", SYNTH_SCRIPT_NAME]
    run = run_tool(cmd, workdir, tools.synth_timeout_s, _max_bytes(tools))

    warnings = tuple(dict.fromkeys(  # unique, in order of first appearance
        line.strip() for line in run.output.splitlines() if line.startswith("Warning:")))
    if run.returncode != 0 or run.timed_out or not stat_file.is_file():
        return SynthResult(False, None, {}, 0, warnings, run)

    design = json.loads(stat_file.read_text())["design"]
    by_type = {str(k): int(v) for k, v in design.get("num_cells_by_type", {}).items()}
    latches = sum(n for cell, n in by_type.items() if "DLATCH" in cell.upper())
    return SynthResult(True, int(design["num_cells"]), by_type, latches, warnings, run)
