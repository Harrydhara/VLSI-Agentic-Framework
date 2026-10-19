import dataclasses
import json

import pytest

from samples import ADDER, ADDER_NO_CARRY, reply
from vlsi_agent import cli
from vlsi_agent.bench import (delivered_after, parse_seeds, render_markdown, row_from_history, run_benchmark,
                              summarize, summarize_group)
from vlsi_agent.circuits import get_circuit
from vlsi_agent.config import PROJECT_ROOT, load_config
from vlsi_agent.llm import FakeLLM

DEFAULT_PROMPTS = PROJECT_ROOT / "prompts" / "default"
_base = load_config()
CONFIG = dataclasses.replace(_base, tools=dataclasses.replace(_base.tools, sim_timeout_s=2))


def row(outcomes, strategy="default", circuit="alu4", split="dev", status="attempts_exhausted", cells=None, **extra):
    return {"circuit": circuit, "split": split, "family": "alu", "strategy": strategy, "seed": 1, "status": status,
            "attempts_used": len(outcomes), "attempt_outcomes": outcomes,
            "oneshot_outcome": delivered_after(outcomes, 1), "final_outcome": delivered_after(outcomes, len(outcomes)),
            "oneshot_compiled": True, "final_compiled": True, "oneshot_synth_ok": True, "final_synth_ok": True,
            "oneshot_cells": cells, "final_cells": cells, "final_latches": 0,
            "own_checks_passed": status == "dev_passed", "prompt_tokens": 100, "output_tokens": 50,
            "total_tokens": 150, "duration_s": 2.0, "run_dir": "x", **extra}


# --------------------------------------------------------------- pure functions


@pytest.mark.parametrize("text,seeds", [("1-5", [1, 2, 3, 4, 5]), ("1,3,7", [1, 3, 7]),
                                        ("1-3,10", [1, 2, 3, 10]), (" 2 , 2,3 ", [2, 3])])
def test_parse_seeds(text, seeds):
    assert parse_seeds(text) == seeds


@pytest.mark.parametrize("text", ["", "5-1", "a"])
def test_parse_seeds_rejects_bad_input(text):
    with pytest.raises(ValueError):
        parse_seeds(text)


def test_delivered_after_skips_attempts_without_code():
    assert delivered_after(["fail", None, "pass"], 1) == "fail"
    assert delivered_after(["fail", None, "pass"], 2) == "fail"  # attempt 2 had no code
    assert delivered_after(["fail", None, "pass"], 3) == "pass"
    assert delivered_after([None], 1) is None and delivered_after([], 3) is None


def test_summary_counts_fixed_broken_and_pass_after_each_attempt():
    rows = [
        row(["pass"], status="dev_passed", cells=20),        # passed at once
        row(["fail", "pass"], status="dev_passed", cells=30),  # fixed by a revision
        row(["pass", "fail", "fail"]),                       # broken by a revision
        row(["fail", "fail", "fail"], cells=99),             # never passed
    ]
    g = summarize_group(rows, max_attempts=3)
    assert (g["runs"], g["oneshot_pass"], g["final_pass"]) == (4, "2/4", "2/4")
    assert (g["fixed"], g["broken"]) == (1, 1)
    assert g["pass_after_attempt"] == {1: "2/4", 2: "2/4", 3: "2/4"}
    assert g["own_checks_passed"] == "2/4"
    assert g["median_cells_passing"] == 25  # only the passing final designs (20 and 30), not the 99
    assert g["statuses"] == {"attempts_exhausted": 2, "dev_passed": 2}


def test_summary_groups_and_markdown():
    rows = [row(["fail", "pass"], strategy="default"), row(["pass"], strategy="tb_timing"),
            row(["fail", "fail"], strategy="default", circuit="alu8", split="heldout")]
    summary = summarize(rows, max_attempts=2)
    assert list(summary["by_strategy"]) == ["default", "tb_timing"]
    assert summary["by_strategy"]["default"]["final_pass"] == "1/2"
    assert list(summary["by_split"]) == ["default / dev", "default / heldout", "tb_timing / dev"]
    assert "alu8 / default" in summary["by_circuit"]
    meta = {"date": "today", "model": "m", "temperature": 0.2, "think": False, "max_total_tokens": 1000,
            "seeds": [1], "strategies": {"default": "prompts/default"}}
    text = render_markdown(summary, meta, max_attempts=2)
    assert "| strategy | runs | one-shot pass | iterative pass | fixed | broken | pass after 1 | pass after 2 |" in text
    assert "| default | 2 | 0/2 | 1/2 | 1 | 0 | 0/2 | 1/2 |" in text


