"""The agent loop: generate a design, run development checks, revise from the feedback.

max_attempts = 1 is the one-shot baseline; more attempts turn on revision, with the
same model and the same prompts. The loop stops when the development checks pass,
when the attempts run out, or, before any model call, once the token budget is used
up (a single call can still overshoot the budget by its own size). Afterwards the
final design (from the last attempt that produced code) is scored with the circuit's
hidden reference test. The model never sees that test or its result, and the loop
never uses it to decide anything: scoring only starts once the loop has finished.

Two workflows (chosen by the strategy folder, see prompts.py):

- single: one reply per attempt contains the design and its own testbench.
- spec_first: a testbench conversation writes the testbench from the specification
  before any design exists, and a separate design conversation writes the design,
  which is checked against that testbench. Neither conversation ever sees the other's
  code. When a design fails checks, the failing checks go to the testbench's author
  first; if it confirms them, they go to the design conversation as feedback.

With score_every_attempt, every attempt's design is scored too (still after the
loop). Attempt 1 is then exactly what a one-shot run would have produced, so one run
gives both the one-shot and the iterative score, and shows when a revision fixed a
design or broke one.

Every run writes to its own folder:

    events.jsonl            one JSON event per line, written as things happen
    history.json            settings, every model call, every attempt, the scores
    attempt_<n>/            response.md (raw reply), design.v, tb.v, tool logs, feedback.txt
    testbench/              spec_first only: each testbench version and its checks
    reference/attempt_<n>/  the hidden reference test run on that attempt's design
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from vlsi_agent import __version__
from vlsi_agent.circuits import Circuit
from vlsi_agent.config import Config, project_path
from vlsi_agent.devcheck import (DESIGN_FILE, DevReport, Sources, check_testbench, failure_lines, run_dev_checks,
                                 separate)
from vlsi_agent.evaluate import ReferenceResult, reference_test
from vlsi_agent.llm import LLM, Completion, LLMError, Message, extract_verilog
from vlsi_agent.prompts import Prompts, SpecFirstPrompts, load_strategy

STATUSES = ("dev_passed", "attempts_exhausted", "token_budget_exhausted", "llm_error")
TESTBENCH_TRIES = 3  # spec_first: the first testbench plus up to two fixes

NO_CODE_FEEDBACK = ("- No Verilog module was found in your reply. Reply with two ```verilog code blocks: "
                    "the design, then the testbench.")
NO_DESIGN_FEEDBACK = "- No Verilog module was found in your reply. Reply with the design in one ```verilog code block."
TRUNCATED_FEEDBACK = ("- Your reply was cut off at the output token limit before it finished. "
                      "Keep the code complete and shorter.")


@dataclass(frozen=True)
class Call:
    """One model call. role: "design" (single: design and testbench together),
    "testbench", "testbench_fix" or "testbench_review"."""

    role: str
    attempt: int | None  # the design attempt it belongs to; None for the first testbench calls
    completion: Completion


@dataclass
class Attempt:
    number: int
    workdir: Path
    completion: Completion  # the reply that produced this attempt's design
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
    workflow: str = "single"
    attempts: list[Attempt] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    reference: ReferenceResult | None = None  # None if no attempt produced code
    final_attempt: int | None = None
    error: str | None = None
    duration_s: float = 0.0

    @property
    def prompt_tokens(self) -> int:
        return sum(c.completion.prompt_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.completion.output_tokens for c in self.calls)

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


class _Run:
    """What both workflows share: budget-checked model calls and design checks."""

    def __init__(self, config: Config, circuit: Circuit, llm: LLM, run_dir: Path, log: EventLog,
                 result: RunResult, budget: int):
        self.config, self.circuit, self.llm = config, circuit, llm
        self.run_dir, self.log, self.result, self.budget = run_dir, log, result, budget

    def budget_left(self) -> bool:
        if self.result.total_tokens < self.budget:
            return True
        self.result.status = "token_budget_exhausted"
        self.log.emit("budget_exhausted", used=self.result.total_tokens, budget=self.budget)
        return False

    def call(self, role: str, attempt: int | None, messages: list[Message]) -> Completion | None:
        """Ask the model; None (with the run's status set) if the budget is used up or the model fails."""
        if not self.budget_left():
            return None
        self.log.emit("model_called", attempt=attempt, role=role, messages=len(messages))
        try:
            completion = self.llm.complete(messages)
        except LLMError as e:
            self.result.status, self.result.error = "llm_error", str(e)
            self.log.emit("model_error", attempt=attempt, role=role, error=str(e))
            return None
        self.result.calls.append(Call(role, attempt, completion))
        self.log.emit("model_replied", attempt=attempt, role=role, prompt_tokens=completion.prompt_tokens,
                      output_tokens=completion.output_tokens, duration_s=round(completion.duration_s, 3),
                      truncated=completion.truncated)
        return completion

    def check_attempt(self, number: int, completion: Completion, *, testbench: str | None = None,
                      require_testbench: bool = True, no_code_feedback: str = NO_CODE_FEEDBACK) -> Attempt:
        """Run the development checks on a reply. With testbench given, that testbench is used
        instead of any testbench in the reply."""
        spec = self.circuit.spec
        workdir = self.run_dir / f"attempt_{number}"
        workdir.mkdir()
        (workdir / "response.md").write_text(completion.text)
        code = extract_verilog(completion.text)
        report = None
        if code is None:
            feedback = no_code_feedback
        else:
            sources = separate(code, spec.top)
            if testbench is not None:
                sources = Sources(sources.design, testbench, sources.module_names)
            report = run_dev_checks(self.config.tools, workdir, sources, spec.top,
                                    self.config.agent.feedback_max_chars, require_testbench=require_testbench)
            feedback = report.feedback
        if completion.truncated and not (report and report.passed):
            feedback = TRUNCATED_FEEDBACK + ("\n\n" + feedback if feedback else "")
        (workdir / "feedback.txt").write_text(feedback)
        attempt = Attempt(number, workdir, completion, code, report, feedback)
        self.result.attempts.append(attempt)
        self.log.emit("checks_done", attempt=number, passed=attempt.dev_passed, **_check_summary(report))
        return attempt

    def recheck(self, attempt: Attempt, testbench: str) -> None:
        """Run the checks again on an attempt's design with a revised testbench."""
        spec = self.circuit.spec
        sources = separate(attempt.code or "", spec.top)
        attempt.report = run_dev_checks(self.config.tools, attempt.workdir,
                                        Sources(sources.design, testbench, sources.module_names), spec.top,
                                        self.config.agent.feedback_max_chars)
        attempt.feedback = attempt.report.feedback
        (attempt.workdir / "feedback.txt").write_text(attempt.feedback)
        self.log.emit("checks_done", attempt=attempt.number, passed=attempt.dev_passed, recheck=True,
                      **_check_summary(attempt.report))


def _loop_single(run: _Run, prompts: Prompts, max_attempts: int) -> None:
    spec = run.circuit.spec
    messages = [Message("system", prompts.system), Message("user", prompts.task_message(spec))]
    for number in range(1, max_attempts + 1):
        completion = run.call("design", number, messages)
        if completion is None:
            return
        attempt = run.check_attempt(number, completion)
        if attempt.dev_passed:
            run.result.status = "dev_passed"
            return
        if number == max_attempts or not run.budget_left():
            return
        messages += [Message("assistant", completion.text),
                     Message("user", prompts.feedback_message(number, max_attempts, attempt.feedback))]
        run.log.emit("feedback_sent", attempt=number, chars=len(attempt.feedback))


def _testbench_in(text: str, top: str) -> str | None:
    code = extract_verilog(text)
    return separate(code, top).testbench if code else None


_CONFIRMED = re.compile(r"^\W*TESTBENCH OK\W*$", re.MULTILINE)


def _review_verdict(text: str, current: str, top: str) -> str | None:
    """The revised testbench from a review reply, or None if the author kept its testbench:
    it wrote a line TESTBENCH OK (even if it also pasted code), sent no testbench, or sent
    the same testbench back."""
    if _CONFIRMED.search(text):
        return None
    revised = _testbench_in(text, top)
    if revised is None or " ".join(revised.split()) == " ".join(current.split()):
        return None
    return revised


def _testbench_disagrees(report: DevReport | None) -> bool:
    """True when the design ran against the testbench and failed some of its checks."""
    return bool(report and report.sim and report.sim.completed and report.sim.total is not None
                and not report.sim.all_passed)


def _loop_spec_first(run: _Run, prompts: SpecFirstPrompts, max_attempts: int) -> None:
    spec, tools, max_chars = run.circuit.spec, run.config.tools, run.config.agent.feedback_max_chars
    tb_dir = run.run_dir / "testbench"

    # 1. The testbench, from the specification alone, before any design exists.
    tb_messages = [Message("system", prompts.tb_system), Message("user", prompts.tb_task_message(spec))]
    testbench = None
    for version in range(1, TESTBENCH_TRIES + 1):
        completion = run.call("testbench" if version == 1 else "testbench_fix", None, tb_messages)
        if completion is None:
            return
        candidate = _testbench_in(completion.text, spec.top)
        check = check_testbench(tools, tb_dir / f"v{version}", spec, candidate, max_chars)
        (tb_dir / f"v{version}" / "response.md").write_text(completion.text)
        run.log.emit("testbench_checked", version=version, usable=check.ok)
        tb_messages.append(Message("assistant", completion.text))
        if check.ok:
            testbench = candidate
            break
        tb_messages.append(Message("user", prompts.tb_fix_message(check.feedback)))
    if testbench is None:
        run.log.emit("testbench_unusable", tries=TESTBENCH_TRIES)  # carry on with compile and synthesis checks only

    # 2. The design, in its own conversation, checked against that testbench.
    design_messages = [Message("system", prompts.design_system), Message("user", prompts.design_task_message(spec))]
    reviews = 0
    for number in range(1, max_attempts + 1):
        completion = run.call("design", number, design_messages)
        if completion is None:
            return
        attempt = run.check_attempt(number, completion, testbench=testbench, require_testbench=testbench is not None,
                                    no_code_feedback=NO_DESIGN_FEEDBACK)

        # 3. The design fails some checks: the testbench's author re-checks them first.
        if testbench is not None and _testbench_disagrees(attempt.report):
            tb_messages.append(Message("user", prompts.tb_review_message(failure_lines(attempt.report.sim.run.output))))
            review = run.call("testbench_review", number, tb_messages)
            if review is None:
                return
            tb_messages.append(Message("assistant", review.text))
            reviews += 1
            revised = _review_verdict(review.text, testbench, spec.top)
            folder = tb_dir / f"review{reviews}"
            if revised is None:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "response.md").write_text(review.text)
                run.log.emit("testbench_confirmed", attempt=number)
            else:
                check = check_testbench(tools, folder, spec, revised, max_chars)
                (folder / "response.md").write_text(review.text)
                if check.ok:
                    testbench = revised
                    run.log.emit("testbench_revised", attempt=number)
                    run.recheck(attempt, testbench)
                else:
                    run.log.emit("testbench_revision_rejected", attempt=number)  # keep the previous testbench

        if attempt.dev_passed:
            run.result.status = "dev_passed"
            return
        if number == max_attempts or not run.budget_left():
            return
        design_messages += [Message("assistant", completion.text),
                            Message("user", prompts.design_feedback_message(number, max_attempts, attempt.feedback))]
        run.log.emit("feedback_sent", attempt=number, chars=len(attempt.feedback))


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
    strategy = load_strategy(project_path(prompts_dir or config.agent.prompts_dir))

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    log = EventLog(run_dir / "events.jsonl", on_event)
    start = time.monotonic()
    result = RunResult(run_dir, circuit.name, "attempts_exhausted", max_attempts, workflow=strategy.workflow)
    log.emit("run_started", circuit=circuit.name, split=circuit.spec.split, model=llm.name,
             max_attempts=max_attempts, max_total_tokens=budget, prompts=str(strategy.folder),
             workflow=strategy.workflow)

    run = _Run(config, circuit, llm, run_dir, log, result, budget)
    if isinstance(strategy, SpecFirstPrompts):
        _loop_spec_first(run, strategy, max_attempts)
    else:
        _loop_single(run, strategy, max_attempts)

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
    _write_history(result, config, circuit, llm, strategy.folder, budget, score_every_attempt)
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
            "workflow": result.workflow,
            "yosys": config.tools.yosys,
            "score_every_attempt": score_every_attempt,
        },
        "status": result.status,
        "error": result.error,
        "duration_s": round(result.duration_s, 3),
        "tokens": {"prompt": result.prompt_tokens, "output": result.output_tokens, "total": result.total_tokens},
        "calls": [
            {"role": c.role, "attempt": c.attempt, "prompt_tokens": c.completion.prompt_tokens,
             "output_tokens": c.completion.output_tokens, "duration_s": round(c.completion.duration_s, 3),
             "truncated": c.completion.truncated}
            for c in result.calls
        ],
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
