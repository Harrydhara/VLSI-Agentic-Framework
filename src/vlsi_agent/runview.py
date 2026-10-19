"""Read a finished run folder back for display: each attempt's files, checks and scores.

Plain Python with no Gradio, so the dashboard's results view can be tested directly.
Works for any run folder made by run_agent: from the dashboard (runs/ui), the CLI
(runs/agent) or a benchmark (runs/bench/.../seed<k>).
"""

from __future__ import annotations

import difflib
import html
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from vlsi_agent.config import PROJECT_ROOT

RUNS_ROOT = PROJECT_ROOT / "runs"


@dataclass(frozen=True)
class AttemptView:
    number: int
    folder: Path
    design: str | None  # None when the reply had no Verilog
    testbench: str | None
    reply: str
    feedback: str  # what the model was told about this attempt
    checks: dict
    dev_passed: bool
    reference: dict | None  # {"outcome", "passed", "total"}; None if this attempt was not scored
    reference_log: str  # the hidden test's output for this attempt ("" if not scored)


@dataclass(frozen=True)
class RunView:
    folder: Path
    history: dict
    attempts: list[AttemptView]

    def attempt(self, number: int) -> AttemptView:
        return next(a for a in self.attempts if a.number == number)


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


# Where run folders live: runs/<ui|agent|...>/<run>/ and runs/bench/<benchmark>/runs/<circuit>/<strategy>/<seed>/.
# Looking only there is ~10x faster than walking every file, which matters on /mnt/c under WSL.
_RUN_PATTERNS = ("*/*/history.json", "bench/*/runs/*/*/*/history.json")


def list_runs(root: Path = RUNS_ROOT) -> list[str]:
    """Run folders under root (those with a history.json), newest first, relative to root."""
    found = [p.parent for pattern in _RUN_PATTERNS for p in Path(root).glob(pattern)]
    found.sort(key=lambda p: (p / "history.json").stat().st_mtime, reverse=True)
    return [p.relative_to(root).as_posix() for p in found]


def load_run(folder: Path) -> RunView:
    folder = Path(folder)
    history = json.loads((folder / "history.json").read_text(encoding="utf-8"))
    attempts = []
    for a in history["attempts"]:
        work = folder / a["folder"]
        attempts.append(AttemptView(
            number=a["number"],
            folder=work,
            design=_read(work / "design.v"),
            testbench=_read(work / "tb.v"),
            reply=_read(work / "response.md") or "",
            feedback=a["feedback"],
            checks=a["checks"],
            dev_passed=a["dev_passed"],
            reference=a["reference"],
            reference_log=_read(folder / "reference" / a["folder"] / "reference.log") or "",
        ))
    return RunView(folder, history, attempts)


# --------------------------------------------------------------------------- summaries


def _hidden(reference: dict | None) -> str:
    if reference is None:
        return "not scored"
    counts = f" {reference['passed']}/{reference['total']}" if reference.get("total") else ""
    return reference["outcome"].upper() + counts


def check_columns(a: AttemptView) -> list[str]:
    """[own checks, compile, testbench, synthesis, hidden test] for one attempt."""
    c = a.checks
    if not c.get("code_found"):
        return ["FAIL", "no Verilog in reply", "—", "—", _hidden(a.reference)]
    compile_ = "ok" if c.get("compiled") else ("top module missing" if not c.get("has_top") else "failed")
    if not c.get("compiled"):
        testbench = "—"
    elif not c.get("has_testbench"):
        testbench = "none"
    elif c.get("sim_timed_out"):
        testbench = "timed out"
    elif "tb_total" in c:
        testbench = f"{c['tb_passed']}/{c['tb_total']}" if c["tb_total"] is not None else "no RESULT line"
    else:
        testbench = "did not compile"
    if "synth_ok" not in c:
        synthesis = "—"
    elif c["synth_ok"]:
        synthesis = f"{c['cells']} cells" + (f", {c['latches']} latches" if c.get("latches") else "")
    else:
        synthesis = "failed"
    return ["PASS" if a.dev_passed else "FAIL", compile_, testbench, synthesis, _hidden(a.reference)]


CHECK_HEADERS = ["Attempt", "Own checks", "Compile", "Testbench", "Synthesis", "Hidden test"]


def check_table(run: RunView) -> list[list]:
    return [[a.number, *check_columns(a)] for a in run.attempts]


