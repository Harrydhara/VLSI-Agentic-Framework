"""Environment check: are the EDA tools and the Ollama model ready to use?"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

from vlsi_agent.config import Config


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def check_tool(name: str, cmd: list[str], timeout_s: float = 15) -> Check:
    """Run a tool's version command; pass if it exits 0, report its first output line."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        return Check(name, False, f"not found: {cmd[0]}")
    except PermissionError:
        return Check(name, False, f"not executable: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return Check(name, False, f"no response after {timeout_s:g} s: {' '.join(cmd)}")
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    first = lines[0] if lines else "(no output)"
    if proc.returncode != 0:
        return Check(name, False, f"exit code {proc.returncode}: {first}")
    return Check(name, True, first)


def _get_json(url: str, timeout_s: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        return json.load(resp)


def check_ollama(base_url: str, model: str, timeout_s: float = 5) -> list[Check]:
    """Check the Ollama server answers and has the configured model pulled."""
    base_url = base_url.rstrip("/")
    try:
        version = _get_json(f"{base_url}/api/version", timeout_s).get("version", "?")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        return [
            Check("ollama server", False, f"cannot reach {base_url} ({reason}); is the service running? "
                                          "Try: systemctl status ollama"),
            Check(f"model {model}", False, "skipped: server not reachable"),
        ]
    checks = [Check("ollama server", True, f"version {version} at {base_url}")]

    try:
        names = [m["name"] for m in _get_json(f"{base_url}/api/tags", timeout_s).get("models", [])]
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as e:
        checks.append(Check(f"model {model}", False, f"could not list models: {e}"))
        return checks
    if model in names:
        checks.append(Check(f"model {model}", True, "pulled"))
    else:
        checks.append(Check(f"model {model}", False, f"not pulled; run: ollama pull {model}"))
    return checks


def run_checks(config: Config) -> list[Check]:
    tools = config.tools
    return [
        check_tool("iverilog", [tools.iverilog, "-V"]),
        check_tool("vvp", [tools.vvp, "-V"]),
        check_tool("yosys", [tools.yosys, "-V"]),
        *check_ollama(config.llm.base_url, config.llm.model),
    ]


def main(config: Config) -> int:
    """Print a pass/fail table; return 0 only if every check passed."""
    checks = run_checks(config)
    width = max(len(c.name) for c in checks)
    print(f"config: {config.source}")
    for c in checks:
        print(f"  {'OK  ' if c.ok else 'FAIL'}  {c.name:<{width}}  {c.detail}")
    failed = sum(not c.ok for c in checks)
    print("all checks passed" if not failed else f"{failed} check(s) failed")
    return 0 if not failed else 1
