import dataclasses

import pytest

from vlsi_agent.agent import _save_prompts
from vlsi_agent.circuits import get_circuit
from vlsi_agent.config import PROJECT_ROOT
from vlsi_agent.dashboard import editor_draft, editor_save, strategy_choices
from vlsi_agent.prompts import TEMPLATES, PromptError, load_strategy, workflow_of
from vlsi_agent.strategy_editor import (Draft, check_draft, check_markdown, draft_from_slots, load_draft, preview,
                                        save_draft, suggest_name)

PROMPTS = PROJECT_ROOT / "prompts"
SINGLE = Draft("single", {"system": "Be careful.", "task": "Design {{top}}:\n{{description}}\n{{interface}}",
                          "feedback": "Attempt {{attempt}} of {{max_attempts}} failed:\n{{feedback}}"}, "notes")


def test_every_shipped_strategy_loads_into_the_editor_without_errors():
    for entry in strategy_choices():
        if entry.startswith("prompts/custom/"):
            continue
        draft = load_draft(PROJECT_ROOT / entry)
        assert list(draft.texts) == list(TEMPLATES[draft.workflow])
        assert check_draft(draft)[0] == [], entry
        assert draft.notes.startswith(f"Started from `{entry}`")  # not the shipped README with its results


def test_names_for_copies_and_for_custom_strategies(tmp_path):
    assert suggest_name(PROMPTS / "default", root=tmp_path) == "default_copy"
    assert suggest_name(tmp_path / "mine", root=tmp_path) == "mine"


def test_check_reports_errors_and_warnings():
    assert check_draft(SINGLE) == ([], [])
    assert check_markdown(SINGLE).startswith("Checks passed")
    bad = dataclasses.replace(SINGLE, texts={**SINGLE.texts, "task": "Design {{top}} {{descripton}}",
                                             "system": "  "})
    errors, warnings = check_draft(bad)
    assert errors == ["`task.txt`: unknown placeholder(s) {{descripton}}; allowed here: {{description}}, "
                      "{{interface}}, {{top}}"]
    assert warnings == ["`system.txt` is empty.",
                        "`task.txt` does not use {{description}}, {{interface}}, so the model will not see it."]
    assert check_markdown(bad).startswith("**Cannot save until the errors are fixed.**")


def test_save_writes_a_loadable_strategy_and_never_replaces_by_accident(tmp_path):
    folder, replaced = save_draft(SINGLE, "mine", root=tmp_path)
    assert (folder, replaced) == (tmp_path / "mine", False)
    assert load_strategy(folder).task == SINGLE.texts["task"] and workflow_of(folder) == "single"
    assert (folder / "README.md").read_text() == "notes\n"
    with pytest.raises(PromptError, match="already exists"):
        save_draft(SINGLE, "mine", root=tmp_path)
    assert save_draft(SINGLE, "mine", root=tmp_path, may_replace=True) == (folder, True)


def test_save_switches_workflow_cleanly(tmp_path):
    spec_first = load_draft(PROMPTS / "examples" / "spec_first")
    folder, _ = save_draft(spec_first, "both", root=tmp_path)
    assert workflow_of(folder) == "spec_first" and load_strategy(folder).tb_review == spec_first.texts["tb_review"]
    save_draft(SINGLE, "both", root=tmp_path, may_replace=True)
    assert workflow_of(folder) == "single" and not (folder / "strategy.toml").exists()
    assert sorted(p.name for p in folder.glob("*.txt")) == ["feedback.txt", "system.txt", "task.txt"]


@pytest.mark.parametrize("name", ["", "Mine", "two words", "../escape", "a/b", "-dash", "x" * 41])
def test_save_rejects_bad_names(tmp_path, name):
    with pytest.raises(PromptError, match="Name"):
        save_draft(SINGLE, name, root=tmp_path)
    assert not any(tmp_path.iterdir())


def test_save_refuses_unknown_placeholders(tmp_path):
    bad = dataclasses.replace(SINGLE, texts={**SINGLE.texts, "feedback": "{{feedbak}}"})
    with pytest.raises(PromptError, match="feedbak"):
        save_draft(bad, "mine", root=tmp_path)
    assert not (tmp_path / "mine").exists()


def test_preview_fills_in_the_circuit_and_sample_feedback():
    text = preview(SINGLE, get_circuit("adder4").spec)
    assert "===== task.txt =====\nDesign adder4:" in text and "module adder4(" in text
    assert "Attempt 1 of 3 failed:\n[the development-check results" in text
    broken = dataclasses.replace(SINGLE, texts={**SINGLE.texts, "system": "{{feedback}}"})
    assert "===== system.txt =====\n(cannot fill in: unknown placeholder(s): {{feedback}})" in preview(
        broken, get_circuit("adder4").spec)


def test_draft_from_slots_ignores_unused_boxes():
    draft = draft_from_slots("single", ["s", "t", "f", "", "", "", ""], "n")
    assert draft.texts == {"system": "s", "task": "t", "feedback": "f"} and draft.notes == "n"


@pytest.mark.parametrize("folder", [PROMPTS / "default", PROMPTS / "examples" / "spec_first"])
def test_runs_keep_a_loadable_copy_of_their_prompts(tmp_path, folder):
    original = load_strategy(folder)
    _save_prompts(original, tmp_path / "prompts")
    copy = load_strategy(tmp_path / "prompts")
    assert copy.workflow == original.workflow
    assert all(getattr(copy, name) == getattr(original, name) for name in TEMPLATES[original.workflow])


def test_dashboard_editor_load_and_save(tmp_path):
    draft, name = editor_draft("prompts/default")
    assert name == "default_copy" and draft.workflow == "single"
    assert editor_draft("../src") is None and editor_draft(None) is None

    boxes = [draft.texts[n] for n in TEMPLATES["single"]] + [""] * 4
    status, entry = editor_save("prompts/default", "single", "my_try", boxes, "notes", root=tmp_path)
    assert status.startswith(f"**Saved `{entry}`**") and (tmp_path / "my_try" / "task.txt").is_file()
    status, again = editor_save("prompts/default", "single", "my_try", boxes, "notes", root=tmp_path)
    assert again is None and "already exists" in status  # loaded from elsewhere: no silent overwrite
    status, again = editor_save(entry, "single", "my_try", boxes, "notes", root=tmp_path)
    assert again == entry and "(replaced the earlier version)" in status  # editing the loaded strategy
    status, none = editor_save(entry, "single", "my_try", ["{{oops}}", "", ""], "", root=tmp_path)
    assert none is None and status.startswith("**Not saved.**")
