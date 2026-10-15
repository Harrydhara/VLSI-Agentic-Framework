import pytest

from vlsi_agent import cli
from vlsi_agent.circuits import get_circuit


def test_circuits_lists_every_circuit(capsys):
    assert cli.main(["circuits"]) == 0
    out = capsys.readouterr().out
    for name in ("adder4", "counter4", "mux2"):
        assert name in out


def test_evaluate_unknown_circuit(tmp_path, capsys):
    design = tmp_path / "design.v"
    design.write_text("module m; endmodule\n")
    assert cli.main(["evaluate", "adder99", str(design)]) == 2
    assert "unknown circuit" in capsys.readouterr().err


def test_evaluate_missing_design_file(capsys):
    assert cli.main(["evaluate", "adder4", "/nonexistent/design.v"]) == 2
    assert "design file not found" in capsys.readouterr().err


@pytest.mark.integration
def test_evaluate_reports_pass_and_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RUNS_DIR", tmp_path)
    circuit = get_circuit("adder4")

    assert cli.main(["evaluate", "adder4", str(circuit.fixture_path("correct.v"))]) == 0
    assert "PASS  512/512 checks passed" in capsys.readouterr().out

    assert cli.main(["evaluate", "adder4", str(circuit.fixture_path("no_carry_out.v"))]) == 1
    out = capsys.readouterr().out
    assert "FAIL  256/512 checks passed" in out
    assert "MISMATCH" in out
