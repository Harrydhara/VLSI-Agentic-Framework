"""Waveforms: run a design through its reference test with a VCD dump, read the VCD, plot it.

The reference tests dump only a requested time window (+vcd_from / +vcd_until), so even
the exhaustive ALU tests give a small file. Parsing is plain Python; only the figure
needs Plotly (the optional ui extra).
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from vlsi_agent.circuits import Circuit, Spec
from vlsi_agent.config import ToolsConfig
from vlsi_agent.evaluate import LOG_NAME, reference_test

VCD_NAME = "reference.vcd"
_UNITS_NS = {"s": 1e9, "ms": 1e6, "us": 1e3, "ns": 1.0, "ps": 1e-3, "fs": 1e-6}
CLOCKED_WINDOW_NS = 400
COMBINATIONAL_WINDOW_NS = 64
MAX_WINDOW_NS = 5000

# Reference-test signals holding the expected value, and the output each is compared with
# (None: the circuit's only output, or all outputs together, like adder's {cout, sum}).
EXPECTED_SIGNALS = {"expected": None, "model": None,
                    "ey": "y", "ez": "zero", "ec": "carry", "ev": "overflow", "en": "negative"}


@dataclass
class Signal:
    name: str  # hierarchical, e.g. "ref_tb.dut.count"
    width: int
    changes: list[tuple[float, str]] = field(default_factory=list)  # (time in ns, value), in time order

    def value_at(self, t: float) -> str | None:
        value = None
        for time, v in self.changes:
            if time > t:
                break
            value = v
        return value


@dataclass
class Waveform:
    signals: dict[str, Signal]
    start_ns: float
    end_ns: float


def _timescale_ns(tokens: list[str]) -> float:
    text = "".join(tokens)
    m = re.fullmatch(r"(\d+)(s|ms|us|ns|ps|fs)", text)
    if not m:
        raise ValueError(f"cannot read VCD timescale {text!r}")
    return int(m.group(1)) * _UNITS_NS[m.group(2)]


def parse_vcd(path: Path, start_ns: float = 0.0, end_ns: float = float("inf")) -> Waveform:
    """Read a VCD file, keeping value changes up to end_ns (and the values in force at start_ns)."""
    tokens = Path(path).read_text(encoding="utf-8", errors="replace").split()
    scale, scopes, by_id, signals = 1.0, [], {}, {}
    i, now = 0, 0.0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "$timescale":
            j = tokens.index("$end", i)
            scale = _timescale_ns(tokens[i + 1:j])
            i = j + 1
        elif tok == "$scope":
            scopes.append(tokens[i + 2])
            i = tokens.index("$end", i) + 1
        elif tok == "$upscope":
            scopes.pop()
            i = tokens.index("$end", i) + 1
        elif tok == "$var":
            width, code, ref = int(tokens[i + 2]), tokens[i + 3], tokens[i + 4]
            name = ".".join(scopes + [ref])
            signals[name] = Signal(name, width)
            by_id.setdefault(code, []).append(signals[name])
            i = tokens.index("$end", i) + 1
        elif tok in ("$date", "$version", "$comment", "$dumpoff"):  # $dumpoff lists every signal as x: skip it
            i = tokens.index("$end", i) + 1
        elif tok.startswith("$"):  # $dumpvars, $dumpon, $dumpall, $enddefinitions and their $end
            i += 1
        elif tok.startswith("#"):
            now = int(tok[1:]) * scale
            if now > end_ns:
                break
            i += 1
        elif tok[0] in "bBrR":
            value, code = tok[1:].lower(), tokens[i + 1]
            _record(by_id.get(code, []), now, value)
            i += 2
        else:  # scalar change: value immediately followed by the id code, e.g. "1!" or "x#"
            _record(by_id.get(tok[1:], []), now, tok[0].lower())
            i += 1
    for s in signals.values():  # keep the value in force at start_ns, then the changes inside the window
        before = [c for c in s.changes if c[0] <= start_ns]
        s.changes = ([(start_ns, before[-1][1])] if before else []) + [c for c in s.changes if c[0] > start_ns]
    return Waveform(signals, start_ns, end_ns)


def _record(targets: list[Signal], now: float, value: str) -> None:
    for s in targets:
        if s.changes and s.changes[-1][0] == now:
            s.changes[-1] = (now, value)
        elif not s.changes or s.changes[-1][1] != value:
            s.changes.append((now, value))


# --------------------------------------------------------------------------- what to show


def display_rows(wave: Waveform, spec: Spec, top: str = "ref_tb") -> list[tuple[str, Signal]]:
    """The circuit's inputs, then each output followed by the test's expected value for it."""
    def port(name: str) -> Signal | None:
        return wave.signals.get(f"{top}.dut.{name}") or wave.signals.get(f"{top}.{name}")

    outputs = [p.name for p in spec.ports if p.direction == "output"]
    expected: dict[str | None, tuple[str, Signal]] = {}
    for name, target in EXPECTED_SIGNALS.items():
        sig = wave.signals.get(f"{top}.{name}")
        if sig is None or (target is not None and target not in outputs):
            continue  # not in this test (updown8's en is an input, not an expected flag)
        if target is None and len(outputs) == 1:
            target = outputs[0]
        expected[target] = (f"expected {target}" if target else "expected", sig)

    rows = [(p.name, s) for p in spec.ports if p.direction == "input" and (s := port(p.name))]
    for name in outputs:
        if s := port(name):
            rows.append((name, s))
        if name in expected:
            rows.append(expected.pop(name))
    return rows + list(expected.values())


def mismatch_times(reference_log: str) -> list[int]:
    """When the reference test reported mismatches ("MISMATCH at <t> ns..."; it shows the first 5)."""
    return [int(t) for t in re.findall(r"^MISMATCH at (\d+) ns", reference_log, re.MULTILINE)]


def is_clocked(spec: Spec) -> bool:
    return any(p.name in ("clk", "clock") for p in spec.ports)


def default_window(spec: Spec, reference_log: str) -> tuple[int, int]:
    """(start, length) in ns: opening a little before the first mismatch, or at 0 if there was none."""
    length = CLOCKED_WINDOW_NS if is_clocked(spec) else COMBINATIONAL_WINDOW_NS
    times = mismatch_times(reference_log)
    return (max(0, times[0] - length // 4) if times else 0), length


# --------------------------------------------------------------------------- running the test


@dataclass
class WaveRun:
    wave: Waveform | None  # None when no usable dump was written (e.g. the design did not compile)
    outcome: str  # the reference test's outcome for this run, one of circuits.OUTCOMES
    detail: str
    log: str  # the reference test's output, with the MISMATCH lines


def reference_waveform(tools: ToolsConfig, circuit: Circuit, design_file: Path, start_ns: int,
                       length_ns: int) -> WaveRun:
    """Run the design through its reference test, dumping [start, start + length] ns."""
    end_ns = start_ns + length_ns
    with tempfile.TemporaryDirectory() as tmp:
        result = reference_test(tools, circuit, design_file, Path(tmp), vcd_window=(start_ns, end_ns))
        log = (Path(tmp) / LOG_NAME).read_text(encoding="utf-8", errors="replace")
        wave = None
        if (Path(tmp) / VCD_NAME).is_file():
            try:
                wave = parse_vcd(Path(tmp) / VCD_NAME, start_ns, end_ns)
            except (ValueError, IndexError):  # a dump cut short, e.g. by the simulation timeout
                wave = None
    return WaveRun(wave, result.outcome, result.detail, log)


def reference_log_now(tools: ToolsConfig, circuit: Circuit, design_file: Path) -> str:
    """The reference test's output for a design, run now without a dump."""
    with tempfile.TemporaryDirectory() as tmp:
        reference_test(tools, circuit, design_file, Path(tmp))
        return (Path(tmp) / LOG_NAME).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- figure


