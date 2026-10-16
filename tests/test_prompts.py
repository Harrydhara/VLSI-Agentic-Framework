import shutil

import pytest

from vlsi_agent.circuits import get_circuit
from vlsi_agent.config import PROJECT_ROOT
from vlsi_agent.prompts import PromptError, load_prompts, render

DEFAULT = PROJECT_ROOT / "prompts" / "default"
ALL_FOLDERS = sorted(p.parent for p in (PROJECT_ROOT / "prompts").rglob("system.txt"))


def test_shipped_strategies_are_found():
    names = [str(f.relative_to(PROJECT_ROOT / "prompts")).replace("\\", "/") for f in ALL_FOLDERS]
    assert "default" in names and "examples/tb_timing" in names


@pytest.mark.parametrize("folder", ALL_FOLDERS, ids=lambda p: p.name)
def test_every_prompts_folder_loads_and_renders(folder):
    prompts = load_prompts(folder)
    spec = get_circuit("counter4").spec
    task = prompts.task_message(spec)
    assert spec.description in task and spec.interface() in task
    assert "- a failed check" in prompts.feedback_message(2, 3, "- a failed check")
    assert "RESULT: %0d/%0d passed" in prompts.system  # the dev checks depend on this line


def test_render_replaces_placeholders_and_leaves_verilog_braces_alone():
    assert render("{{ top }}: assign {cout, sum} = x;", {"top": "adder4"}) == "adder4: assign {cout, sum} = x;"


def test_render_rejects_unknown_placeholders():
    with pytest.raises(PromptError, match=r"unknown placeholder\(s\): \{\{nme\}\}"):
        render("hi {{nme}}", {"name": "x"})


def test_default_prompts_render_the_spec():
    prompts = load_prompts(DEFAULT)
    spec = get_circuit("adder4").spec
    task = prompts.task_message(spec)
    assert spec.description in task and spec.interface() in task and "top module adder4" in task
    feedback = prompts.feedback_message(1, 3, "- something failed")
    assert feedback.startswith("Attempt 1 of 3") and "- something failed" in feedback
    assert "RESULT: %0d/%0d passed" in prompts.system


def test_unknown_placeholder_in_a_file_is_caught_at_load_time(tmp_path):
    folder = tmp_path / "mine"
    shutil.copytree(DEFAULT, folder)
    (folder / "task.txt").write_text("Build {{description}} with {{feedback}}")
    with pytest.raises(PromptError, match=r"task.txt: unknown placeholder\(s\) \{\{feedback\}\}"):
        load_prompts(folder)


def test_missing_prompt_file(tmp_path):
    folder = tmp_path / "mine"
    shutil.copytree(DEFAULT, folder)
    (folder / "system.txt").unlink()
    with pytest.raises(PromptError, match="missing prompt file: .*system.txt"):
        load_prompts(folder)
