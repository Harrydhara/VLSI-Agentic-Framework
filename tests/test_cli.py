import shutil

import pytest

from vlsi_agent import cli
from vlsi_agent.circuits import get_circuit
from vlsi_agent.llm import Completion, LLMError


class ScriptedOllama:
    """Stands in for OllamaLLM inside the CLI; returns or raises a scripted result."""

    result: Completion | LLMError
    sent: list = []

    def __init__(self, config):
        pass

    def complete(self, messages):
        ScriptedOllama.sent = list(messages)
        if isinstance(self.result, LLMError):
            raise self.result
        return self.result


def test_ask_prints_reply_and_usage(monkeypatch, capsys):
    ScriptedOllama.result = Completion("endmodule", "gemma4:26b", 28, 3, 0.4, truncated=False)
    monkeypatch.setattr(cli, "OllamaLLM", ScriptedOllama)
    assert cli.main(["ask", "Which keyword ends a module?", "--system", "Be brief."]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == "endmodule"
    assert "28 prompt + 3 output tokens" in err
    assert [(m.role, m.content) for m in ScriptedOllama.sent] == [
        ("system", "Be brief."), ("user", "Which keyword ends a module?")]


def test_ask_reports_llm_errors(monkeypatch, capsys):
    ScriptedOllama.result = LLMError("cannot reach Ollama")
    monkeypatch.setattr(cli, "OllamaLLM", ScriptedOllama)
    assert cli.main(["ask", "hi"]) == 1
    assert "LLM error: cannot reach Ollama" in capsys.readouterr().err


def test_circuits_lists_every_circuit(capsys):
    assert cli.main(["circuits"]) == 0
    out = capsys.readouterr().out
    for name in ("adder4", "counter4", "mux2", "adder8", "mux4", "updown8"):
        assert name in out
    assert "heldout" in out


def test_evaluate_unknown_circuit(tmp_path, capsys):
    design = tmp_path / "design.v"
    design.write_text("module m; endmodule\n")
    assert cli.main(["evaluate", "adder99", str(design)]) == 2
    assert "unknown circuit" in capsys.readouterr().err


def test_evaluate_missing_design_file(capsys):
    assert cli.main(["evaluate", "adder4", "/nonexistent/design.v"]) == 2
    assert "design file not found" in capsys.readouterr().err


@pytest.mark.integration
def test_run_with_scripted_replies_shows_the_revision(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RUNS_DIR", tmp_path)
    script = cli.PROJECT_ROOT / "examples" / "scripted_adder4.json"
    assert cli.main(["run", "adder4", "--fake", str(script), "--attempts", "3"]) == 0
    out = capsys.readouterr().out
    assert "attempt 1: development checks FAILED (compiled, testbench 256/512" in out
    assert "attempt 1: sent the check results back to the model" in out
    assert "attempt 2: development checks PASSED" in out
    assert "hidden reference test on attempt 2: PASS 512/512" in out
    assert "finished: dev_passed after 2 attempt(s)" in out
    [run_dir] = (tmp_path / "agent").iterdir()
    assert run_dir.name.endswith("-adder4-iter3") and (run_dir / "history.json").is_file()


@pytest.mark.integration
def test_one_shot_run_scores_without_revising(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RUNS_DIR", tmp_path)
    script = cli.PROJECT_ROOT / "examples" / "scripted_adder4.json"
    assert cli.main(["run", "adder4", "--fake", str(script), "--one-shot"]) == 1  # reference test fails
    out = capsys.readouterr().out
    assert "up to 1 attempt(s)" in out and "sent the check results" not in out
    assert "hidden reference test on attempt 1: FAIL 256/512" in out


@pytest.mark.integration
def test_run_prompts_path_is_relative_to_the_current_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RUNS_DIR", tmp_path / "runs")
    mine = tmp_path / "mine"
    shutil.copytree(cli.PROJECT_ROOT / "prompts" / "default", mine)
    monkeypatch.chdir(tmp_path)
    script = cli.PROJECT_ROOT / "examples" / "scripted_adder4.json"
    assert cli.main(["run", "adder4", "--fake", str(script), "--one-shot", "--prompts", "mine"]) == 1
    assert f"prompts\": \"{mine}" in next((tmp_path / "runs" / "agent").iterdir()).joinpath("events.jsonl").read_text()


def test_run_rejects_a_bad_fake_file(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "a list"}')
    assert cli.main(["run", "adder4", "--fake", str(bad)]) == 2
    assert "JSON list of strings" in capsys.readouterr().err


def test_run_rejects_zero_attempts(capsys):
    assert cli.main(["run", "adder4", "--attempts", "0"]) == 2
    assert "at least 1" in capsys.readouterr().err


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
