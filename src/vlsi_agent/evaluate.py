"""Score a design against a circuit's hidden reference test.

The reference testbench prints `RESULT __RESULT_TOKEN__: p/t passed`. Before each run
the placeholder is replaced with a fresh random token, and only a line carrying that
token counts. A design that contains test code of its own (for example
`$display("RESULT: 1/1 passed"); $finish;`) therefore cannot fake a pass: ending the
simulation early just means the real result line never appears.

This guards against accidental cheating by generated code. It is not a sandbox: a
design written on purpose to cheat could still reach into the testbench through
hierarchical references or read files. Never show reference-test output to the agent.
"""

from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from vlsi_agent import eda
from vlsi_agent.circuits import Circuit, CircuitError
from vlsi_agent.config import ToolsConfig

DESIGN_NAME = "design.v"
REFERENCE_TB_NAME = "reference_tb.v"
REFERENCE_TOP = "ref_tb"
TOKEN_PLACEHOLDER = "__RESULT_TOKEN__"
LOG_NAME = "reference.log"  # the compiler or simulator output, e.g. the MISMATCH lines


@dataclass(frozen=True)
class ReferenceResult:
    outcome: str  # one of circuits.OUTCOMES
    passed: int | None
    total: int | None
    detail: str  # one-line human summary
    compile: eda.CompileResult
    sim: eda.SimResult | None  # None when compilation failed


def reference_test(tools: ToolsConfig, circuit: Circuit, design_file: Path, workdir: Path,
                   waveform: bool = False, vcd_window: tuple[int, int] | None = None) -> ReferenceResult:
    """Copy the design and the reference test into workdir, compile, simulate, classify.
    waveform writes reference.vcd; vcd_window=(from_ns, until_ns) limits it to that window."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(design_file, workdir / DESIGN_NAME)

    testbench = circuit.reference_tb.read_text()
    if f"RESULT {TOKEN_PLACEHOLDER}:" not in testbench:  # a mention in a comment is not enough
        raise CircuitError(f'{circuit.reference_tb} must print its result as "RESULT {TOKEN_PLACEHOLDER}: ..."')
    token = secrets.token_hex(8)
    (workdir / REFERENCE_TB_NAME).write_text(testbench.replace(TOKEN_PLACEHOLDER, token))

    comp = eda.compile_verilog(tools, workdir, [DESIGN_NAME, REFERENCE_TB_NAME], top=REFERENCE_TOP)
    if not comp.ok:
        (workdir / LOG_NAME).write_text(comp.run.output)
        first = comp.errors[0] if comp.errors else None
        detail = f"{first.file}:{first.line}: {first.message}" if first else "compilation failed"
        return ReferenceResult("compile_error", None, None, detail, comp, None)

    plusargs = ["+vcd"] if waveform or vcd_window else []
    if vcd_window:
        plusargs += [f"+vcd_from={int(vcd_window[0])}", f"+vcd_until={int(vcd_window[1])}"]
    sim = eda.simulate(tools, workdir, plusargs=plusargs, result_token=token)
    (workdir / LOG_NAME).write_text(sim.run.output)
    if sim.run.timed_out:
        outcome, detail = "timeout", f"simulation still running after {tools.sim_timeout_s:g} s"
    elif not sim.completed:
        outcome, detail = "error", f"simulator exited with code {sim.run.returncode}"
    elif sim.total is None:
        outcome, detail = "error", "reference test printed no result (did the design end the simulation early?)"
    elif sim.all_passed:
        outcome, detail = "pass", f"{sim.passed}/{sim.total} checks passed"
    else:
        outcome, detail = "fail", f"{sim.passed}/{sim.total} checks passed"
    return ReferenceResult(outcome, sim.passed, sim.total, detail, comp, sim)
