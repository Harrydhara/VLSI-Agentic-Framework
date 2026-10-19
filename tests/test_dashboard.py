import dataclasses

import pytest

from vlsi_agent.config import load_config
from vlsi_agent.dashboard import DEMO_LIMITS, spec_markdown, strategy_choices, stream_run

_base = load_config()
CONFIG = dataclasses.replace(_base, tools=dataclasses.replace(_base.tools, sim_timeout_s=2))


def test_strategy_choices_list_every_shipped_strategy():
    choices = strategy_choices()
    assert {"prompts/default", "prompts/examples/tb_timing", "prompts/examples/spec_first",
            "prompts/examples/spec_first_v1"} <= set(choices)


def test_spec_markdown_shows_description_and_header():
    text = spec_markdown("adder4")
    assert "carry-in and carry-out" in text and "```verilog\nmodule adder4(" in text


@pytest.mark.parametrize("circuit,strategy", [("mux2", "prompts/default"), ("adder4", "prompts/examples/spec_first")])
def test_demo_mode_explains_its_limits(tmp_path, circuit, strategy):
    updates = list(stream_run(CONFIG, circuit, strategy, 3, demo=True, runs_dir=tmp_path))
    assert updates == [("", DEMO_LIMITS, None)]
    assert not any(tmp_path.iterdir())


@pytest.mark.integration
def test_demo_run_streams_progress_and_a_summary(tmp_path):
    updates = list(stream_run(CONFIG, "adder4", "prompts/default", 3, demo=True, runs_dir=tmp_path))
    assert len(updates) > 5  # one update per event, not just a final one
    assert all(run_dir is None for _, _, run_dir in updates[:-1])
    log, status, run_dir = updates[-1]
    assert "attempt 1: development checks FAILED (compiled, testbench 256/512" in log
    assert "attempt 2: development checks PASSED" in log
    assert status.startswith("**Hidden reference test: PASS** (512/512 checks)")
    assert "one-shot FAIL → final PASS" in status and "2 attempt(s)" in status
    assert [run_dir] == list(tmp_path.iterdir()) and (run_dir / "history.json").is_file()


def test_app_builds():
    pytest.importorskip("gradio")
    from vlsi_agent.dashboard import build_app
    app = build_app(CONFIG)
    assert app is not None
