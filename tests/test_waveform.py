import dataclasses

import pytest

from vlsi_agent.circuits import get_circuit
from vlsi_agent.config import load_config
from vlsi_agent.evaluate import reference_test
from vlsi_agent.waveform import (Signal, Waveform, default_window, display_rows, mismatch_times, parse_vcd,
                                 reference_log_now, reference_waveform, waveform_figure)

TOOLS = load_config().tools
TOOLS_FAST_TIMEOUT = dataclasses.replace(TOOLS, sim_timeout_s=2)

# What Icarus writes for a dump window: values listed at $dumpon, then every signal as x at $dumpoff.
VCD = """$date today $end
$version Icarus Verilog $end
$timescale
  1ps
$end
$scope module ref_tb $end
$var reg 1 ! clk $end
$var reg 4 " model [3:0] $end
$scope module dut $end
$var wire 1 ! clk $end
$var reg 4 # count [3:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
#100000
$dumpon
0!
b0 "
bx #
$end
#105000
1!
b1 "
b1 #
#110000
0!
#115000
1!
b10 "
b11 #
#120000
$dumpoff
x!
bx "
bx #
$end
"""


@pytest.fixture
def vcd(tmp_path):
    path = tmp_path / "reference.vcd"
    path.write_text(VCD)
    return path


def test_parse_vcd_reads_scopes_times_and_values(vcd):
    wave = parse_vcd(vcd)
    assert set(wave.signals) == {"ref_tb.clk", "ref_tb.model", "ref_tb.dut.clk", "ref_tb.dut.count"}
    assert wave.signals["ref_tb.clk"].changes == [(100, "0"), (105, "1"), (110, "0"), (115, "1")]
    assert wave.signals["ref_tb.dut.clk"].changes == wave.signals["ref_tb.clk"].changes  # same id code
    assert wave.signals["ref_tb.dut.count"].changes == [(100, "x"), (105, "1"), (115, "11")]
    assert wave.signals["ref_tb.model"].width == 4
    assert wave.signals["ref_tb.model"].value_at(112) == "1"  # the $dumpoff x values are not changes


def test_parse_vcd_keeps_only_the_window(vcd):
    wave = parse_vcd(vcd, 107, 112)
    assert wave.signals["ref_tb.clk"].changes == [(107, "1"), (110, "0")]
    assert wave.signals["ref_tb.dut.count"].changes == [(107, "1")]


def _wave(*names):
    return Waveform({n: Signal(n, 1, [(0, "0")]) for n in names}, 0, 10)


def test_display_rows_put_each_expected_value_under_its_output():
    counter = _wave("ref_tb.dut.clk", "ref_tb.dut.rst", "ref_tb.dut.en", "ref_tb.dut.count", "ref_tb.model")
    assert [label for label, _ in display_rows(counter, get_circuit("counter4").spec)] == [
        "clk", "rst", "en", "count", "expected count"]

    outputs = ("y", "zero", "carry", "overflow", "negative")
    alu = _wave(*[f"ref_tb.dut.{p}" for p in ("a", "b", "op", *outputs)],
                *[f"ref_tb.{e}" for e in ("ey", "ez", "ec", "ev", "en")])
    labels = [label for label, _ in display_rows(alu, get_circuit("alu4").spec)]
    for out in outputs:
        assert labels[labels.index(out) + 1] == f"expected {out}"

    updown = _wave("ref_tb.dut.en", "ref_tb.en", "ref_tb.dut.count", "ref_tb.model")  # en is an input here
    assert [label for label, _ in display_rows(updown, get_circuit("updown8").spec)] == [
        "en", "count", "expected count"]

    adder = _wave("ref_tb.dut.a", "ref_tb.dut.sum", "ref_tb.dut.cout", "ref_tb.expected")  # {cout, sum}
    assert [label for label, _ in display_rows(adder, get_circuit("adder4").spec)] == ["a", "sum", "cout", "expected"]


