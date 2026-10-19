import dataclasses
import json

import pytest

from samples import ADDER, ADDER_NO_CARRY, TB, block, reply
from vlsi_agent.agent import run_agent
from vlsi_agent.circuits import get_circuit
from vlsi_agent.config import PROJECT_ROOT, load_config
from vlsi_agent.dashboard import _run_folder, results_for_attempt, results_for_run
from vlsi_agent.llm import FakeLLM
from vlsi_agent.runview import (CHECK_HEADERS, attempt_diffs, check_table, diff_html, list_runs, load_run,
                                mismatch_lines, run_markdown)

_base = load_config()
CONFIG = dataclasses.replace(_base, tools=dataclasses.replace(_base.tools, sim_timeout_s=2))


def test_diff_html_marks_changes():
    text = diff_html("a\nb\n", "a\nc\n", "attempt 1", "attempt 2")
    assert '<div class="diff-del">-b</div>' in text and '<div class="diff-add">+c</div>' in text
    assert "identical" in diff_html("same", "same", "attempt 1", "attempt 2")
    assert "no file" in diff_html(None, "x", "attempt 1", "attempt 2")
    assert "<script>" not in diff_html("", "<script>", "a", "b")  # code is escaped


def test_run_picker_cannot_leave_the_runs_folder():
    assert _run_folder("../../src") is None
    assert _run_folder("/etc") is None
    assert _run_folder(None) is None
    assert results_for_run("../pyproject.toml")[0] == "Choose a run to see its attempts."


integration = pytest.mark.integration


@pytest.fixture
def scripted_run(tmp_path):
    """A real two-attempt run: broken carry first, then the fix."""
    folder = tmp_path / "runs" / "ui" / "demo"
    run_agent(CONFIG, get_circuit("adder4"), FakeLLM([reply(ADDER_NO_CARRY), reply(ADDER)]), folder,
              max_attempts=3, prompts_dir=PROJECT_ROOT / "prompts" / "default", score_every_attempt=True)
    return folder


@integration
def test_run_view_reads_every_attempt(scripted_run):
    run = load_run(scripted_run)
    assert [a.number for a in run.attempts] == [1, 2]
    first, second = run.attempts
    assert "assign cout = 1'b0" in first.design and "assign {cout, sum}" in second.design
    assert "module tb" in first.testbench and first.reply.startswith("Here is the design")
    assert "256/512 checks passed" in first.feedback and second.feedback == ""
    assert first.reference["outcome"] == "fail" and second.reference["outcome"] == "pass"


@integration
def test_check_table_has_one_row_per_attempt(scripted_run):
    rows = check_table(load_run(scripted_run))
    assert len(CHECK_HEADERS) == len(rows[0])
    assert rows == [[1, "FAIL", "ok", "256/512", "17 cells", "FAIL 256/512"],
                    [2, "PASS", "ok", "512/512", "23 cells", "PASS 512/512"]]


@integration
def test_hidden_test_mismatches_and_diffs(scripted_run):
    run = load_run(scripted_run)
    text = mismatch_lines(run.attempt(1))
    assert text.startswith("**FAIL 256/512**") and "MISMATCH a=1 b=15" in text
    assert "No mismatches" in mismatch_lines(run.attempt(2))
    assert "first attempt" in attempt_diffs(run, 1)
    changes = attempt_diffs(run, 2)
    assert "diff-del\">-  assign cout = 1&#x27;b0;" in changes and "diff-add\">+  assign {cout, sum}" in changes
    assert "identical" in changes  # the testbench did not change
    summary = run_markdown(run)
    assert summary.startswith("**Hidden reference test: PASS** (512/512 checks passed)")
    assert "strategy `default`" in summary and "2 of 3 attempt(s)" in summary


@integration
def test_list_runs_finds_run_folders_newest_first(scripted_run, tmp_path):
    later = tmp_path / "runs" / "agent" / "later"
    run_agent(CONFIG, get_circuit("adder4"), FakeLLM([reply(ADDER)]), later, max_attempts=1,
              prompts_dir=PROJECT_ROOT / "prompts" / "default")
    assert list_runs(tmp_path / "runs") == ["agent/later", "ui/demo"]


@integration
def test_spec_first_run_shows_the_spec_testbench(tmp_path):
    folder = tmp_path / "run"
    run_agent(CONFIG, get_circuit("adder4"), FakeLLM([block(TB), block(ADDER)]), folder, max_attempts=3,
              prompts_dir=PROJECT_ROOT / "prompts" / "examples" / "spec_first", score_every_attempt=True)
    run = load_run(folder)
    assert run.attempts[0].testbench.strip() == TB.strip()
    assert "(testbench written first)" in run_markdown(run)


@integration
def test_dashboard_results_functions_on_a_real_run_folder(scripted_run, monkeypatch):
    import vlsi_agent.dashboard as dashboard
    monkeypatch.setattr(dashboard, "RUNS_ROOT", scripted_run.parents[1])
    summary, table, choices, selected = results_for_run("ui/demo")
    assert choices == ["1", "2"] and selected == "2" and len(table) == 2
    design, testbench, diffs, hidden, feedback, model_reply = results_for_attempt("ui/demo", "1")
    assert "assign cout = 1'b0" in design and "MISMATCH" in hidden and "256/512" in feedback
    assert results_for_attempt("ui/demo", "2")[4].startswith("(nothing")
