"""Prompt templates: plain text files with {{name}} placeholders.

A strategy is a folder of templates. By default it uses the "single" workflow, where
one reply contains both the design and its testbench:

    system.txt    the model's standing instructions (no placeholders)
    task.txt      the first request; may use {{description}}, {{interface}}, {{top}}
    feedback.txt  sent after a failed attempt; may use {{attempt}}, {{max_attempts}}, {{feedback}}

A folder with a strategy.toml containing `workflow = "spec_first"` uses two separate
conversations instead: one writes the testbench from the specification before any
design exists, the other writes the design (see agent.py). Its files:

    tb_system.txt, design_system.txt   standing instructions (no placeholders)
    tb_task.txt, design_task.txt       {{description}}, {{interface}}, {{top}}
    tb_fix.txt                         {{feedback}}: the testbench could not be used
    tb_review.txt                      {{failures}}: a design fails some of its checks
    design_feedback.txt                {{attempt}}, {{max_attempts}}, {{feedback}}

Double braces leave Verilog's own braces ({cout, sum}) alone. Templates are checked
when loaded, so a misspelled placeholder fails before any model call is made.
To try a different strategy, copy a folder, edit the copy, and point the config's
prompts_dir (or `vlsi-agent run --prompts`) at it.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from vlsi_agent.circuits import Spec

WORKFLOWS = ("single", "spec_first")
_SPEC = {"description", "interface", "top"}
ALLOWED = {
    "system": set(),
    "task": _SPEC,
    "feedback": {"attempt", "max_attempts", "feedback"},
}
SPEC_FIRST_ALLOWED = {
    "tb_system": set(),
    "tb_task": _SPEC,
    "tb_fix": {"feedback"},
    "tb_review": {"failures"},
    "design_system": set(),
    "design_task": _SPEC,
    "design_feedback": {"attempt", "max_attempts", "feedback"},
}
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")


class PromptError(ValueError):
    """A prompts folder is missing a file or a template uses an unknown placeholder."""


def render(template: str, values: dict[str, object]) -> str:
    """Replace each {{name}} with values[name]; an unknown name raises PromptError."""
    unknown = sorted(set(_PLACEHOLDER.findall(template)) - set(values))
    if unknown:
        raise PromptError(f"unknown placeholder(s): {', '.join('{{' + u + '}}' for u in unknown)}")
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), template)


def _spec_values(spec: Spec) -> dict[str, str]:
    return {"description": spec.description, "interface": spec.interface(), "top": spec.top}


@dataclass(frozen=True)
class Prompts:
    """Templates for the single workflow."""

    folder: Path
    system: str
    task: str
    feedback: str
    workflow = "single"

    def task_message(self, spec: Spec) -> str:
        return render(self.task, _spec_values(spec))

    def feedback_message(self, attempt: int, max_attempts: int, feedback: str) -> str:
        return render(self.feedback, {"attempt": attempt, "max_attempts": max_attempts,
                                      "feedback": feedback})


@dataclass(frozen=True)
class SpecFirstPrompts:
    """Templates for the spec_first workflow."""

    folder: Path
    tb_system: str
    tb_task: str
    tb_fix: str
    tb_review: str
    design_system: str
    design_task: str
    design_feedback: str
    workflow = "spec_first"

    def tb_task_message(self, spec: Spec) -> str:
        return render(self.tb_task, _spec_values(spec))

    def tb_fix_message(self, feedback: str) -> str:
        return render(self.tb_fix, {"feedback": feedback})

    def tb_review_message(self, failures: str) -> str:
        return render(self.tb_review, {"failures": failures})

    def design_task_message(self, spec: Spec) -> str:
        return render(self.design_task, _spec_values(spec))

    def design_feedback_message(self, attempt: int, max_attempts: int, feedback: str) -> str:
        return render(self.design_feedback, {"attempt": attempt, "max_attempts": max_attempts,
                                             "feedback": feedback})


def _load_files(folder: Path, allowed_by_file: dict[str, set[str]]) -> dict[str, str]:
    texts = {}
    for name, allowed in allowed_by_file.items():
        path = folder / f"{name}.txt"
        try:
            text = path.read_text()
        except FileNotFoundError:
            raise PromptError(f"missing prompt file: {path}") from None
        unknown = sorted(set(_PLACEHOLDER.findall(text)) - allowed)
        if unknown:
            allowed_text = ", ".join("{{" + a + "}}" for a in sorted(allowed)) or "none"
            raise PromptError(f"{path}: unknown placeholder(s) {', '.join('{{' + u + '}}' for u in unknown)}; "
                              f"allowed here: {allowed_text}")
        texts[name] = text.strip()
    return texts


def load_prompts(folder: Path | str) -> Prompts:
    """Load a single-workflow prompts folder."""
    folder = Path(folder)
    return Prompts(folder, **_load_files(folder, ALLOWED))


def workflow_of(folder: Path | str) -> str:
    path = Path(folder) / "strategy.toml"
    if not path.is_file():
        return "single"
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise PromptError(f"{path}: invalid TOML: {e}") from None
    unknown = sorted(set(data) - {"workflow"})
    if unknown:
        raise PromptError(f"{path}: unknown key(s): {', '.join(unknown)}")
    workflow = data.get("workflow", "single")
    if workflow not in WORKFLOWS:
        raise PromptError(f"{path}: workflow must be one of {WORKFLOWS}, got {workflow!r}")
    return workflow


def load_strategy(folder: Path | str) -> Prompts | SpecFirstPrompts:
    """Load a strategy folder of either workflow."""
    folder = Path(folder)
    if workflow_of(folder) == "spec_first":
        return SpecFirstPrompts(folder, **_load_files(folder, SPEC_FIRST_ALLOWED))
    return load_prompts(folder)