def test_default_window_opens_before_the_first_mismatch():
    log = ("MISMATCH at 236 ns: rst=0 en=0 expected count=4, got 5\n"
           "MISMATCH at 246 ns: rst=0 en=0 expected count=4, got 6\nRESULT abc: 1/3 passed\n")
    assert mismatch_times(log) == [236, 246]
    assert default_window(get_circuit("counter4").spec, log) == (136, 400)
    assert default_window(get_circuit("counter4").spec, "RESULT abc: 3/3 passed") == (0, 400)
    assert default_window(get_circuit("adder4").spec, "MISMATCH at 32 ns, a=1 b=15 cin=0: ...") == (16, 64)
    assert mismatch_times("MISMATCH a=1 b=15 cin=0: ...") == []  # logs saved before the time was printed


def test_figure_has_a_row_per_signal_and_marks_mismatches(vcd):
    pytest.importorskip("plotly")
    wave = parse_vcd(vcd, 100, 120)
    rows = [("clk", wave.signals["ref_tb.dut.clk"]), ("count", wave.signals["ref_tb.dut.count"]),
            ("expected count", wave.signals["ref_tb.model"])]
    fig = waveform_figure(rows, 100, 120, [105, 130])
    assert list(fig.layout.yaxis.ticktext) == ["clk", "count", "expected count"]
    assert len(fig.layout.shapes) == 1  # 130 ns is outside the window
    labels = [t for trace in fig.data if trace.mode == "text" for t in trace.text]
    assert {"x", "1", "3", "0", "2"} <= set(labels)


integration = pytest.mark.integration


@integration
def test_windowed_dump_is_small_and_does_not_change_the_score(tmp_path):
    circuit = get_circuit("counter4")
    design = circuit.fixture_path("ignores_enable.v")
    full = reference_test(TOOLS, circuit, design, tmp_path / "full", waveform=True)
    window = reference_test(TOOLS, circuit, design, tmp_path / "window", vcd_window=(100, 140))
    assert (full.outcome, full.passed) == (window.outcome, window.passed) == ("fail", 54)
    small, large = ((tmp_path / d / "reference.vcd").stat().st_size for d in ("window", "full"))
    assert small * 5 < large
    times = {t for s in parse_vcd(tmp_path / "window" / "reference.vcd").signals.values() for t, _ in s.changes}
    assert min(times) >= 100 and max(times) <= 140


@integration
@pytest.mark.parametrize("name,fixture,window,dut_port,expected_value,got_value", [
    ("counter4", "ignores_enable.v", (136, 400), "count", 4, 5),  # counts while en is 0
    ("adder4", "no_carry_out.v", (16, 64), "cout", 16, 0),        # 1 + 15 = 16 needs the carry out
])
def test_waveform_shows_the_first_mismatch(name, fixture, window, dut_port, expected_value, got_value):
    circuit = get_circuit(name)
    design = circuit.fixture_path(fixture)
    log = reference_log_now(TOOLS, circuit, design)
    assert default_window(circuit.spec, log) == window
    w = reference_waveform(TOOLS, circuit, design, *window)
    assert w.outcome == "fail" and w.wave is not None
    t = mismatch_times(w.log)[0]
    rows = dict(display_rows(w.wave, circuit.spec))
    expected = rows[f"expected {dut_port}" if f"expected {dut_port}" in rows else "expected"]
    assert int(expected.value_at(t), 2) == expected_value
    assert int(rows[dut_port].value_at(t), 2) == got_value


@integration
def test_no_waveform_without_a_working_simulation():
    adder = get_circuit("adder4")
    broken = reference_waveform(TOOLS, adder, adder.fixture_path("wrong_port_name.v"), 0, 64)
    assert broken.wave is None and broken.outcome == "compile_error"
    mux = get_circuit("mux2")
    hung = reference_waveform(TOOLS_FAST_TIMEOUT, mux, mux.fixture_path("loop_without_increment.v"), 0, 64)
    assert hung.outcome == "timeout"  # the dump may be cut short; either way there is no crash