def run_markdown(run: RunView) -> str:
    h = run.history
    settings = h["settings"]
    try:
        where = run.folder.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        where = str(run.folder)
    final = h.get("reference")
    verdict = (f"**Hidden reference test: {final['outcome'].upper()}** ({final['detail']})" if final
               else "**No design was produced.**")
    strategy = Path(settings["prompts"]).name
    workflow = settings.get("workflow", "single")
    return (f"{verdict}\n\n`{h['circuit']}` ({h['split']}) · strategy `{strategy}`"
            + (" (testbench written first)" if workflow == "spec_first" else "")
            + f" · {len(run.attempts)} of {settings['max_attempts']} attempt(s) · "
            f"stopped because: {h['status'].replace('_', ' ')} · {h['tokens']['total']:,} tokens · "
            f"{h['duration_s']:.1f} s\n\nRun files: `{where}`")


def mismatch_lines(a: AttemptView, limit: int = 12) -> str:
    """The hidden test's verdict for one attempt, with its first mismatch lines."""
    if a.reference is None:
        return "This attempt was not scored by the hidden test."
    head = f"**{_hidden(a.reference)}**"
    lines = [l for l in a.reference_log.splitlines() if l.startswith("MISMATCH")]
    if a.reference["outcome"] == "compile_error":
        errors = [l for l in a.reference_log.splitlines() if "error" in l][:limit]
        return head + "\n\nThe design did not compile together with the reference test:\n\n```\n" + "\n".join(errors) + "\n```"
    if not lines:
        return head + "\n\nNo mismatches."
    return f"{head}\n\nFirst mismatches:\n\n```\n" + "\n".join(lines[:limit]) + "\n```"


# --------------------------------------------------------------------------- diffs


def diff_html(old: str | None, new: str | None, old_name: str, new_name: str) -> str:
    """A unified diff as HTML with added and removed lines marked."""
    if new is None:
        return '<p class="diff-note">This attempt has no file to compare.</p>'
    if old is None:
        return f'<p class="diff-note">Nothing to compare: {html.escape(old_name)} has no file.</p>'
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), old_name, new_name, lineterm="", n=3))
    if not lines:
        return f'<p class="diff-note">{html.escape(new_name)} is identical to {html.escape(old_name)}.</p>'
    out = []
    for line in lines:
        cls = ("diff-meta" if line.startswith(("---", "+++", "@@")) else
               "diff-add" if line.startswith("+") else "diff-del" if line.startswith("-") else "diff-same")
        out.append(f'<div class="{cls}">{html.escape(line) or " "}</div>')
    return '<div class="diff">' + "".join(out) + "</div>"


def attempt_diffs(run: RunView, number: int) -> str:
    """Design and testbench changes from the previous attempt to this one."""
    if number == run.attempts[0].number:
        return '<p class="diff-note">This is the first attempt, so there is nothing to compare yet.</p>'
    index = [a.number for a in run.attempts].index(number)
    before, now = run.attempts[index - 1], run.attempts[index]
    return ("<h4>Design</h4>" + diff_html(before.design, now.design, f"attempt {before.number}", f"attempt {now.number}")
            + "<h4>Testbench</h4>"
            + diff_html(before.testbench, now.testbench, f"attempt {before.number}", f"attempt {now.number}"))


# --------------------------------------------------------------------------- export


def _markdown_table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return "\n".join(lines + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows])


def run_summary_markdown(run: RunView, title: str | None = None) -> str:
    """A readable account of a run for exports: verdict and settings, each attempt's checks,
    the hidden test's mismatches, the feedback the model got, and the final design."""
    parts = [f"# Run `{title or run.folder.name}`", run_markdown(run), "## Attempts",
             _markdown_table(CHECK_HEADERS, check_table(run))]
    for a in run.attempts:
        parts += [f"## Attempt {a.number}", "**Hidden test:** " + mismatch_lines(a)]
        if a.feedback:
            parts += ["**Feedback sent to the model:**", "```\n" + a.feedback.strip() + "\n```"]
    final = run.history.get("final_attempt")
    design = run.attempt(final).design if final is not None else None
    parts += [f"## Final design (attempt {final})", f"```verilog\n{design.strip()}\n```"] if design else [
        "## Final design", "No design was produced."]
    files = ["`history.json`: settings, every model call, every attempt, the scores",
             "`events.jsonl`: what happened, in order", "`attempt_<n>/`: the reply, design, testbench and tool logs",
             "`reference/`: the hidden test's output for each scored attempt"]
    if (run.folder / "prompts").is_dir():
        files.append("`prompts/`: the prompt templates this run used")
    return "\n\n".join(parts + ["## Files", "\n".join(f"- {f}" for f in files)]) + "\n"


def export_run_zip(run: RunView, out_dir: Path, name: str | None = None) -> Path:
    """The whole run folder plus summary.md, as <out_dir>/<name>.zip."""
    name = name or run.folder.name
    path = Path(out_dir) / f"{name}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/summary.md", run_summary_markdown(run, name))
        for f in sorted(run.folder.rglob("*")):
            if f.is_file():
                z.write(f, f"{name}/{f.relative_to(run.folder).as_posix()}")
    return path
