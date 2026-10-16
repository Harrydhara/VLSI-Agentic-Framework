"""The agent loop: generate a design, run development checks, revise from the feedback.

max_attempts = 1 is the one-shot baseline; more attempts turn on revision, with the
same model and the same prompts. The loop stops when the development checks pass,
when the attempts run out, or, before any model call, once the token budget is used
up (a single call can still overshoot the budget by its own size). Afterwards the
final design (from the last attempt that produced code) is scored with the circuit's
hidden reference test. The model never sees that test or its result, and the loop
never uses it to decide anything: scoring only starts once the loop has finished.

With score_every_attempt, every attempt's design is scored too (still after the
loop). Attempt 1 is then exactly what a one-shot run would have produced, so one run
gives both the one-shot and the iterative score, and shows when a revision fixed a
design or broke one.

Every run writes to its own folder:

    events.jsonl            one JSON event per line, written as things happen
    history.json            settings, every attempt, the scores
    attempt_<n>/            response.md (raw reply), design.v, tb.v, tool logs, feedback.txt
    reference/attempt_<n>/  the hidden reference test run on that attempt's design
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from vlsi_agent import __version__
from vlsi_agent.circuits import Circuit
from vlsi_agent.config import PROJECT_ROOT, Config
from vlsi_agent.devcheck import DESIGN_FILE, DevReport, run_dev_checks, separate
from vlsi_agent.evaluate import ReferenceResult, reference_test
from vlsi_agent.llm import LLM, Completion, LLMError, Message, extract_verilog
from vlsi_agent.prompts import load_prompts

STATUSES = ("dev_passed", "attempts_exhausted", "token_budget_exhausted", "llm_error")

NO_CODE_FEEDBACK = ("- No Verilog module was found in your reply. Reply with two ```verilog code blocks: "
                    "the design, then the testbench.")
TRUNCATED_FEEDBACK = ("- Your reply was cut off at the output token limit before it finished. "
                      "Keep the code complete and shorter.")


@dataclass
class Attempt:
    number: int
    workdir: Path
    completion: Completion
    code: str | None  # None when the reply contained no Verilog module
    report: DevReport | None  # None when there was no code to check
    feedback: str  # what the model was (or would have been) told about this attempt
    reference: ReferenceResult | None = None  # scored after the loop; never shown to the model

    @property
    def dev_passed(self) -> bool:
        return self.report is not None and self.report.passed


@dataclass
class RunResult:
    run_dir: Path
    circuit: str
    status: str  # one of STATUSES
    max_attempts: int
    attempts: list[Attempt] = field(default_factory=list)
    reference: ReferenceResult | None = None  # None if no attempt produced code
    final_attempt: int | None = None
    error: str | None = None
    duration_s: float = 0.0

    @property
    def prompt_tokens(self) -> int:
        return sum(a.completion.prompt_tokens for a in self.attempts)

    @property
    def output_tokens(self) -> int:
        return sum(a.completion.output_tokens for a in self.attempts)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


class EventLog:
    """Appends one JSON object per event to a file as the run goes, and keeps them in memory."""

    def __init__(self, path: Path, on_event: Callable[[dict], None] | None = None):
        self.path = path
        self.events: list[dict] = []
        self._start = time.monotonic()
        self._on_event = on_event

    def emit(self, kind: str, **data) -> None:
        event = {"t": round(time.monotonic() - self._start, 3), "event": kind, **data}
        self.events.append(event)
        with self.path.open("a") as f:
            f.write(json.dumps(event) + "\n")
        if self._on_event:
            self._on_event(event)


def _resolve(path: Path | str) -> Path:
    path = Path(path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _check_summary(report: DevReport | None) -> dict:
    if report is None:
        return {"code_found": False}
    summary = {"code_found": True, "has_top": report.has_top, "has_testbench": report.has_testbench,
               "compiled": bool(report.compile and report.compile.ok)}
    if report.sim is not None:
        summary.update(tb_passed=report.sim.passed, tb_total=report.sim.total, sim_timed_out=report.sim.run.timed_out)
    if report.synth is not None:
        summary.update(synth_ok=report.synth.ok, cells=report.synth.cells, latches=report.synth.latches)
    return summary


def run_agent(config: Config, circuit: Circuit, llm: LLM, run_dir: Path, *,
              max_attempts: int | None = None, max_total_tokens: int | None = None,
              prompts_dir: Path | str | None = None, score_every_attempt: bool = False,
              on_event: Callable[[dict], None] | None = None) -> RunResult:
    """Run the generate / check / revise loop on one circuit, then score the final design
    (or, with score_every_attempt, every attempt's design) with the hidden reference test."""
    max_attempts = config.agent.max_attempts if max_attempts is None else max_attempts
    budget = config.agent.max_total_tokens if max_total_tokens is None else max_total_tokens
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
    prompts = load_prompts(_resolve(prompts_dir or config.agent.prompts_dir))
    spec = circuit.spec

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    log = EventLog(run_dir / "events.jsonl", on_event)
    start = time.monotonic()
    result = RunResult(run_dir, circuit.name, "attempts_exhausted", max_attempts)
    log.emit("run_started", circuit=circuit.name, split=spec.split, model=llm.name,
             max_attempts=max_attempts, max_total_tokens=budget, prompts=str(prompts.folder))

    messages = [Message("system", prompts.system), Message("user", prompts.task_message(spec))]
    for number in range(1, max_attempts + 1):
        if result.total_tokens >= budget:
            result.status = "token_budget_exhausted"
            log.emit("budget_exhausted", used=result.total_tokens, budget=budget)
            break

        log.emit("model_called", attempt=number, messages=len(messages))
        try:
            completion = llm.complete(messages)
        except LLMError as e:
            result.status, result.error = "llm_error", str(e)
            log.emit("model_error", attempt=number, error=str(e))
            break
        log.emit("model_replied", attempt=number, prompt_tokens=completion.prompt_tokens,
                 output_tokens=completion.output_tokens, duration_s=round(completion.duration_s, 3),
                 truncated=completion.truncated)

        workdir = run_dir / f"attempt_{number}"
        workdir.mkdir()
        (workdir / "response.md").write_text(completion.text)
        code = extract_verilog(completion.text)
        report = None
        if code is None:
            feedback = NO_CODE_FEEDBACK
        else:
            report = run_dev_checks(config.tools, workdir, separate(code, spec.top), spec.top,
                                    config.agent.feedback_max_chars)
            feedback = report.feedback
        if completion.truncated and not (report and report.passed):
            feedback = TRUNCATED_FEEDBACK + ("\n\n" + feedback if feedback else "")
        (workdir / "feedback.txt").write_text(feedback)
        attempt = Attempt(number, workdir, completion, code, report, feedback)
        result.attempts.append(attempt)
        log.emit("checks_done", attempt=number, passed=attempt.dev_passed, **_check_summary(report))

        if attempt.dev_passed:
            result.status = "dev_passed"
            break
        if number == max_attempts:
            break
        if result.total_tokens >= budget:
            result.status = "token_budget_exhausted"
            log.emit("budget_exhausted", used=result.total_tokens, budget=budget)
            break
        messages += [Message("assistant", completion.text),
                     Message("user", prompts.feedback_message(number, max_attempts, feedback))]
        log.emit("feedback_sent", attempt=number, chars=len(feedback))

    # The loop is over: only now may the hidden reference test run.
    final = next((a for a in reversed(result.attempts) if a.code is not None), None)
    with_code = [a for a in result.attempts if a.code is not None]
    for attempt in with_code if score_every_attempt else [a for a in with_code if a is final]:
        attempt.reference = reference_test(config.tools, circuit, attempt.workdir / DESIGN_FILE,
                                           run_dir / "reference" / attempt.workdir.name)
        log.emit("reference_scored", attempt=attempt.number, final=attempt is final,
                 outcome=attempt.reference.outcome, passed=attempt.reference.passed,
                 total=attempt.reference.total)
    if final is not None:
        result.final_attempt = final.number
        result.reference = final.reference

    result.duration_s = time.monotonic() - start
    log.emit("run_finished", status=result.status, attempts=len(result.attempts),
             total_tokens=result.total_tokens, duration_s=round(result.duration_s, 3),
             reference=result.reference.outcome if result.reference else None)
    _write_history(result, config, circuit, llm, prompts.folder, budget, score_every_attempt)
    return result


def _write_history(result: RunResult, config: Config, circuit: Circuit, llm: LLM,
                   prompts_folder: Path, budget: int, score_every_attempt: bool) -> None:
    ref = result.reference
    history = {
        "tool_version": __version__,
        "circuit": circuit.name,
        "split": circuit.spec.split,
        "settings": {
            "model": llm.name,
            "temperature": config.llm.temperature,
            "seed": config.llm.seed,
            "think": config.llm.think,
            "max_attempts": result.max_attempts,
            "max_total_tokens": budget,
            "prompts": str(prompts_folder),
            "yosys": config.tools.yosys,
            "score_every_attempt": score_every_attempt,
        },
        "status": result.status,
        "error": result.error,
        "duration_s": round(result.duration_s, 3),
        "tokens": {"prompt": result.prompt_tokens, "output": result.output_tokens, "total": result.total_tokens},
        "attempts": [
            {
                "number": a.number,
                "folder": a.workdir.name,
                "prompt_tokens": a.completion.prompt_tokens,
                "output_tokens": a.completion.output_tokens,
                "duration_s": round(a.completion.duration_s, 3),
                "truncated": a.completion.truncated,
                "dev_passed": a.dev_passed,
                "checks": _check_summary(a.report),
                "feedback": a.feedback,
                "reference": None if a.reference is None else {
                    "outcome": a.reference.outcome, "passed": a.reference.passed, "total": a.reference.total,
                },
            }
            for a in result.attempts
        ],
        "final_attempt": result.final_attempt,
        "reference": None if ref is None else {
            "outcome": ref.outcome, "passed": ref.passed, "total": ref.total, "detail": ref.detail,
        },
    }
    (result.run_dir / "history.json").write_text(json.dumps(history, indent=2) + "\n")
