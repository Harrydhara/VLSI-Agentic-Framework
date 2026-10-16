"""Development checks: the only feedback the agent is allowed to see.

Each attempt's code is split into the design and the agent's own testbench. The design
is compiled on its own, then compiled with the testbench and simulated, then
synthesized. Every problem found becomes a short piece of text for the model. The
hidden reference test is never part of this; see evaluate.py.

The checks pass only when the design compiles, the testbench exists and reports
`RESULT: p/t passed` with p == t > 0, and synthesis succeeds without latches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from vlsi_agent import eda
from vlsi_agent.config import ToolsConfig

DESIGN_FILE = "design.v"
TESTBENCH_FILE = "tb.v"
TESTBENCH_TOP = "tb"

_MODULE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b.*?\bendmodule\b", re.DOTALL)
_SIM_NOISE = re.compile(r"\$finish called at|^VCD info:|^VCD warning:")


@dataclass(frozen=True)
class Sources:
    design: str  # every module except the testbench, in their original order
    testbench: str | None
    module_names: tuple[str, ...]


def split_modules(code: str) -> list[tuple[str, str]]:
    """Return (name, source) for each complete `module ... endmodule` in the code.

    If a module is defined more than once (models sometimes rewrite a module several
    times in one reply), only its last definition is kept, at that definition's place.
    """
    found = [(m.group(1), m.group(0)) for m in _MODULE.finditer(code)]
    last = {name: i for i, (name, _) in enumerate(found)}
    return [module for i, module in enumerate(found) if last[module[0]] == i]


def separate(code: str, top: str) -> Sources:
    """Split code into design and testbench.

    The testbench is the module named tb; failing that, a module other than the top
    that calls $finish. Everything else (top and helper modules) is the design.
    Compiler directives outside modules (`timescale, `define) go at the top of both.
    """
    modules = split_modules(code)
    outside = _MODULE.sub("", code)
    directives = "".join(line.strip() + "\n" for line in outside.splitlines() if line.strip().startswith("`"))
    tb_index = next((i for i, (name, _) in enumerate(modules) if name == TESTBENCH_TOP), None)
    if tb_index is None:
        tb_index = next((i for i, (name, src) in enumerate(modules) if name != top and "$finish" in src), None)
    design = "\n\n".join(src for i, (_, src) in enumerate(modules) if i != tb_index)
    testbench = modules[tb_index][1] if tb_index is not None else None
    return Sources(directives + design + "\n" if design else "",
                   directives + testbench + "\n" if testbench else None,
                   tuple(name for name, _ in modules))


@dataclass(frozen=True)
class DevReport:
    passed: bool
    feedback: str  # what the model is told; empty when everything passed
    has_top: bool
    has_testbench: bool
    compile: eda.CompileResult | None
    tb_compile: eda.CompileResult | None
    sim: eda.SimResult | None
    synth: eda.SynthResult | None


def _excerpt(text: str, max_lines: int = 25) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip() and not _SIM_NOISE.search(line)]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines)"]
    return "\n".join(lines)


def _diagnostics(result: eda.CompileResult) -> str:
    shown = [d for d in result.diagnostics if d.severity in ("error", "warning")]
    if not shown:
        return _excerpt(result.run.output)
    return "\n".join(f"{d.file}:{d.line}: {d.severity}: {d.message}" for d in shown[:20])


def _cap(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n... (feedback cut to {max_chars} characters)"


def run_dev_checks(tools: ToolsConfig, workdir: Path, sources: Sources, top: str,
                   max_chars: int = 3000) -> DevReport:
    """Write design.v / tb.v into workdir, run the checks, and describe any problems."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / DESIGN_FILE).write_text(sources.design)
    if sources.testbench:
        (workdir / TESTBENCH_FILE).write_text(sources.testbench)
    problems: list[str] = []
    comp = tb_comp = sim = synth = None

    has_top = top in sources.module_names
    if not has_top:
        found = ", ".join(sources.module_names) or "none"
        problems.append(f"The design has no module named {top} (modules found: {found}). "
                        "Use exactly the module header you were given.")
    else:
        comp = eda.compile_verilog(tools, workdir, [DESIGN_FILE], top=top)
        (workdir / "compile.log").write_text(comp.run.output)
        if not comp.ok:
            problems.append(f"The design does not compile:\n{_diagnostics(comp)}")

    if comp is not None and comp.ok:
        if not sources.testbench:
            problems.append("No testbench was found. Include a module named tb that checks the design "
                            'and ends with $display("RESULT: %0d/%0d passed", passed, total); $finish;')
        else:
            tb_comp = eda.compile_verilog(tools, workdir, [DESIGN_FILE, TESTBENCH_FILE], top=TESTBENCH_TOP)
            (workdir / "tb_compile.log").write_text(tb_comp.run.output)
            if not tb_comp.ok:
                problems.append(f"The design compiles, but the design plus testbench does not:\n"
                                f"{_diagnostics(tb_comp)}")
            else:
                sim = eda.simulate(tools, workdir)
                (workdir / "sim.log").write_text(sim.run.output)
                if sim.run.timed_out:
                    problems.append(f"The simulation did not finish within {tools.sim_timeout_s:g} s. Check that the "
                                    "testbench calls $finish and that the design has no infinite loop.\n"
                                    f"Output before it was stopped:\n{_excerpt(sim.run.output, 10)}")
                elif not sim.completed:
                    problems.append(f"The simulator stopped with exit code {sim.run.returncode}:\n"
                                    f"{_excerpt(sim.run.output)}")
                elif sim.total is None:
                    problems.append('The testbench printed no "RESULT: <passed>/<total> passed" line.\n'
                                    f"Simulation output:\n{_excerpt(sim.run.output)}")
                elif not sim.all_passed:
                    problems.append(f"The testbench reported failures ({sim.passed}/{sim.total} checks passed):\n"
                                    f"{_excerpt(sim.run.output)}")

        synth = eda.synthesize(tools, workdir, [DESIGN_FILE], top)
        if not synth.ok:
            problems.append(f"Synthesis with Yosys failed:\n{_excerpt(synth.run.output, 15)}")
        elif synth.latches:
            problems.append(f"Synthesis inferred {synth.latches} latch(es). In combinational logic, assign every "
                            "output in every branch (add a default or else).")

    passed = not problems
    feedback = _cap("\n\n".join(f"- {p}" for p in problems), max_chars) if problems else ""
    return DevReport(passed, feedback, has_top, sources.testbench is not None, comp, tb_comp, sim, synth)
