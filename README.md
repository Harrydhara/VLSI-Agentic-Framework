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

To send the model a single prompt (the reply goes to stdout, token usage to stderr):

```bash
~/.venvs/vlsi-agent/bin/vlsi-agent ask "Which Verilog keyword ends a module?"
```

Model settings (`temperature`, `seed`, `think`, `max_output_tokens`, ...) are in the
`[llm]` section of the config. A fixed seed helps, but identical runs on a GPU can
still give different answers, so compare settings over several runs.

## Run the tests

```bash
~/.venvs/vlsi-agent/bin/pytest            # everything
~/.venvs/vlsi-agent/bin/pytest -m "not integration"   # skip tests that need the real tools/Ollama
```

## Circuits and reference tests

Each circuit has a folder in `circuits/`:

| File | Who sees it | Purpose |
|---|---|---|
| `spec.toml` | the agent | Description and exact ports; `split` is `dev` (tune prompts on these) or `heldout` (final comparison only) |
| `reference_tb.v` | never the agent | Hidden reference test used for final scoring |
| `fixtures/*.v` + `fixtures/expected.toml` | developers | Known-correct and deliberately faulty designs, each with its expected outcome |

```bash
vlsi-agent circuits                                  # list circuits
vlsi-agent evaluate adder4 my_adder.v --waveform     # score any Verilog file; files go to runs/evaluate/
vlsi-agent check-fixtures                            # every fixture must get its expected outcome
```

Outcomes: `pass`, `fail`, `compile_error` (including a wrong port name), `timeout`, or
`error` (the simulator crashed, or no result line appeared).

## Testbench convention

A testbench reports its score by printing exactly this line before `$finish`:

```verilog
$display("RESULT: %0d/%0d passed", passed, total);
```

The simulator's exit code is not enough: `vvp` exits 0 even when a testbench never
checks anything or forgets `$finish`. A run counts as passing only if `vvp` exits 0
before the timeout **and** the last `RESULT` line shows `passed == total` with `total > 0`.

Reference tests print `RESULT __RESULT_TOKEN__: ...` instead. The placeholder is
replaced with a random token on every run and only that line counts, so a generated
design that prints its own `RESULT` line and calls `$finish` cannot fake a pass.

## Layout

| Path | What it holds |
|---|---|
| `config/default.toml` | Model, Ollama URL, pinned EDA tool paths, tool timeouts |
| `src/vlsi_agent/` | The Python package (`cli.py`, `config.py`, `doctor.py`, ...) |
| `src/vlsi_agent/eda.py` | Runs iverilog, vvp and Yosys with timeouts; returns structured results |
| `src/vlsi_agent/circuits.py` | Loads and validates circuit folders |
| `src/vlsi_agent/evaluate.py` | Scores a design with a circuit's hidden reference test |
| `src/vlsi_agent/llm.py` | Ollama client, scripted `FakeLLM` for tests, Verilog extraction from replies |
| `circuits/` | Circuit specs, reference tests and fixtures |
| `tests/` | pytest tests |
