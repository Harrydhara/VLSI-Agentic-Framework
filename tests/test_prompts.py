import shutil

import pytest

from vlsi_agent.circuits import get_circuit
from vlsi_agent.config import PROJECT_ROOT
from vlsi_agent.prompts import PromptError, load_prompts, load_strategy, render, workflow_of

DEFAULT = PROJECT_ROOT / "prompts" / "default"
SPEC_FIRST = PROJECT_ROOT / "prompts" / "examples" / "spec_first"
ALL_FOLDERS = sorted({p.parent for name in ("system.txt", "strategy.toml")
                      for p in (PROJECT_ROOT / "prompts").rglob(name)})


def test_shipped_strategies_are_found():
    names = [str(f.relative_to(PROJECT_ROOT / "prompts")).replace("\\", "/") for f in ALL_FOLDERS]
    assert {"default", "examples/tb_timing", "examples/spec_first"} <= set(names)


@pytest.mark.parametrize("folder", ALL_FOLDERS, ids=lambda p: p.name)
def test_every_strategy_loads_and_renders(folder):
    strategy = load_strategy(folder)
    spec = get_circuit("counter4").spec
    if strategy.workflow == "single":
        task = strategy.task_message(spec)
        assert "- a failed check" in strategy.feedback_message(2, 3, "- a failed check")
        testbench_rules = strategy.system
    else:
        task = strategy.design_task_message(spec)
        assert spec.interface() in strategy.tb_task_message(spec)
        assert "- a failed check" in strategy.design_feedback_message(2, 3, "- a failed check")
        assert "bad port" in strategy.tb_fix_message("bad port")
        assert "FAIL: x" in strategy.tb_review_message("FAIL: x")
        testbench_rules = strategy.tb_system
    assert spec.description in task and spec.interface() in task
    assert "RESULT: %0d/%0d passed" in testbench_rules  # the dev checks depend on this line


def test_workflow_is_read_from_strategy_toml(tmp_path):
    assert workflow_of(DEFAULT) == "single" and workflow_of(SPEC_FIRST) == "spec_first"
    folder = tmp_path / "mine"
    shutil.copytree(SPEC_FIRST, folder)
    (folder / "strategy.toml").write_text('workflow = "ping_pong"\n')
    with pytest.raises(PromptError, match="workflow must be one of"):
        load_strategy(folder)
    (folder / "strategy.toml").write_text('workflow = "spec_first"\ncolour = "blue"\n')
    with pytest.raises(PromptError, match=r"unknown key\(s\): colour"):
        load_strategy(folder)


def test_spec_first_folder_needs_all_its_files(tmp_path):
    folder = tmp_path / "mine"
    shutil.copytree(SPEC_FIRST, folder)
    (folder / "tb_review.txt").unlink()
    with pytest.raises(PromptError, match="missing prompt file: .*tb_review.txt"):
        load_strategy(folder)


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
