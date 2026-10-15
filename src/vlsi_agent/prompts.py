"""Prompt templates: plain text files with {{name}} placeholders.

A prompts folder holds three files:

    system.txt    the model's standing instructions (no placeholders)
    task.txt      the first request; may use {{description}}, {{interface}}, {{top}}
    feedback.txt  sent after a failed attempt; may use {{attempt}}, {{max_attempts}}, {{feedback}}

Double braces leave Verilog's own braces ({cout, sum}) alone. Templates are checked
when loaded, so a misspelled placeholder fails before any model call is made.
To try a different strategy, copy prompts/default, edit the copy, and point the
config's prompts_dir (or `vlsi-agent run --prompts`) at it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from vlsi_agent.circuits import Spec

ALLOWED = {
    "system": set(),
    "task": {"description", "interface", "top"},
    "feedback": {"attempt", "max_attempts", "feedback"},
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


@dataclass(frozen=True)
class Prompts:
    folder: Path
    system: str
    task: str
    feedback: str

    def task_message(self, spec: Spec) -> str:
        return render(self.task, {"description": spec.description, "interface": spec.interface(),
                                  "top": spec.top})

    def feedback_message(self, attempt: int, max_attempts: int, feedback: str) -> str:
        return render(self.feedback, {"attempt": attempt, "max_attempts": max_attempts,
                                      "feedback": feedback})


def load_prompts(folder: Path | str) -> Prompts:
    folder = Path(folder)
    texts = {}
    for name, allowed in ALLOWED.items():
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
    return Prompts(folder, texts["system"], texts["task"], texts["feedback"])
