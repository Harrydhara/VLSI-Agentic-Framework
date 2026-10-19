"""Read a benchmark folder back for the dashboard's charts.

Rows are rebuilt from each run's history.json (as docs/report/build.py does), so a run
that was re-done counts with its latest result. Plain Python apart from the figures,
which need Plotly (the optional ui extra).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from vlsi_agent.bench import row_from_history, summarize_group
from vlsi_agent.runview import RUNS_ROOT

BENCH_ROOT = RUNS_ROOT / "bench"
_RUN_GLOB = "runs/*/*/seed*/history.json"  # runs/<circuit>/<strategy>/seed<k>/

BLUES = ["#a9cdf2", "#4a90d9", "#1f5fa8", "#123e70", "#0a2747"]  # pass after 1, 2, 3... attempts
GREEN, RED, AMBER, TEXT = "#3fa66b", "#e3705d", "#e0b25a", "#8a94a0"


@dataclass(frozen=True)
class BenchView:
    name: str
    rows: list[dict]  # bench.row_from_history rows plus the own_check_counts keys
    strategies: list[str]
    circuits: list[str]
    max_attempts: int
    meta: dict = field(default_factory=dict)  # from summary.json; {} if there is none yet
    skipped: int = 0  # run folders that could not be read (e.g. a run still in progress)

    def pick(self, **wanted) -> list[dict]:
        return [r for r in self.rows if all(r[k] == v for k, v in wanted.items())]

    def group(self, **wanted) -> dict:
        return summarize_group(self.pick(**wanted), self.max_attempts)


def own_check_counts(history: dict) -> dict:
    """Over every scored attempt of a run: how often the agent's own checks agreed with the hidden test."""
    counts = {"correct": 0, "correct_rejected": 0, "wrong": 0, "wrong_accepted": 0}
    for a in history["attempts"]:
        if not a["reference"]:
            continue  # no code in this reply
        if a["reference"]["outcome"] == "pass":
            counts["correct"] += 1
            counts["correct_rejected"] += not a["dev_passed"]
        else:
            counts["wrong"] += 1
            counts["wrong_accepted"] += bool(a["dev_passed"])
    return counts


def list_benchmarks(root: Path = BENCH_ROOT) -> list[str]:
    """Benchmark folders under root that hold at least one finished run, most runs first."""
    if not Path(root).is_dir():
        return []
    counts = {d.name: sum(1 for _ in d.glob(_RUN_GLOB)) for d in Path(root).iterdir() if d.is_dir()}
    return sorted((name for name, n in counts.items() if n), key=lambda name: (-counts[name], name))


def load_benchmark(folder: Path) -> BenchView:
    folder = Path(folder)
    rows, skipped, max_attempts = [], 0, 1
    for path in sorted(folder.glob(_RUN_GLOB)):
        run = path.parent
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
            row = row_from_history(history, run.parent.name, int(run.name.removeprefix("seed")), "", run)
        except (ValueError, KeyError, OSError):
            skipped += 1
            continue
        rows.append({**row, **own_check_counts(history)})
        max_attempts = max(max_attempts, history["settings"]["max_attempts"])
    summary = folder / "summary.json"
    meta = json.loads(summary.read_text(encoding="utf-8")).get("meta", {}) if summary.is_file() else {}
    present = list(dict.fromkeys(r["strategy"] for r in rows))
    strategies = [s for s in meta.get("strategies", {}) if s in present] + sorted(set(present) - set(meta.get("strategies", {})))
    circuits = sorted({r["circuit"] for r in rows})
    return BenchView(folder.name, rows, strategies, circuits, max_attempts, meta, skipped)


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def bench_markdown(view: BenchView) -> str:
    m = view.meta
    parts = [f"**{view.name}** · {_plural(len(view.rows), 'run')} · "
             f"{_plural(len(view.strategies), 'strategy', 'strategies')} × {_plural(len(view.circuits), 'circuit')}"]
    if m:
        parts.append(f"model `{m.get('model')}` · seeds {', '.join(map(str, m.get('seeds', [])))} · "
                     f"up to {m.get('max_attempts')} attempts · {m.get('date')}")
    text = " · ".join(parts)
    text += ("\n\nEach strategy ran on the same circuits and seeds. One-shot and iterative scores come from the same "
             "runs: one-shot is attempt 1's design, iterative is the design handed over at the end.")
    if view.skipped:
        text += f"\n\n{view.skipped} run folder(s) could not be read (still running?) and are left out."
    return text


# --------------------------------------------------------------------------- figures


def _n(count: str) -> int:
    return int(count.split("/")[0])


def _style(fig, title: str, height: int = 340):
    fig.update_layout(
        title={"text": title, "font": {"size": 14}, "x": 0, "xanchor": "left"}, height=height,
        margin={"l": 50, "r": 20, "t": 40, "b": 40}, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"color": TEXT}, legend={"orientation": "h", "y": -0.12, "yanchor": "top", "x": 0},
        bargap=0.25, modebar={"bgcolor": "rgba(0,0,0,0)", "color": TEXT},
    )
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.2)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)", zeroline=False)
    return fig


