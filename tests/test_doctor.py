import sys

import pytest

from vlsi_agent import cli
from vlsi_agent.config import load_config
from vlsi_agent.doctor import check_ollama, check_tool, run_checks


def test_tool_that_works_passes():
    check = check_tool("python", [sys.executable, "--version"])
    assert check.ok
    assert check.detail.startswith("Python 3.")


def test_missing_tool_fails():
    check = check_tool("ghost", ["/nonexistent/ghost-tool", "-V"])
    assert not check.ok
    assert "not found" in check.detail


def test_tool_with_nonzero_exit_fails():
    check = check_tool("failing", [sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"])
    assert not check.ok
    assert check.detail == "exit code 3: boom"


def test_hung_tool_times_out():
    check = check_tool("hang", [sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.5)
    assert not check.ok
    assert "no response" in check.detail


def test_unreachable_ollama_fails_both_checks():
    # Port 9 (discard) is never an Ollama server; the connection is refused at once.
    server, model = check_ollama("http://127.0.0.1:9", "gemma4:26b", timeout_s=2)
    assert not server.ok and "cannot reach" in server.detail
    assert not model.ok and "skipped" in model.detail


def test_cli_reports_bad_config(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("[llm]\n")
    assert cli.main(["--config", str(bad), "doctor"]) == 2
    assert "config error" in capsys.readouterr().err


@pytest.mark.integration
def test_real_environment_is_ready():
    checks = run_checks(load_config())
    failed = [f"{c.name}: {c.detail}" for c in checks if not c.ok]
    assert not failed, failed
