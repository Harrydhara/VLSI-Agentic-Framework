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

## Dashboard

```bash
~/.venvs/vlsi-agent/bin/pip install -e ".[ui]"   # Gradio and Plotly, once
~/.venvs/vlsi-agent/bin/vlsi-agent ui             # then open http://localhost:7860
```

Pick a circuit (its specification and module header are shown), a strategy, one-shot or
iterative mode and the model, then press **Generate & test** to watch the run's progress
live. "Scripted demo (no GPU)" replays `examples/scripted_adder4.json` for adder4. Runs
started from the dashboard are saved under `runs/ui/`.

**3. Results** opens any saved run (dashboard, CLI or benchmark): a table of each
attempt's own checks and hidden-test score, then per attempt the design, testbench,
changes from the previous attempt, the hidden test's mismatch lines, the feedback sent to
the model and its reply. The **Waveform** tab re-runs that attempt's design through the
hidden test and plots a time window (Plotly), opening just before the first mismatch,
with dashed lines at the mismatches and the test's expected value under each output.

**4. Benchmarks** charts any folder under `runs/bench/` (Plotly): hidden-test passes after
1, 2, 3 attempts per strategy (one-shot vs iterative on the same runs), one-shot →
iterative per circuit, how often the agent's own checks rejected correct designs or
accepted wrong ones, revisions that fixed or broke a design, and tokens per run. The
numbers are rebuilt from each run's `history.json`, as the report does.

**Export:** *Export run (.zip)* packs the chosen run folder with a readable `summary.md`
(verdict, each attempt's checks, the hidden test's mismatches, the feedback sent, the
final design). *Export charts and rows* gives the benchmark's charts as one standalone
HTML page (opens offline) and its rows as CSV. The zip holds the raw run files, so
`history.json` keeps the absolute path of the prompts folder; the CSV's paths are
relative to the project.

## Run the agent

```bash
vlsi-agent run adder4                  # iterative: up to [agent] max_attempts (3) attempts
vlsi-agent run adder4 --one-shot       # baseline: one attempt, no revision
vlsi-agent run adder4 --fake examples/scripted_adder4.json   # scripted replies, no GPU needed
vlsi-agent run adder4 --budget 20000 --prompts my_prompts/   # token budget, your own prompts
```

Each attempt asks the model for the design **and its own testbench**. The development
checks (compile, run that testbench, synthesize, look for latches) are the only feedback
the model gets. The loop stops when they pass, when the attempts run out, or before a
model call once the token budget is used up. Then the final design is scored with the
circuit's **hidden reference test**, which the model never sees.

The run status (`dev_passed`, `attempts_exhausted`, `token_budget_exhausted`,
`llm_error`) describes the development checks. The score is the reference outcome. The two
can disagree: a wrong testbench written by the model can reject a correct design.

Each run gets a folder under `runs/agent/`: `events.jsonl` (written live),
`history.json` (settings, every attempt, the scores), `attempt_<n>/` (raw reply, design,
testbench, tool logs, the feedback sent), and `reference/attempt_<n>/`.

With `--score-every-attempt`, every attempt's design is scored after the run, not just
the last one. Attempt 1 is exactly what a one-shot run produces, so one run gives both the
one-shot and the iterative score, and shows whether a revision fixed a design or broke
one. Scoring starts only after the loop ends, so it cannot affect the run.

## Benchmark

```bash
vlsi-agent bench                                   # dev circuits, seeds 1-5, default prompts
vlsi-agent bench --prompts prompts/default prompts/examples/tb_timing   # compare strategies
vlsi-agent bench --split heldout --seeds 1-5       # the final comparison only
vlsi-agent bench --out runs/bench/my_run           # give the same folder again to resume
```

Every run is iterative, with every attempt scored by the hidden reference test after the
run. Attempt 1 is exactly a one-shot run, so each run gives a **paired** one-shot and
iterative score, and counts revisions that **fixed** a design or **broke** one. Results
go to `runs/bench/<folder>/`: `rows.csv` (one row per run), `summary.md` and
`summary.json` (tables by strategy, split and circuit: pass counts after each attempt,
compile and synthesis success, tokens, time, and cell counts for passing designs only).