def pass_after_figure(view: BenchView):
    """Hidden-test passes if the agent had stopped after 1, 2, ... attempts, per strategy."""
    import plotly.graph_objects as go

    fig = go.Figure()
    groups = {s: view.group(strategy=s) for s in view.strategies}
    for k in range(1, view.max_attempts + 1):
        counts = [groups[s]["pass_after_attempt"][k] for s in view.strategies]
        fig.add_bar(name="1 attempt (one-shot)" if k == 1 else f"{k} attempts", x=view.strategies,
                    y=[_n(c) for c in counts], text=[_n(c) for c in counts], customdata=counts,
                    textposition="outside", cliponaxis=False, textfont={"size": 11},
                    marker_color=BLUES[min(k - 1, len(BLUES) - 1)],
                    hovertemplate="%{x}: %{customdata} passed after " + str(k) + " attempt(s)<extra></extra>")
    runs = sorted({g["runs"] for g in groups.values()})
    fig.update_yaxes(title="runs passing" + (f" (of {runs[0]})" if len(runs) == 1 else ""),
                     range=[0, max(runs, default=1) * 1.15])
    return _style(fig, "Hidden-test passes if stopped after k attempts", height=380)


def circuit_heatmap(view: BenchView):
    """Per circuit and strategy: one-shot → final passes, coloured by the final pass rate."""
    import plotly.graph_objects as go

    z, text, hover = [], [], []
    for c in view.circuits:
        z_row, text_row, hover_row = [], [], []
        for s in view.strategies:
            g = view.group(circuit=c, strategy=s)
            if not g["runs"]:
                z_row.append(None)
                text_row.append("")
                hover_row.append("no runs")
                continue
            first, final = _n(g["oneshot_pass"]), _n(g["final_pass"])
            z_row.append(final / g["runs"])
            text_row.append(f"{first} → {final}" if first != final else f"{final}")
            hover_row.append(f"{c} / {s}<br>one-shot {g['oneshot_pass']}, iterative {g['final_pass']}<br>"
                             f"fixed {g['fixed']}, broken {g['broken']}<br>mean {g['mean_tokens']:,} tokens")
        z.append(z_row)
        text.append(text_row)
        hover.append(hover_row)
    fig = go.Figure(go.Heatmap(
        z=z, x=view.strategies, y=view.circuits, text=text, texttemplate="%{text}", hovertext=hover,
        hoverinfo="text", zmin=0, zmax=1, showscale=False, xgap=3, ygap=3,
        colorscale=[[0, RED], [0.5, AMBER], [1, GREEN]], textfont={"color": "#1d2228", "size": 13}))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showgrid=False)
    runs = max((len(view.pick(circuit=c, strategy=s)) for c in view.circuits for s in view.strategies), default=0)
    fig = _style(fig, f"Passes per circuit: one-shot → iterative, of {runs}",
                 height=110 + 48 * len(view.circuits))
    fig.update_layout(margin={"l": 100, "r": 20, "t": 40, "b": 40})
    return fig


def own_checks_figure(view: BenchView):
    """Over all attempts: correct designs the agent's own checks rejected, wrong ones they accepted."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for key, of, label, color in (("correct_rejected", "correct", "correct designs rejected", AMBER),
                                  ("wrong_accepted", "wrong", "wrong designs accepted", RED)):
        hits = [sum(r[key] for r in view.pick(strategy=s)) for s in view.strategies]
        totals = [sum(r[of] for r in view.pick(strategy=s)) for s in view.strategies]
        fig.add_bar(name=label, x=view.strategies, y=[100 * h / t if t else 0 for h, t in zip(hits, totals)],
                    text=[f"{h} of {t}" if t else "none" for h, t in zip(hits, totals)], textposition="outside",
                    cliponaxis=False, textfont={"size": 10}, marker_color=color, hovertemplate="%{x}: %{text} " + label + "<extra></extra>")
    fig.update_yaxes(title="% of scored attempts", range=[0, 115], ticksuffix="%")
    return _style(fig, "Own checks vs the hidden test (all attempts)", height=380)


def fixed_broken_figure(view: BenchView):
    """Runs where revising turned a failing one-shot design into a passing one, or the reverse."""
    import plotly.graph_objects as go

    groups = [view.group(strategy=s) for s in view.strategies]
    fig = go.Figure()
    for key, label, color in (("fixed", "fixed (fail → pass)", GREEN), ("broken", "broken (pass → fail)", RED)):
        values = [g[key] for g in groups]
        fig.add_bar(name=label, x=view.strategies, y=values, text=values, textposition="outside", cliponaxis=False,
                    marker_color=color, hovertemplate="%{x}: %{y} run(s) " + label + "<extra></extra>")
    top = max([g["fixed"] for g in groups] + [g["broken"] for g in groups] + [1])
    fig.update_yaxes(title="runs", range=[0, top * 1.3 + 0.5], dtick=1 if top <= 10 else None)
    return _style(fig, "Revisions that fixed or broke a design", height=380)


def tokens_figure(view: BenchView):
    """Tokens used per run, one box per strategy, every run shown as a point."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for s in view.strategies:
        rows = view.pick(strategy=s)
        fig.add_box(y=[r["total_tokens"] for r in rows], name=s, boxpoints="all", jitter=0.4, pointpos=0,
                    marker={"color": BLUES[1], "size": 5}, line={"color": BLUES[2]}, fillcolor="rgba(74,144,217,0.15)",
                    hovertext=[f"{r['circuit']} seed {r['seed']}: {r['final_outcome'] or 'no design'}, "
                               f"{r['attempts_used']} attempt(s), {r['duration_s']:.0f} s" for r in rows],
                    hoverinfo="y+text")
    fig.update_yaxes(title="tokens per run", rangemode="tozero")
    fig = _style(fig, "Cost: tokens per run")
    fig.update_layout(showlegend=False)
    return fig


CHARTS = (pass_after_figure, circuit_heatmap, own_checks_figure, fixed_broken_figure, tokens_figure)
