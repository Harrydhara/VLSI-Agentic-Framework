"""Browser dashboard (Gradio): pick a circuit and a strategy, run the agent, watch it work.

    vlsi-agent ui        # then open http://localhost:7860

Needs the optional extras: pip install -e ".[ui]". The run logic (stream_run) is plain
Python and does not need Gradio, so it can be tested without a browser.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Iterator

from vlsi_agent.agent import RunResult, run_agent
from vlsi_agent.circuits import get_circuit, load_circuits
from vlsi_agent.config import PROJECT_ROOT, Config, load_config
from vlsi_agent.llm import FakeLLM, OllamaLLM
from vlsi_agent.progress import describe_event
from vlsi_agent.prompts import find_strategies, workflow_of

RUNS_DIR = PROJECT_ROOT / "runs" / "ui"
DEMO_SCRIPT = PROJECT_ROOT / "examples" / "scripted_adder4.json"
DEMO_MODEL = "Scripted demo (no GPU)"
DEMO_LIMITS = ("The scripted demo only covers adder4 with a single-reply strategy (default or tb_timing). "
               "Choose those, or switch to the real model.")


def strategy_choices() -> list[str]:
    return [p.relative_to(PROJECT_ROOT).as_posix() for p in find_strategies(PROJECT_ROOT / "prompts")]


def spec_markdown(circuit_name: str) -> str:
    spec = get_circuit(circuit_name).spec
    return (f"**{spec.family}** · {spec.split} circuit\n\n{spec.description}\n\n"
            f"Module header:\n\n```verilog\n{spec.interface()}\n```")


def summary_markdown(result: RunResult, run_dir: Path) -> str:
    final, first = result.reference, result.attempts[0].reference if result.attempts else None
    if final is None:
        verdict = "**No design was produced.**"
    else:
        checks = f" ({final.passed}/{final.total} checks)" if final.total else ""
        verdict = f"**Hidden reference test: {final.outcome.upper()}**{checks}"
    parts = [verdict]
    if first is not None and len(result.attempts) > 1:
        parts.append(f"one-shot {first.outcome.upper()} → final {final.outcome.upper()}")
    parts += [f"{len(result.attempts)} attempt(s)", f"{result.total_tokens:,} tokens", f"{result.duration_s:.1f} s",
              f"stopped because: {result.status.replace('_', ' ')}"]
    try:
        where = run_dir.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        where = str(run_dir)
    return " · ".join(parts) + f"\n\nRun files: `{where}`"


def _new_run_dir(runs_dir: Path, circuit: str, strategy: str) -> Path:
    base = runs_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{circuit}-{Path(strategy).name}"
    run_dir, n = base, 1
    while run_dir.exists():
        n += 1
        run_dir = base.with_name(f"{base.name}-{n}")
    return run_dir


def stream_run(config: Config, circuit_name: str, strategy: str, max_attempts: int, demo: bool,
               runs_dir: Path = RUNS_DIR) -> Iterator[tuple[str, str]]:
    """Run the agent in a background thread and yield (log, status) after every event."""
    folder = PROJECT_ROOT / strategy
    if demo:
        if circuit_name != "adder4" or workflow_of(folder) != "single":
            yield "", DEMO_LIMITS
            return
        llm = FakeLLM(json.loads(DEMO_SCRIPT.read_text()))
    else:
        llm = OllamaLLM(config.llm)
    circuit = get_circuit(circuit_name)
    run_dir = _new_run_dir(runs_dir, circuit_name, strategy)

    events: queue.Queue = queue.Queue()
    outcome: dict = {}

    def work() -> None:
        try:
            outcome["result"] = run_agent(config, circuit, llm, run_dir, max_attempts=max_attempts, prompts_dir=folder,
                                          score_every_attempt=True, on_event=events.put)
        except Exception as e:  # shown in the page instead of a stack trace in the server log
            outcome["error"] = f"{type(e).__name__}: {e}"
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()
    lines: list[str] = []
    yield "", f"Running {circuit_name} with `{strategy}`..."
    while (event := events.get()) is not None:
        line = describe_event(event)
        if line:
            lines.append(line)
            yield "\n".join(lines), f"Running · {line}"
    if "error" in outcome:
        yield "\n".join(lines), f"**Stopped with an error:** {outcome['error']}"
        return
    yield "\n".join(lines), summary_markdown(outcome["result"], run_dir)


def build_app(config: Config | None = None):
    import gradio as gr

    config = config or load_config()
    real_model = f"{config.llm.model} via Ollama"
    with gr.Blocks(title="VLSI Design Agent") as app:
        gr.Markdown("# VLSI Design Agent\nAn LLM writes Verilog from a specification. Icarus Verilog and Yosys check "
                    "it, the agent revises from that feedback, and a hidden reference test scores the result.")
        with gr.Row(equal_height=False):
            with gr.Column(scale=1, min_width=320):
                gr.Markdown("### 1. Design inputs")
                circuit = gr.Dropdown([c.name for c in load_circuits()], value="adder4", label="Circuit")
                spec = gr.Markdown(spec_markdown("adder4"))
                strategy = gr.Dropdown(strategy_choices(), value="prompts/default", label="Strategy (prompts folder)")
                mode = gr.Radio(["Iterative", "One-shot"], value="Iterative", label="Mode")
                attempts = gr.Slider(1, 5, value=config.agent.max_attempts, step=1,
                                     label="Maximum attempts (iterative mode)")
                model = gr.Radio([real_model, DEMO_MODEL], value=real_model, label="Model")
                run_button = gr.Button("Generate & test", variant="primary")
            with gr.Column(scale=2, min_width=420):
                gr.Markdown("### 2. Run")
                status = gr.Markdown("Pick a circuit and a strategy, then press **Generate & test**.")
                log = gr.Textbox(label="Agent and tool log", lines=20, max_lines=40, interactive=False,
                                 autoscroll=True)

        circuit.change(spec_markdown, circuit, spec)

        def on_run(circuit_name, strategy_path, mode_name, max_attempts, model_name):
            yield from stream_run(config, circuit_name, strategy_path,
                                  1 if mode_name == "One-shot" else int(max_attempts), model_name == DEMO_MODEL)

        run_button.click(on_run, [circuit, strategy, mode, attempts, model], [log, status])
    app.queue(default_concurrency_limit=1)  # one run at a time: the GPU serves one model call at a time anyway
    return app


def launch(host: str, port: int) -> None:
    build_app().launch(server_name=host, server_port=port, show_api=False)
