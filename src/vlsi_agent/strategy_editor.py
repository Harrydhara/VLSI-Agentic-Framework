"""Edit prompt strategies from the dashboard: copy one, change its templates, check, save.

Edited strategies live in prompts/custom/<name>/; the shipped ones (prompts/default and
prompts/examples/...) are only ever read, so the baseline stays as it is. Every run keeps
a copy of the templates it used (run_dir/prompts/), so editing a strategy after running
it does not change what the old runs record. Plain Python, tested without a browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from vlsi_agent.circuits import Spec
from vlsi_agent.config import PROJECT_ROOT
from vlsi_agent.prompts import (TEMPLATES, PromptError, load_strategy, placeholders, render,
                                unknown_placeholder_message, workflow_of)

CUSTOM_ROOT = PROJECT_ROOT / "prompts" / "custom"
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
NOTES_FILE = "README.md"

WORKFLOW_TEXT = {
    "single": "**single**: one reply holds both the design and its testbench; failed checks are sent back.",
    "spec_first": ("**spec_first**: one conversation writes the testbench from the specification before any "
                   "design exists, a second writes the design; the testbench author reviews disputed checks."),
}
FILE_HELP = {
    "system": "Standing instructions for the model. No placeholders.",
    "task": "The first request. {{description}}, {{interface}} and {{top}} bring in the circuit's specification.",
    "feedback": "Sent after an attempt fails the development checks. {{feedback}} is the check results; "
                "{{attempt}} and {{max_attempts}} count attempts.",
    "tb_system": "Standing instructions for the testbench conversation. No placeholders.",
    "tb_task": "Asks for a testbench from the specification, before any design exists. "
               "{{description}}, {{interface}}, {{top}}.",
    "tb_fix": "Sent when the testbench cannot be used. {{feedback}} says why.",
    "tb_review": "Sent when a design fails some of the testbench's checks, so its author can confirm or correct "
                 "them. {{failures}} lists the failing checks.",
    "design_system": "Standing instructions for the design conversation. No placeholders.",
    "design_task": "Asks for the design. {{description}}, {{interface}}, {{top}}.",
    "design_feedback": "Sent after a design fails the checks. {{feedback}}, {{attempt}}, {{max_attempts}}.",
}
# Without these the model would not see the specification or the check results (allowed, but worth a warning).
EXPECTED = {"task": {"description", "interface"}, "tb_task": {"description", "interface"},
            "design_task": {"description", "interface"}, "feedback": {"feedback"},
            "design_feedback": {"feedback"}, "tb_fix": {"feedback"}, "tb_review": {"failures"}}
SAMPLE_VALUES = {"attempt": 1, "max_attempts": 3,
                 "feedback": "[the development-check results for the failed attempt go here]",
                 "failures": "[the testbench checks that the design failed go here]"}


@dataclass(frozen=True)
class Draft:
    workflow: str
    texts: dict[str, str]  # template name (e.g. "task") -> text
    notes: str = ""  # README.md: what changed and why; never sent to the model


def is_custom(folder: Path, root: Path = CUSTOM_ROOT) -> bool:
    return Path(folder).resolve().parent == Path(root).resolve()


def load_draft(folder: Path, root: Path = CUSTOM_ROOT) -> Draft:
    """A strategy's templates as editable text (a missing file loads as empty)."""
    folder = Path(folder)
    workflow = workflow_of(folder)
    texts = {}
    for name in TEMPLATES[workflow]:
        path = folder / f"{name}.txt"
        texts[name] = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    notes_path = folder / NOTES_FILE
    if is_custom(folder, root) and notes_path.is_file():
        notes = notes_path.read_text(encoding="utf-8").strip()
    else:  # a shipped strategy's README describes that strategy and its results, not the copy
        notes = f"Started from `{_display(folder)}`.\n\nWhat changed and why:\n\n- "
    return Draft(workflow, texts, notes)


def suggest_name(folder: Path, root: Path = CUSTOM_ROOT) -> str:
    """The folder's own name when editing a custom strategy, else <name>_copy."""
    folder = Path(folder)
    return folder.name if is_custom(folder, root) else f"{folder.name}_copy"