def test_history_without_per_attempt_scores_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="score_every_attempt"):
        row_from_history({"settings": {"score_every_attempt": False}}, "default", 1, "adder", tmp_path)


# --------------------------------------------------------------- real runs (fake model, real EDA tools)

integration = pytest.mark.integration


class Factory:
    """Makes a fresh FakeLLM per run and remembers which seeds it was asked for."""

    def __init__(self, script):
        self.script = script
        self.seeds = []

    def __call__(self, config):
        self.seeds.append(config.llm.seed)
        return FakeLLM(self.script)


@integration
def test_benchmark_runs_every_combination_and_writes_tables(tmp_path):
    factory = Factory([reply(ADDER_NO_CARRY), reply(ADDER)])
    rows = run_benchmark(CONFIG, [get_circuit("adder4")], {"default": DEFAULT_PROMPTS}, [1, 2],
                         tmp_path, factory, max_attempts=3)
    assert factory.seeds == [1, 2]
    assert [(r["seed"], r["oneshot_outcome"], r["final_outcome"]) for r in rows] == [(1, "fail", "pass"),
                                                                                    (2, "fail", "pass")]
    assert rows[0]["attempt_outcomes"] == ["fail", "pass"] and rows[0]["final_cells"] > 0
    assert (tmp_path / "runs" / "adder4" / "default" / "seed2" / "history.json").is_file()
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["by_strategy"]["default"]["fixed"] == 2 and summary["meta"]["seeds"] == [1, 2]
    assert "| default | 2 | 0/2 | 2/2 | 2 | 0 |" in (tmp_path / "summary.md").read_text()
    assert (tmp_path / "rows.csv").read_text().count("\n") == 3  # header + 2 runs


@integration
def test_benchmark_resumes_without_redoing_finished_runs(tmp_path):
    args = (CONFIG, [get_circuit("adder4")], {"default": DEFAULT_PROMPTS}, [1, 2], tmp_path)
    first = run_benchmark(*args, Factory([reply(ADDER)]), max_attempts=2)

    half_done = tmp_path / "runs" / "adder4" / "default" / "seed2"
    (half_done / "history.json").unlink()  # pretend seed 2 was interrupted
    again = Factory([reply(ADDER)])
    second = run_benchmark(*args, again, max_attempts=2)
    assert again.seeds == [2]  # seed 1 was skipped
    assert [r["final_outcome"] for r in second] == [r["final_outcome"] for r in first] == ["pass", "pass"]


@integration
def test_bench_command_with_fake_replies(tmp_path, capsys):
    script = PROJECT_ROOT / "examples" / "scripted_adder4.json"
    code = cli.main(["bench", "--circuits", "adder4", "--seeds", "1-2", "--fake", str(script),
                     "--out", str(tmp_path / "b")])
    out = capsys.readouterr().out
    assert code == 0
    assert "1 circuit(s) x 1 strategy(ies) x 2 seed(s) = 2 runs" in out
    assert "[2/2] adder4 default seed 2: one-shot FAIL -> final PASS" in out
    assert "default: one-shot 0/2, iterative 2/2 (fixed 2, broken 0)" in out

    assert cli.main(["bench", "--circuits", "adder4", "--seeds", "1-2", "--fake", str(script),
                     "--out", str(tmp_path / "b")]) == 0
    assert "already done, skipped" in capsys.readouterr().out


def test_bench_rejects_bad_seeds(capsys):
    assert cli.main(["bench", "--seeds", "9-1"]) == 2
    assert "--seeds" in capsys.readouterr().err
