import dataclasses
import shutil
from pathlib import Path

import pytest

from vlsi_agent import circuits
from vlsi_agent.circuits import CircuitError, get_circuit, load_circuit, load_circuits
from vlsi_agent.config import load_config
from vlsi_agent.evaluate import reference_test

TOOLS = dataclasses.replace(load_config().tools, sim_timeout_s=2)
ALL = load_circuits()
FIXTURE_CASES = [(c, name, expected) for c in ALL for name, expected in c.fixtures.items()]


def copy_adder(tmp_path: Path) -> Path:
    dest = tmp_path / "adder4"
    shutil.copytree(circuits.CIRCUITS_DIR / "adder4", dest)
    return dest


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new, 1))


# --------------------------------------------------------------- loading and validation


@pytest.mark.parametrize("split", circuits.SPLITS)
def test_each_split_covers_every_family(split):
    assert {c.spec.family for c in load_circuits(split=split)} >= {"adder", "mux", "counter"}


def test_circuits_are_sorted_and_split_apart():
    assert [c.name for c in ALL] == sorted(c.name for c in ALL)
    dev = {c.name for c in load_circuits(split="dev")}
    heldout = {c.name for c in load_circuits(split="heldout")}
    assert dev and heldout and not dev & heldout
    assert dev | heldout == {c.name for c in ALL}


def test_interface_is_rendered_from_the_ports():
    assert get_circuit("adder4").spec.interface() == (
        "module adder4(\n"
        "    input  [3:0] a,\n"
        "    input  [3:0] b,\n"
        "    input        cin,\n"
        "    output [3:0] sum,\n"
        "    output       cout\n"
        ");"
    )


def test_split_filter():
    assert {c.spec.split for c in load_circuits(split="dev")} == {"dev"}
    with pytest.raises(ValueError, match="split must be one of"):
        load_circuits(split="test")


def test_unknown_circuit_lists_the_known_ones():
    with pytest.raises(CircuitError, match=r"unknown circuit 'adder99' \(known: .*adder4"):
        get_circuit("adder99")


def test_copied_circuit_loads(tmp_path):
    assert load_circuit(copy_adder(tmp_path)).name == "adder4"


@pytest.mark.parametrize("old,new,message", [
    ('split = "dev"', 'split = "test"', "split must be one of"),
    ('name = "adder4"', 'name = "adder5"', "must match its folder"),
    ('family = "adder"', 'family = "adder"\ncolor = "red"', r"unknown key\(s\): color"),
    ('top = "adder4"', 'top = "4adder"', "invalid top module name"),
    ('direction = "output"\nwidth = 1', 'direction = "inout"\nwidth = 1', "direction must be one of"),
    ('name = "cin"\ndirection = "input"\nwidth = 1', 'name = "cin"\ndirection = "input"\nwidth = 0',
     "width must be a positive integer"),
    ('name = "cin"', 'name = "a"', r"duplicate port name\(s\): a"),
])
def test_bad_spec_is_rejected(tmp_path, old, new, message):
    root = copy_adder(tmp_path)
    edit(root / "spec.toml", old, new)
    with pytest.raises(CircuitError, match=message):
        load_circuit(root)


@pytest.mark.parametrize("old,new,message", [
    ('"ignores_cin.v" = "fail"', '"ghost.v" = "fail"', "fixture ghost.v not found"),
    ('"ignores_cin.v" = "fail"', '"ignores_cin.v" = "maybe"', "must be one of"),
    ('"correct.v" = "pass"\n"correct_ripple.v" = "pass"', '"correct.v" = "fail"\n"correct_ripple.v" = "fail"',
     "at least one fixture expected to pass"),
])
def test_bad_fixture_list_is_rejected(tmp_path, old, new, message):
    root = copy_adder(tmp_path)
    edit(root / "fixtures" / "expected.toml", old, new)
    with pytest.raises(CircuitError, match=message):
        load_circuit(root)


def test_missing_reference_test_is_rejected(tmp_path):
    root = copy_adder(tmp_path)
    (root / "reference_tb.v").unlink()
    with pytest.raises(CircuitError, match="missing file: .*reference_tb.v"):
        load_circuit(root)


def test_reference_test_without_token_placeholder_is_rejected(tmp_path):
    root = copy_adder(tmp_path)
    edit(root / "reference_tb.v", "RESULT __RESULT_TOKEN__:", "RESULT:")
    circuit = load_circuit(root)
    with pytest.raises(CircuitError, match="__RESULT_TOKEN__"):
        reference_test(TOOLS, circuit, circuit.fixture_path("correct.v"), tmp_path / "run")


# --------------------------------------------------------------- real reference runs


@pytest.mark.integration
@pytest.mark.parametrize("circuit,fixture,expected", FIXTURE_CASES,
                         ids=[f"{c.name}/{f}" for c, f, _ in FIXTURE_CASES])
def test_fixture_gets_its_expected_outcome(tmp_path, circuit, fixture, expected):
    result = reference_test(TOOLS, circuit, circuit.fixture_path(fixture), tmp_path)
    assert result.outcome == expected, result.detail


@pytest.mark.integration
def test_design_cannot_fake_a_pass(tmp_path):
    # Wrong logic plus test code that prints passing RESULT lines (even one with the
    # placeholder name) and ends the simulation before the real test can report.
    fake = tmp_path / "fake.v"
    fake.write_text(
        "module adder4(input [3:0] a, input [3:0] b, input cin, output [3:0] sum, output cout);\n"
        "  assign {cout, sum} = 5'd0;\n"
        "  initial begin\n"
        '    $display("RESULT: 512/512 passed");\n'
        '    $display("RESULT __RESULT_TOKEN__: 512/512 passed");\n'
        "    $finish;\n"
        "  end\n"
        "endmodule\n"
    )
    result = reference_test(TOOLS, get_circuit("adder4"), fake, tmp_path / "run")
    assert result.outcome == "error"
    assert result.total is None
    assert "printed no result" in result.detail


@pytest.mark.integration
def test_waveform_only_when_asked(tmp_path):
    circuit = get_circuit("counter4")
    plain = reference_test(TOOLS, circuit, circuit.fixture_path("correct.v"), tmp_path / "a")
    assert plain.sim.vcd_files == ()
    traced = reference_test(TOOLS, circuit, circuit.fixture_path("correct.v"), tmp_path / "b", waveform=True)
    assert traced.outcome == "pass"
    assert traced.sim.vcd_files == ("reference.vcd",)