def _segments(sig: Signal, t0: float, t1: float) -> list[tuple[float, float, str]]:
    """(start, end, value) for each stretch of a signal inside [t0, t1]."""
    points = [c for c in sig.changes if c[0] < t1]
    ends = [t for t, _ in points[1:]] + [t1]
    return [(max(t, t0), e, v) for (t, v), e in zip(points, ends) if e > t0]


def _bus_label(value: str) -> str:
    return str(int(value, 2)) if value and set(value) <= {"0", "1"} else "x"


def waveform_figure(rows: list[tuple[str, Signal]], t0: float, t1: float, mismatches_ns=()):
    """One row per signal: 1-bit signals as steps, buses as bands labelled in decimal, x in red,
    and a dashed line at each reported mismatch inside the window."""
    import plotly.graph_objects as go

    line, unknown, text = "#4a90d9", "#e3705d", "#8a94a0"
    fig = go.Figure()
    ticks = []
    for k, (label, sig) in enumerate(rows):
        base = -1.5 * k
        ticks.append((base + 0.45, label))
        segs = _segments(sig, t0, t1)
        if sig.width == 1:
            xs, ys, bad_x, bad_y = [], [], [], []
            for s, e, v in segs:
                level = {"1": 0.9, "0": 0.0}.get(v)
                if level is None:  # x or z
                    bad_x += [s, e, None]
                    bad_y += [base + 0.45] * 2 + [None]
                    level = 0.45
                xs += [s, e]
                ys += [base + level] * 2
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line={"color": line, "width": 2}, hoverinfo="skip"))
            if bad_x:
                fig.add_trace(go.Scatter(x=bad_x, y=bad_y, mode="lines", line={"color": unknown, "width": 3},
                                         hoverinfo="skip"))
        else:
            d = (t1 - t0) / 400  # the slant at each value change
            xs, ys, bad_x, bad_y, mids, labels, hovers = [], [], [], [], [], [], []
            for s, e, v in segs:
                edge = min(d, (e - s) / 2)
                shape_x = [s, s + edge, e - edge, e, e - edge, s + edge, s, None]
                shape_y = [base + 0.45, base + 0.9, base + 0.9, base + 0.45, base, base, base + 0.45, None]
                shown = _bus_label(v)
                if shown == "x":
                    bad_x += shape_x
                    bad_y += shape_y
                else:
                    xs += shape_x
                    ys += shape_y
                mids.append((s + e) / 2)
                labels.append(shown if e - s >= (t1 - t0) / 40 else "")  # no room for a label in short stretches
                hovers.append(f"{label} = {shown} ({sig.width}'b{v})<br>{s:g} to {e:g} ns")
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line={"color": line, "width": 1.5}, hoverinfo="skip"))
            if bad_x:
                fig.add_trace(go.Scatter(x=bad_x, y=bad_y, mode="lines", line={"color": unknown, "width": 2},
                                         hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=mids, y=[base + 0.45] * len(mids), mode="text", text=labels,
                                     textfont={"size": 11, "color": text}, hovertext=hovers, hoverinfo="text"))
    inside = [t for t in mismatches_ns if t0 <= t <= t1]
    for t in inside:
        fig.add_vline(x=t, line_dash="dash", line_color=unknown, line_width=1)
    if inside:  # one label, above the rows
        fig.add_annotation(x=inside[0], y=1, xref="x", yref="paper", yanchor="bottom", xanchor="left",
                           text="mismatch", showarrow=False, font={"color": unknown, "size": 11})
    fig.update_layout(
        height=80 + 42 * len(rows), margin={"l": 130, "r": 40, "t": 30, "b": 40}, showlegend=False,
        modebar={"orientation": "v", "bgcolor": "rgba(0,0,0,0)", "color": text},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font={"color": text},
        xaxis={"title": "time (ns)", "range": [t0, t1], "gridcolor": "rgba(128,128,128,0.25)", "zeroline": False},
        yaxis={"tickvals": [t for t, _ in ticks], "ticktext": [l for _, l in ticks], "showgrid": False,
               "zeroline": False, "range": [-1.5 * len(rows) + 0.3, 1.2], "fixedrange": True},
    )
    return fig
