"""Benchmark: run the agent over circuits x strategies x seeds and summarize the results.

Every run is an iterative run with every attempt scored by the hidden reference test
afterwards. Attempt 1 is exactly what a one-shot run would produce, so each run gives a
paired comparison: the one-shot score (attempt 1) and the iterative score (the final
design), from the same first reply. It also shows when a revision fixed a design and
when it broke one.

A benchmark folder holds:

    runs/<circuit>/<strategy>/seed<k>/   one agent run each (see agent.py)
    rows.csv                             one row per run
    summary.json, summary.md             the tables

Rows are always rebuilt from each run's history.json, so an interrupted benchmark can be
resumed: runs that already have a history.json are skipped, and a half-finished run
folder is deleted and run again.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import shutil
import statistics
import time
from pathlib import Path
from typing import Callable

from vlsi_agent import __version__
from vlsi_agent.agent import run_agent
from vlsi_agent.circuits import Circuit
from vlsi_agent.config import PROJECT_ROOT, Config
from vlsi_agent.llm import LLM

LLMFactory = Callable[[Config], LLM]  # builds a model client for one run (the seed is in the config)

ROW_FIELDS = [
    "circuit", "split", "family", "strategy", "seed", "status", "attempts_used",
    "oneshot_outcome", "final_outcome", "attempt_outcomes",
    "oneshot_compiled", "final_compiled", "oneshot_synth_ok", "final_synth_ok",
    "oneshot_cells", "final_cells", "final_latches", "own_checks_passed",
    "prompt_tokens", "output_tokens", "total_tokens", "duration_s", "run_dir",
]


def parse_seeds(text: str) -> list[int]:
    """'1-5' -> [1, 2, 3, 4, 5]; '1,3,7' -> [1, 3, 7]; '1-3,10' -> [1, 2, 3, 10]."""
    seeds: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            low, high = (int(x) for x in part.split("-", 1))
            if high < low:
                raise ValueError(f"bad seed range {part!r}")
            seeds.extend(range(low, high + 1))
        else:
            seeds.append(int(part))
    if not seeds:
        raise ValueError("no seeds given")
    return list(dict.fromkeys(seeds))


def delivered_after(outcomes: list[str | None], k: int) -> str | None:
    """Reference outcome of the design the agent would hand over if stopped after k attempts:
    the last attempt (of the first k) that produced code. None means no design at all."""
    for outcome in reversed(outcomes[:k]):
        if outcome is not None:
            return outcome
    return None


def row_from_history(history: dict, strategy: str, seed: int, family: str, run_dir: Path) -> dict:
    if not history["settings"].get("score_every_attempt"):
        raise ValueError(f"{run_dir}: run was not made with score_every_attempt; cannot get the one-shot score")
    attempts = history["attempts"]
    outcomes = [a["reference"]["outcome"] if a["reference"] else None for a in attempts]
    first = attempts[0] if attempts else None
    final = next((a for a in attempts if a["number"] == history["final_attempt"]), attempts[-1] if attempts else None)

    def check(attempt: dict | None, key: str):
        return attempt["checks"].get(key) if attempt else None

    return {
        "circuit": history["circuit"], "split": history["split"], "family": family,
        "strategy": strategy, "seed": seed, "status": history["status"], "attempts_used": len(attempts),
        "oneshot_outcome": delivered_after(outcomes, 1), "final_outcome": delivered_after(outcomes, len(outcomes)),
        "attempt_outcomes": outcomes,
        "oneshot_compiled": bool(check(first, "compiled")), "final_compiled": bool(check(final, "compiled")),
        "oneshot_synth_ok": bool(check(first, "synth_ok")), "final_synth_ok": bool(check(final, "synth_ok")),
        "oneshot_cells": check(first, "cells"), "final_cells": check(final, "cells"),
        "final_latches": check(final, "latches"),
        "own_checks_passed": history["status"] == "dev_passed",
        "prompt_tokens": history["tokens"]["prompt"], "output_tokens": history["tokens"]["output"],
        "total_tokens": history["tokens"]["total"], "duration_s": history["duration_s"],
        "run_dir": str(run_dir),
    }


def run_benchmark(config: Config, circuits: list[Circuit], strategies: dict[str, Path], seeds: list[int],
                  out_dir: Path, make_llm: LLMFactory, *, max_attempts: int | None = None,
                  max_total_tokens: int | None = None,
                  on_progress: Callable[[str], None] | None = None) -> list[dict]:
    """Run (or resume) every circuit x strategy x seed combination and write the results."""
    max_attempts = config.agent.max_attempts if max_attempts is None else max_attempts
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(c, s, seed) for c in circuits for s in strategies for seed in seeds]
    rows = []
    for index, (circuit, strategy, seed) in enumerate(jobs, 1):
        run_dir = out_dir / "runs" / circuit.name / strategy / f"seed{seed}"
        history_file = run_dir / "history.json"
        label = f"[{index}/{len(jobs)}] {circuit.name} {strategy} seed {seed}"
        if history_file.is_file():
            note = "already done, skipped"
        else:
            if run_dir.exists():  # left over from an interrupted run
                shutil.rmtree(run_dir)
            run_config = dataclasses.replace(config, llm=dataclasses.replace(config.llm, seed=seed))
            run_agent(run_config, circuit, make_llm(run_config), run_dir, max_attempts=max_attempts,
                      max_total_tokens=max_total_tokens, prompts_dir=strategies[strategy], score_every_attempt=True)
            note = None
        row = row_from_history(json.loads(history_file.read_text()), strategy, seed, circuit.spec.family, run_dir)
        rows.append(row)
        if on_progress:
            text = note or (f"one-shot {str(row['oneshot_outcome']).upper()} -> final {str(row['final_outcome']).upper()} "
                            f"({row['attempts_used']} attempts, {row['total_tokens']} tokens, {row['duration_s']:.0f} s)")
            on_progress(f"{label}: {text}")

    meta = {
        "tool_version": __version__, "date": time.strftime("%Y-%m-%d %H:%M"), "model": config.llm.model,
        "temperature": config.llm.temperature, "think": config.llm.think, "max_attempts": max_attempts,
        "max_total_tokens": config.agent.max_total_tokens if max_total_tokens is None else max_total_tokens,
        "seeds": seeds, "strategies": {name: _short(path) for name, path in strategies.items()},
        "circuits": [c.name for c in circuits],
    }
    write_results(out_dir, rows, meta, max_attempts)
    return rows


def _short(path: Path) -> str:
    """Show a path relative to the project folder when it is inside it."""
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- summary


def _count(rows: list[dict], test: Callable[[dict], bool]) -> str:
    return f"{sum(1 for r in rows if test(r))}/{len(rows)}"


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 1) if values else None


def _median(values: list[float]) -> float | int | None:
    if not values:
        return None
    m = statistics.median(values)
    return int(m) if float(m).is_integer() else m  # 134, not 134.0


def summarize_group(rows: list[dict], max_attempts: int) -> dict:
    """Counts like '3/5' rather than percentages: with a handful of runs, a percentage
    suggests more precision than there is."""
    passing_final_cells = [r["final_cells"] for r in rows if r["final_outcome"] == "pass" and r["final_cells"]]
    return {
        "runs": len(rows),
        "oneshot_pass": _count(rows, lambda r: r["oneshot_outcome"] == "pass"),
        "final_pass": _count(rows, lambda r: r["final_outcome"] == "pass"),
        "pass_after_attempt": {k: _count(rows, lambda r, k=k: delivered_after(r["attempt_outcomes"], k) == "pass")
                               for k in range(1, max_attempts + 1)},
        "fixed": sum(1 for r in rows if r["oneshot_outcome"] != "pass" and r["final_outcome"] == "pass"),
        "broken": sum(1 for r in rows if r["oneshot_outcome"] == "pass" and r["final_outcome"] != "pass"),
        "own_checks_passed": _count(rows, lambda r: r["own_checks_passed"]),
        "oneshot_compiled": _count(rows, lambda r: r["oneshot_compiled"]),
        "final_compiled": _count(rows, lambda r: r["final_compiled"]),
        "oneshot_synth_ok": _count(rows, lambda r: r["oneshot_synth_ok"]),
        "final_synth_ok": _count(rows, lambda r: r["final_synth_ok"]),
        "mean_attempts": _mean([r["attempts_used"] for r in rows]),
        "mean_tokens": _mean([r["total_tokens"] for r in rows]),
        "mean_seconds": _mean([r["duration_s"] for r in rows]),
        "median_cells_passing": _median(passing_final_cells),
        "statuses": {s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})},
    }


def summarize(rows: list[dict], max_attempts: int) -> dict:
    strategies = list(dict.fromkeys(r["strategy"] for r in rows))
    circuits = list(dict.fromkeys(r["circuit"] for r in rows))
    splits = list(dict.fromkeys(r["split"] for r in rows))

    def pick(**wanted) -> list[dict]:
        return [r for r in rows if all(r[k] == v for k, v in wanted.items())]

    return {
        "by_strategy": {s: summarize_group(pick(strategy=s), max_attempts) for s in strategies},
        "by_split": {f"{s} / {sp}": summarize_group(pick(strategy=s, split=sp), max_attempts)
                     for s in strategies for sp in splits if pick(strategy=s, split=sp)},
        "by_circuit": {f"{c} / {s}": summarize_group(pick(circuit=c, strategy=s), max_attempts)
                       for c in circuits for s in strategies if pick(circuit=c, strategy=s)},
    }


def _table(headers: list[str], body: list[list]) -> str:
    cells = [[("" if v is None else str(v)) for v in row] for row in body]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in cells]
    return "\n".join(lines)


def render_markdown(summary: dict, meta: dict, max_attempts: int) -> str:
    attempt_cols = [f"pass after {k}" for k in range(1, max_attempts + 1)]

    def overview(groups: dict, first_header: str) -> str:
        headers = [first_header, "runs", "one-shot pass", "iterative pass", "fixed", "broken",
                   *attempt_cols, "own checks passed", "mean attempts", "mean tokens", "mean s"]
        body = [[name, g["runs"], g["oneshot_pass"], g["final_pass"], g["fixed"], g["broken"],
                 *g["pass_after_attempt"].values(), g["own_checks_passed"], g["mean_attempts"],
                 g["mean_tokens"], g["mean_seconds"]] for name, g in groups.items()]
        return _table(headers, body)

    tools_table = _table(
        ["circuit / strategy", "compiled (one-shot -> final)", "synthesized (one-shot -> final)",
         "median cells (passing final designs)"],
        [[name, f"{g['oneshot_compiled']} -> {g['final_compiled']}",
          f"{g['oneshot_synth_ok']} -> {g['final_synth_ok']}", g["median_cells_passing"]]
         for name, g in summary["by_circuit"].items()])

    return "\n".join([
        "# Benchmark results",
        "",
        f"{meta['date']} · model `{meta['model']}` (temperature {meta['temperature']}, think {meta['think']}) · "
        f"up to {max_attempts} attempts · budget {meta['max_total_tokens']} tokens · seeds {meta['seeds']}",
        "",
        "Strategies: " + ", ".join(f"`{n}` = {p}" for n, p in meta["strategies"].items()),
        "",
        "**One-shot** is the hidden reference result for attempt 1; **iterative** is the result for the final "
        "design. Both come from the same runs (paired). **fixed** = one-shot failed but the final design passed; "
        "**broken** = one-shot passed but a revision made it fail. **Own checks passed** = the agent's own "
        "development checks accepted its design. Counts are shown as passed/runs.",
        "",
        "## By strategy",
        "",
        overview(summary["by_strategy"], "strategy"),
        "",
        "## By split",
        "",
        overview(summary["by_split"], "strategy / split"),
        "",
        "## By circuit",
        "",
        overview(summary["by_circuit"], "circuit / strategy"),
        "",
        "## Compile, synthesis and area",
        "",
        "Cell counts are compared only for designs that pass the reference test, all synthesized with the "
        "same Yosys `synth` flow. They are a rough area proxy, not real power, area or timing.",
        "",
        tools_table,
        "",
    ])


def write_results(out_dir: Path, rows: list[dict], meta: dict, max_attempts: int) -> dict:
    with (out_dir / "rows.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "attempt_outcomes": "|".join(o or "no_code" for o in row["attempt_outcomes"])})
    summary = summarize(rows, max_attempts)
    (out_dir / "summary.json").write_text(json.dumps({"meta": meta, **summary}, indent=2) + "\n")
    (out_dir / "summary.md").write_text(render_markdown(summary, meta, max_attempts))
    return summary