Tune strategies on `dev`. Use `heldout` once, for the final comparison, with seeds you
did not use while tuning.

**Results so far:** [`docs/benchmark-report.html`](docs/benchmark-report.html) (open it in a
browser) reports 200 gemma4:26b runs in two rounds: one-shot vs. iterative with four
strategies (default, tb_timing, spec_first v1 and v2), with method, tables, how reliable
the agent's own checks were, examples and caveats. Its tables and run grids are generated
from `runs/bench/` by `python3 docs/report/build.py`; the text is in `docs/report/template.html`.

## Changing the strategy

The prompts are plain text in `prompts/default/` (`system.txt`, `task.txt`,
`feedback.txt`) with `{{placeholders}}`. Copy the folder, edit the copy, and run with
`--prompts <folder>` (or set `prompts_dir` in a config copy). A misspelled placeholder
is reported before any model call.

Or use the dashboard's **5. Strategy editor**: load any strategy, edit its templates
(each shows which placeholders it may use), **Check** them, preview them filled in for a
circuit, and **Save** the copy as `prompts/custom/<name>/`, which then appears in the
strategy list. The shipped strategies are never changed, and saving never replaces a
different custom strategy than the one loaded. Every run keeps a copy of the templates it
used in its folder (`prompts/`), so editing a strategy later does not change the record.

A strategy folder can also change the workflow with a `strategy.toml`.
[`prompts/examples/spec_first/`](prompts/examples/spec_first/README.md) uses
`workflow = "spec_first"`: the testbench is written from the specification in its own
conversation before any design exists, the design is written in a second conversation,
and failed checks go to the testbench's author for review before the design is revised.

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

Reference tests also print failures as `MISMATCH at <t> ns...` (the first 5), and dump
waveforms only on request: `+vcd` writes `reference.vcd`, and `+vcd_from=<ns>` /
`+vcd_until=<ns>` limit it to a window. Copy the block from an existing `reference_tb.v`
when adding a circuit; the dashboard relies on both.

## Layout

| Path | What it holds |
|---|---|
| `config/default.toml` | Model, pinned EDA tool paths, tool timeouts |
| `src/vlsi_agent/` | The Python package (`cli.py`, `config.py`, `doctor.py`, ...) |
| `src/vlsi_agent/eda.py` | Runs iverilog, vvp and Yosys with timeouts; returns structured results |
| `src/vlsi_agent/circuits.py` | Loads and validates circuit folders |
| `src/vlsi_agent/evaluate.py` | Scores a design with a circuit's hidden reference test |
| `src/vlsi_agent/llm.py` | Ollama client, scripted `FakeLLM` for tests, Verilog extraction from replies |
| `src/vlsi_agent/agent.py` | The generate / check / revise loop, event log, history export |
| `src/vlsi_agent/bench.py` | Benchmark runner (resumable) and summary tables |
| `src/vlsi_agent/devcheck.py` | Development checks: the only feedback the model sees |
| `src/vlsi_agent/prompts.py` | Loads and fills the prompt templates |
| `src/vlsi_agent/dashboard.py` | The Gradio dashboard (`vlsi-agent ui`) |
| `src/vlsi_agent/runview.py` | Reads saved run folders for the dashboard's results view |
| `src/vlsi_agent/benchview.py` | Reads benchmark folders and draws the dashboard's benchmark charts |
| `src/vlsi_agent/strategy_editor.py` | Loads, checks, previews and saves strategies for the dashboard's editor |
| `src/vlsi_agent/waveform.py` | Windowed reference-test VCD dumps, VCD parser, Plotly waveform figure |
| `prompts/default/` | The starter agent's prompts |
| `examples/` | Scripted replies for running without a GPU |
| `docs/` | Reports, e.g. `benchmark-report.html` |
| `circuits/` | Circuit specs, reference tests and fixtures |
| `tests/` | pytest tests |
