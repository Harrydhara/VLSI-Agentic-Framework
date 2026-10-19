import dataclasses
import json

import pytest

from samples import ADDER, ADDER_NO_CARRY, reply
from vlsi_agent.bench import delivered_after, run_benchmark
from vlsi_agent.benchview import (CHARTS, BenchView, bench_markdown, circuit_heatmap, fixed_broken_figure,
                                  list_benchmarks, load_benchmark, own_check_counts, own_checks_figure,
                                  pass_after_figure, tokens_figure)
from vlsi_agent.circuits import get_circuit
from vlsi_agent.config import PROJECT_ROOT, load_config
from vlsi_agent.dashboard import benchmark_charts
from vlsi_agent.llm import FakeLLM

_base = load_config()
CONFIG = dataclasses.replace(_base, tools=dataclasses.replace(_base.tools, sim_timeout_s=2))


def row(outcomes, strategy="default", circuit="alu4", seed=1, correct=0, correct_rejected=0, wrong=0,
        wrong_accepted=0, tokens=150):
    return {"circuit": circuit, "strategy": strategy, "seed": seed, "attempts_used": len(outcomes),
            "attempt_outcomes": outcomes, "oneshot_outcome": delivered_after(outcomes, 1),
            "final_outcome": delivered_after(outcomes, len(outcomes)), "status": "attempts_exhausted",
            "own_checks_passed": False, "oneshot_compiled": True, "final_compiled": True, "oneshot_synth_ok": True,
            "final_synth_ok": True, "final_cells": None, "total_tokens": tokens, "duration_s": 2.0,
            "correct": correct, "correct_rejected": correct_rejected, "wrong": wrong, "wrong_accepted": wrong_accepted}


VIEW = BenchView("demo", [
    row(["fail", "pass"], correct=1, wrong=1, tokens=300),                          # fixed
    row(["pass"], circuit="adder4", correct=1, correct_rejected=1),
    row(["pass", "fail", "fail"], strategy="tb_timing", correct=1, wrong=2, wrong_accepted=1),  # broken
    row(["pass"], strategy="tb_timing", circuit="adder4", correct=1),
], strategies=["default", "tb_timing"], circuits=["adder4", "alu4"], max_attempts=3)


def test_own_check_counts_compare_each_attempt_with_the_hidden_test():
    history = {"attempts": [
        {"reference": {"outcome": "pass"}, "dev_passed": False},   # correct, rejected by own checks
        {"reference": {"outcome": "fail"}, "dev_passed": True},    # wrong, accepted
        {"reference": None, "dev_passed": False},                  # no code: not counted
        {"reference": {"outcome": "pass"}, "dev_passed": True},
    ]}
    assert own_check_counts(history) == {"correct": 2, "correct_rejected": 1, "wrong": 1, "wrong_accepted": 1}


def test_list_benchmarks_most_runs_first(tmp_path):
    for bench, n in (("small", 1), ("big", 3), ("empty", 0)):
        (tmp_path / bench / "runs").mkdir(parents=True)
        for k in range(1, n + 1):
            run = tmp_path / bench / "runs" / "adder4" / "default" / f"seed{k}"
            run.mkdir(parents=True)
            (run / "history.json").write_text("{}")
    assert list_benchmarks(tmp_path) == ["big", "small"]
    assert list_benchmarks(tmp_path / "missing") == []


def test_pass_after_figure_has_one_bar_series_per_attempt():
    pytest.importorskip("plotly")
    fig = pass_after_figure(VIEW)
    assert [t.name for t in fig.data] == ["1 attempt (one-shot)", "2 attempts", "3 attempts"]
    assert list(fig.data[0].customdata) == ["1/2", "2/2"] and list(fig.data[2].customdata) == ["2/2", "1/2"]
    assert [str(t) for t in fig.data[0].text] == ["1", "2"] and fig.layout.yaxis.title.text == "runs passing (of 2)"


def test_circuit_heatmap_shows_one_shot_to_final():
    pytest.importorskip("plotly")
    heat = circuit_heatmap(VIEW).data[0]
    assert list(heat.y) == ["adder4", "alu4"] and list(heat.x) == ["default", "tb_timing"]
    assert [list(r) for r in heat.text] == [["1", "1"], ["0 → 1", "1 → 0"]]
    assert [list(r) for r in heat.z] == [[1.0, 1.0], [1.0, 0.0]]


def test_own_checks_fixed_broken_and_tokens_figures():
    pytest.importorskip("plotly")
    rejected, accepted = own_checks_figure(VIEW).data
    assert list(rejected.text) == ["1 of 2", "0 of 2"] and list(rejected.y) == [50, 0]
    assert list(accepted.text) == ["0 of 1", "1 of 2"]
    fixed, broken = fixed_broken_figure(VIEW).data
    assert list(fixed.y) == [1, 0] and list(broken.y) == [0, 1]
    boxes = tokens_figure(VIEW).data
    assert [b.name for b in boxes] == ["default", "tb_timing"] and list(boxes[0].y) == [300, 150]


def test_markdown_mentions_skipped_runs():
    assert "4 runs · 2 strategies × 2 circuits" in bench_markdown(VIEW)
    assert "1 run folder(s) could not be read" in bench_markdown(dataclasses.replace(VIEW, skipped=1))


def test_benchmark_charts_stay_inside_runs_bench():
    for name in (None, "", "../ui", "../../src", "no-such-benchmark"):
        summary, *figures = benchmark_charts(name)
        assert summary.startswith("Choose a benchmark") and figures == [None] * len(CHARTS)


@pytest.mark.integration
def test_load_a_real_benchmark_folder_and_draw_it(tmp_path, monkeypatch):
    pytest.importorskip("plotly")
    bench = tmp_path / "bench" / "tiny"
    run_benchmark(CONFIG, [get_circuit("adder4")], {"default": PROJECT_ROOT / "prompts" / "default"}, [1, 2],
                  bench, lambda config: FakeLLM([reply(ADDER_NO_CARRY), reply(ADDER)]), max_attempts=3)
    (bench / "runs" / "adder4" / "default" / "seed3").mkdir()
    (bench / "runs" / "adder4" / "default" / "seed3" / "history.json").write_text("{")  # half written
    view = load_benchmark(bench)
    assert (len(view.rows), view.skipped, view.strategies, view.circuits, view.max_attempts) == (
        2, 1, ["default"], ["adder4"], 3)
    g = view.group(strategy="default")
    assert (g["oneshot_pass"], g["final_pass"], g["fixed"]) == ("0/2", "2/2", 2)
    first = view.rows[0]
    assert (first["correct"], first["correct_rejected"], first["wrong"], first["wrong_accepted"]) == (1, 0, 1, 0)
    assert view.meta == json.loads((bench / "summary.json").read_text())["meta"]
    assert list(view.meta["strategies"]) == ["default"]

    import vlsi_agent.dashboard as dashboard
    monkeypatch.setattr(dashboard, "BENCH_ROOT", bench.parent)
    summary, *figures = benchmark_charts("tiny")
    assert summary.startswith("**tiny** · 2 runs · 1 strategy × 1 circuit") and "could not be read" in summary
    assert all(f is not None for f in figures) and len(figures) == len(CHARTS)
