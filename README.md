# VLSI Design Agent

An LLM (local Ollama, `gemma4:26b`) writes Verilog from a circuit spec. Icarus Verilog and
Yosys check it, and the tool feedback drives revisions. Final scores come from reference
tests the agent never sees.

## Setup (inside WSL Ubuntu)

The tools (Ollama, iverilog, yosys) run in WSL, so the Python environment lives there too.
The virtual environment goes in the Linux home folder because it is much faster than `/mnt/c`.

```bash
cd "/mnt/c/Users/harrydhara16/Documents/Claude framework"
python3 -m venv ~/.venvs/vlsi-agent
~/.venvs/vlsi-agent/bin/pip install -e ".[dev]"
```

## Check the environment

```bash
~/.venvs/vlsi-agent/bin/vlsi-agent doctor
```

All checks should say `OK`. Tool paths and the model name are in `config/default.toml`.

## Run the tests

```bash
~/.venvs/vlsi-agent/bin/pytest            # everything
~/.venvs/vlsi-agent/bin/pytest -m "not integration"   # skip tests that need the real tools/Ollama
```

## Layout

| Path | What it holds |
|---|---|
| `config/default.toml` | Model, Ollama URL, pinned EDA tool paths |
| `src/vlsi_agent/` | The Python package (`cli.py`, `config.py`, `doctor.py`, ...) |
| `tests/` | pytest tests |