def check_draft(draft: Draft) -> tuple[list[str], list[str]]:
    """(errors that block saving, warnings that do not)."""
    errors, warnings = [], []
    for name, allowed in TEMPLATES[draft.workflow].items():
        text = draft.texts.get(name, "")
        problem = unknown_placeholder_message(text, allowed)
        if problem:
            errors.append(f"`{name}.txt`: {problem}")
        if not text.strip():
            warnings.append(f"`{name}.txt` is empty.")
            continue
        missing = sorted(EXPECTED.get(name, set()) - placeholders(text))
        if missing:
            warnings.append(f"`{name}.txt` does not use {', '.join('{{' + m + '}}' for m in missing)}, "
                            "so the model will not see it.")
    return errors, warnings


def check_markdown(draft: Draft) -> str:
    errors, warnings = check_draft(draft)
    if not errors and not warnings:
        return "Checks passed: every placeholder is known and nothing important is left out."
    lines = [f"- ❌ {e}" for e in errors] + [f"- ⚠️ {w}" for w in warnings]
    head = "**Cannot save until the errors are fixed.**" if errors else "Can be saved; the warnings are advice."
    return head + "\n\n" + "\n".join(lines)


def save_draft(draft: Draft, name: str, root: Path = CUSTOM_ROOT, may_replace: bool = False) -> tuple[Path, bool]:
    """Write the draft to root/<name>/; returns (folder, whether it replaced an existing one).
    An existing strategy is only replaced with may_replace (it is the one being edited)."""
    if not NAME_PATTERN.fullmatch(name or ""):
        raise PromptError("Name: 1-40 characters, lowercase letters, digits, - and _, starting with a letter "
                          "or digit.")
    errors, _ = check_draft(draft)
    if errors:
        raise PromptError("Fix the errors first: " + "; ".join(errors))
    folder = Path(root) / name
    replaced = folder.exists()
    if replaced and not may_replace:
        raise PromptError(f"`{_display(folder)}` already exists. Load it to edit it, or choose another name.")
    folder.mkdir(parents=True, exist_ok=True)
    for workflow_files in TEMPLATES.values():  # templates of the other workflow would only confuse
        for other in set(workflow_files) - set(TEMPLATES[draft.workflow]):
            (folder / f"{other}.txt").unlink(missing_ok=True)
    for template in TEMPLATES[draft.workflow]:
        (folder / f"{template}.txt").write_text(draft.texts.get(template, "").strip() + "\n", encoding="utf-8")
    toml = folder / "strategy.toml"
    if draft.workflow == "single":
        toml.unlink(missing_ok=True)
    else:
        toml.write_text(f'workflow = "{draft.workflow}"\n', encoding="utf-8")
    if draft.notes.strip():
        (folder / NOTES_FILE).write_text(draft.notes.strip() + "\n", encoding="utf-8")
    load_strategy(folder)  # the agent must be able to load what was saved
    return folder, replaced


def preview(draft: Draft, spec: Spec) -> str:
    """Every template as the model would receive it, for this circuit and with sample feedback."""
    values = {"description": spec.description, "interface": spec.interface(), "top": spec.top, **SAMPLE_VALUES}
    parts = []
    for name, allowed in TEMPLATES[draft.workflow].items():
        try:
            text = render(draft.texts.get(name, ""), {k: v for k, v in values.items() if k in allowed})
        except PromptError as e:
            text = f"(cannot fill in: {e})"
        parts.append(f"===== {name}.txt =====\n{text.strip() or '(empty)'}")
    return "\n\n".join(parts)


def draft_from_slots(workflow: str, slot_values, notes: str = "") -> Draft:
    """The dashboard shows a workflow's templates in fixed text boxes, in TEMPLATES order."""
    return Draft(workflow, dict(zip(TEMPLATES[workflow], slot_values)), notes or "")


def _display(folder: Path) -> str:
    try:
        return Path(folder).resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(folder)
