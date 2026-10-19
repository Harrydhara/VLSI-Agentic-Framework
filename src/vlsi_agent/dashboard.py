"""Browser dashboard (Gradio): pick a circuit and a strategy, run the agent, inspect the result.

    vlsi-agent ui        # then open http://localhost:7860

Needs the optional extras: pip install -e ".[ui]". The run logic (stream_run) and the
results data (runview.py) are plain Python and do not need Gradio, so they are tested
without a browser.
"""

from __future__ import annotations

import json
import queue
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterator

from vlsi_agent.agent import DESIGN_FILE, RunResult, run_agent
from vlsi_agent.benchview import BENCH_ROOT, CHARTS, bench_markdown, export_benchmark, list_benchmarks, load_benchmark
from vlsi_agent.circuits import get_circuit, load_circuits
from vlsi_agent.config import PROJECT_ROOT, Config, ToolsConfig, load_config
from vlsi_agent.llm import FakeLLM, OllamaLLM
from vlsi_agent.progress import describe_event
from vlsi_agent.prompts import TEMPLATES, PromptError, find_strategies, workflow_of
from vlsi_agent.runview import (CHECK_HEADERS, RUNS_ROOT, attempt_diffs, check_table, export_run_zip, list_runs,
                                load_run, mismatch_lines, run_markdown)
from vlsi_agent.strategy_editor import (CUSTOM_ROOT, FILE_HELP, WORKFLOW_TEXT, Draft, check_draft, check_markdown,
                                        draft_from_slots, load_draft, preview, save_draft, suggest_name)
from vlsi_agent.waveform import (MAX_WINDOW_NS, default_window, display_rows, is_clocked, mismatch_times,
                                 reference_log_now, reference_waveform, waveform_figure)

RUNS_DIR = RUNS_ROOT / "ui"
DEMO_SCRIPT = PROJECT_ROOT / "examples" / "scripted_adder4.json"
DEMO_MODEL = "Scripted demo (no GPU)"
DEMO_LIMITS = ("The scripted demo only covers adder4 with a single-reply strategy (default, tb_timing or a "
               "custom copy of them, not spec_first). Choose those, or switch to the real model.")

CSS = """
.diff { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; line-height: 1.45;
        padding: 8px 0; border-radius: 6px; overflow-x: auto; background: var(--block-background-fill);
        border: 1px solid var(--border-color-primary); }
.diff > div { white-space: pre; padding: 0 12px; }
.diff-add { background: rgba(46, 160, 67, 0.20); }
.diff-del { background: rgba(248, 81, 73, 0.20); }
.diff-meta { color: var(--body-text-color-subdued); }
.diff-note { color: var(--body-text-color-subdued); }
"""


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
    return " · ".join(parts) + "\n\nDetails are in **3. Results** below."


def _new_run_dir(runs_dir: Path, circuit: str, strategy: str) -> Path:
    base = runs_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{circuit}-{Path(strategy).name}"
    run_dir, n = base, 1
    while run_dir.exists():
        n += 1
        run_dir = base.with_name(f"{base.name}-{n}")
    return run_dir


def stream_run(config: Config, circuit_name: str, strategy: str, max_attempts: int, demo: bool,
               runs_dir: Path = RUNS_DIR) -> Iterator[tuple[str, str, Path | None]]:
    """Run the agent in a background thread and yield (log, status, run folder) after every
    event. The run folder is None until the run has finished."""
    folder = PROJECT_ROOT / strategy
    if demo:
        if circuit_name != "adder4" or workflow_of(folder) != "single":
            yield "", DEMO_LIMITS, None
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
    yield "", f"Running {circuit_name} with `{strategy}`...", None
    while (event := events.get()) is not None:
        line = describe_event(event)
        if line:
            lines.append(line)
            yield "\n".join(lines), f"Running · {line}", None
    if "error" in outcome:
        yield "\n".join(lines), f"**Stopped with an error:** {outcome['error']}", None
        return
    yield "\n".join(lines), summary_markdown(outcome["result"], run_dir), run_dir


# --------------------------------------------------------------------------- results view


def _run_folder(relative: str | None) -> Path | None:
    """A run folder chosen in the page, only if it is really inside runs/."""
    if not relative:
        return None
    folder = (RUNS_ROOT / relative).resolve()
    if not folder.is_relative_to(RUNS_ROOT.resolve()) or not (folder / "history.json").is_file():
        return None
    return folder


def results_for_run(relative: str | None) -> tuple[str, list[list], list[str], str | None]:
    """(run summary, check table rows, attempt choices, selected attempt) for a run folder."""
    folder = _run_folder(relative)
    if folder is None:
        return "Choose a run to see its attempts.", [], [], None
    run = load_run(folder)
    choices = [str(a.number) for a in run.attempts]
    return run_markdown(run), check_table(run), choices, (choices[-1] if choices else None)


def results_for_attempt(relative: str | None, number: str | None) -> tuple[str, str, str, str, str, str]:
    """(design, testbench, diffs html, hidden-test markdown, feedback, model reply) for one attempt."""
    folder = _run_folder(relative)
    if folder is None or not number:
        return "", "", "", "", "", ""
    run = load_run(folder)
    a = run.attempt(int(number))
    return (a.design or "(no Verilog in this reply)", a.testbench or "(no testbench)", attempt_diffs(run, a.number),
            mismatch_lines(a), a.feedback or "(nothing: this attempt passed the development checks)", a.reply)


WAVE_HINT = "Press **Draw waveform** to run this attempt's design through the hidden test again, recording this window."


def waveform_defaults(tools: ToolsConfig, relative: str | None, number: str | None) -> tuple[int, int]:
    """(start, length) in ns for an attempt's waveform: just before its first hidden-test mismatch."""
    folder = _run_folder(relative)
    if folder is None or not number:
        return 0, 64
    run = load_run(folder)
    a = run.attempt(int(number))
    circuit = get_circuit(run.history["circuit"])
    log = a.reference_log
    if "MISMATCH" in log and not mismatch_times(log) and a.design:  # saved before the tests printed the time
        log = reference_log_now(tools, circuit, a.folder / DESIGN_FILE)
    return default_window(circuit.spec, log)


def waveform_for_attempt(tools: ToolsConfig, relative: str | None, number: str | None, start, length):
    """(note, Plotly figure or None) for one attempt over [start, start + length] ns."""
    folder = _run_folder(relative)
    if folder is None or not number:
        return "Choose a run and an attempt.", None
    run = load_run(folder)
    a = run.attempt(int(number))
    if not a.design:
        return "This attempt has no design to simulate.", None
    circuit = get_circuit(run.history["circuit"])
    start = max(0, int(start or 0))
    length = max(1, min(int(length or 1), MAX_WINDOW_NS))
    w = reference_waveform(tools, circuit, a.folder / DESIGN_FILE, start, length)
    if w.wave is None:
        return f"No waveform: the hidden test ended with **{w.outcome.upper()}** ({w.detail}).", None
    times = mismatch_times(w.log)
    shown = [t for t in times if start <= t <= start + length]
    parts = [f"Attempt {a.number} · {start} to {start + length} ns · hidden test **{w.outcome.upper()}** ({w.detail})"]
    if times:
        listed = ", ".join(f"{t}" for t in times) + " ns" + (" (the test lists the first 5)" if len(times) == 5 else "")
        parts.append(f"Mismatches reported at {listed}; "
                     + ("dashed lines mark those in this window." if shown else "none is in this window."))
    if not is_clocked(circuit.spec):
        parts.append("Each input vector is held for 1 ns. The test computes the expected value when it checks, "
                     "at the end of that 1 ns, so the expected rows trail the outputs by 1 ns.")
    parts.append("Red marks unknown (x) values. Hover a bus to see its value in binary.")
    figure = waveform_figure(display_rows(w.wave, circuit.spec), start, start + length, times)
    return "\n\n".join(parts), figure


def _bench_folder(name: str | None) -> Path | None:
    """A benchmark chosen in the page, only if it is really a folder directly under runs/bench/."""
    folder = (BENCH_ROOT / name).resolve() if name else None
    if folder is None or folder.parent != BENCH_ROOT.resolve() or not folder.is_dir():
        return None
    return folder


def benchmark_charts(name: str | None) -> tuple:
    """(summary markdown, then one Plotly figure per chart in CHARTS) for a benchmark folder."""
    folder = _bench_folder(name)
    if folder is None:
        return ("Choose a benchmark. Run one with `vlsi-agent bench`; results go to `runs/bench/`.",
                *([None] * len(CHARTS)))
    view = load_benchmark(folder)
    if not view.rows:
        return (f"`{name}` has no finished runs yet.", *([None] * len(CHARTS)))
    return (bench_markdown(view), *(chart(view) for chart in CHARTS))


EDITOR_SLOTS = max(len(files) for files in TEMPLATES.values())  # text boxes for one workflow's templates


def editor_draft(source: str | None) -> tuple[Draft, str] | None:
    """A strategy from the list as an editable draft, and a name to save it under."""
    if source not in strategy_choices():
        return None
    folder = PROJECT_ROOT / source
    return load_draft(folder), suggest_name(folder)


def editor_save(loaded_from: str | None, workflow: str, name: str, slot_values, notes: str,
                root: Path = CUSTOM_ROOT) -> tuple[str, str | None]:
    """(status markdown, the saved strategy as a strategy-list entry or None). Replaces an
    existing custom strategy only if it is the one that was loaded into the editor."""
    draft = draft_from_slots(workflow, slot_values, notes)
    name = (name or "").strip()
    target = (Path(root) / name).resolve()
    may_replace = bool(name) and loaded_from is not None and (PROJECT_ROOT / loaded_from).resolve() == target
    try:
        folder, replaced = save_draft(draft, name, root, may_replace=may_replace)
    except PromptError as e:
        return f"**Not saved.** {e}", None
    try:
        entry = folder.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        entry = str(folder)
    warnings = check_draft(draft)[1]
    status = (f"**Saved `{entry}`**" + (" (replaced the earlier version)" if replaced else "")
              + ". Choose it under **1. Design inputs** to run it.")
    if warnings:
        status += "\n\n" + "\n".join(f"- ⚠️ {w}" for w in warnings)
    return status, entry


def _export_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="vlsi-export-"))  # Gradio serves (and copies) files from the temp dir


def export_run(relative: str | None) -> str | None:
    """A zip of the chosen run folder with a summary.md, or None if no run is chosen."""
    folder = _run_folder(relative)
    if folder is None:
        return None
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", relative.replace("/runs/", "/")).strip("-")  # bench/dev-seeds1-5/alu4/...
    return str(export_run_zip(load_run(folder), _export_dir(), name))


def export_benchmark_files(name: str | None) -> list[str] | None:
    """The chosen benchmark's charts (standalone HTML) and rows (CSV), or None."""
    folder = _bench_folder(name)
    if folder is None:
        return None
    view = load_benchmark(folder)
    return [str(p) for p in export_benchmark(view, _export_dir())] if view.rows else None


def _relative(run_dir: Path) -> str:
    return run_dir.relative_to(RUNS_ROOT).as_posix()


# --------------------------------------------------------------------------- page


def build_app(config: Config | None = None):
    import gradio as gr

    config = config or load_config()
    real_model = f"{config.llm.model} via Ollama"
    runs = list_runs()
    # Gradio 5 takes css here; Gradio 6 moves it to launch() (pyproject pins gradio<6).
    with gr.Blocks(title="VLSI Design Agent", css=CSS) as app:
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
                log = gr.Textbox(label="Agent and tool log", lines=14, max_lines=30, interactive=False,
                                 autoscroll=True)

        gr.Markdown("### 3. Results")
        with gr.Row():
            run_picker = gr.Dropdown(runs, value=runs[0] if runs else None, label="Run (newest first)",
                                     filterable=True, scale=5)
            refresh = gr.Button("Refresh list", scale=1)
        run_info = gr.Markdown()
        with gr.Row(equal_height=True):
            export_run_button = gr.Button("Export run (.zip)", scale=1)
            run_export = gr.File(label="Run folder and summary.md", interactive=False, height=60, scale=3)
        checks = gr.Dataframe(headers=CHECK_HEADERS, value=[], interactive=False, wrap=True, row_count=1,
                              label="Each attempt: the agent's own checks and the hidden test")
        attempt = gr.Radio([], label="Attempt")
        with gr.Tabs():
            with gr.Tab("Design"):
                design = gr.Code(label="design.v", interactive=False)
            with gr.Tab("Testbench"):
                testbench = gr.Code(label="tb.v", interactive=False)
            with gr.Tab("Changes from the previous attempt"):
                diffs = gr.HTML(padding=False)
            with gr.Tab("Hidden test"):
                hidden = gr.Markdown()
            with gr.Tab("Waveform"):
                with gr.Row(equal_height=True):
                    wave_start = gr.Number(0, label="Start (ns)", precision=0, minimum=0)
                    wave_length = gr.Number(64, label="Length (ns)", precision=0, minimum=1, maximum=MAX_WINDOW_NS)
                    draw = gr.Button("Draw waveform")
                wave_note = gr.Markdown(WAVE_HINT)
                wave_plot = gr.Plot(show_label=False)
            with gr.Tab("Feedback sent to the model"):
                feedback = gr.Textbox(lines=12, interactive=False, show_label=False)
            with gr.Tab("Model reply"):
                reply = gr.Markdown()

        gr.Markdown("### 4. Benchmarks")
        benchmarks = list_benchmarks()
        with gr.Row():
            bench_picker = gr.Dropdown(benchmarks, value=benchmarks[0] if benchmarks else None,
                                       label="Benchmark (runs/bench/)", scale=5)
            bench_refresh = gr.Button("Refresh list", scale=1)
        bench_info = gr.Markdown()
        with gr.Row(equal_height=True):
            export_bench_button = gr.Button("Export charts and rows", scale=1)
            bench_export = gr.File(label="Charts (standalone HTML) and rows (CSV)", file_count="multiple",
                                   interactive=False, height=90, scale=3)
        with gr.Row(equal_height=False):  # side by side when there is room, stacked otherwise
            pass_plot, heatmap_plot = gr.Plot(show_label=False, min_width=460), gr.Plot(show_label=False, min_width=460)
        with gr.Row(equal_height=False):
            checks_plot, fixed_plot = gr.Plot(show_label=False, min_width=460), gr.Plot(show_label=False, min_width=460)
        tokens_plot = gr.Plot(show_label=False)
        bench_outputs = [bench_info, pass_plot, heatmap_plot, checks_plot, fixed_plot, tokens_plot]

        gr.Markdown("### 5. Strategy editor\nCopy a strategy, change its prompts, check them and save the copy under "
                    "`prompts/custom/`; it then appears in **1. Design inputs**. The shipped strategies are never "
                    "changed, and every run keeps a copy of the prompts it used.")
        start_draft, start_name = editor_draft("prompts/default")
        start_files = list(TEMPLATES[start_draft.workflow])
        with gr.Row():
            editor_source = gr.Dropdown(strategy_choices(), value="prompts/default", label="Start from", scale=5)
            editor_load = gr.Button("Load", scale=1)
        editor_workflow = gr.State(start_draft.workflow)
        editor_loaded = gr.State("prompts/default")  # the strategy whose files are in the editor
        workflow_md = gr.Markdown("Workflow: " + WORKFLOW_TEXT[start_draft.workflow])
        slots = []
        for i in range(EDITOR_SLOTS):
            shown = i < len(start_files)
            slots.append(gr.Textbox(start_draft.texts[start_files[i]] if shown else "", lines=6, max_lines=30,
                                    label=f"{start_files[i]}.txt" if shown else "template",
                                    info=FILE_HELP[start_files[i]] if shown else None, visible=shown))
        editor_notes = gr.Textbox(start_draft.notes, lines=4, label="README.md: notes on this strategy",
                                  info="What you changed and why. Never sent to the model.")
        with gr.Row(equal_height=True):
            editor_name = gr.Textbox(start_name, label="Save as prompts/custom/<name>", scale=4)
            editor_check = gr.Button("Check", scale=1)
            editor_save_button = gr.Button("Save", variant="primary", scale=1)
        editor_status = gr.Markdown()
        with gr.Accordion("Preview: the prompts as the model receives them", open=False):
            with gr.Row(equal_height=True):
                preview_circuit = gr.Dropdown([c.name for c in load_circuits()], value="adder4",
                                              label="Circuit", scale=4)
                preview_button = gr.Button("Preview", scale=1)
            preview_text = gr.Textbox(lines=24, max_lines=60, interactive=False, show_label=False)

        circuit.change(spec_markdown, circuit, spec)

        def on_run(circuit_name, strategy_path, mode_name, max_attempts, model_name):
            busy = gr.update(interactive=False, value="Running...")  # one run at a time, and it shows
            try:
                for log_text, status_text, run_dir in stream_run(
                        config, circuit_name, strategy_path, 1 if mode_name == "One-shot" else int(max_attempts),
                        model_name == DEMO_MODEL):
                    picker = (gr.update(choices=list_runs(), value=_relative(run_dir)) if run_dir is not None
                              else gr.update())
                    yield log_text, status_text, picker, busy
            except Exception as e:  # e.g. a broken strategy: say so, and never leave the button disabled
                yield gr.update(), f"**Could not start the run:** {type(e).__name__}: {e}", gr.update(), busy
            yield gr.update(), gr.update(), gr.update(), gr.update(interactive=True, value="Generate & test")

        def on_run_picked(relative):
            summary, table, choices, selected = results_for_run(relative)
            return summary, table, gr.update(choices=choices, value=selected), None

        def on_attempt_wave(relative, number):
            start, length = waveform_defaults(config.tools, relative, number)
            return start, length, WAVE_HINT, None

        def on_draw(relative, number, start, length):
            return waveform_for_attempt(config.tools, relative, number, start, length)

        attempt_files = [design, testbench, diffs, hidden, feedback, reply]
        wave_reset = [wave_start, wave_length, wave_note, wave_plot]
        run_button.click(on_run, [circuit, strategy, mode, attempts, model], [log, status, run_picker, run_button])
        refresh.click(lambda: gr.update(choices=list_runs()), None, run_picker)
        export_run_button.click(export_run, run_picker, run_export)
        # Picking a run often leaves the attempt number as it was (say 3 → 3), which fires no
        # attempt change, so the attempt views are refreshed here; attempt.input covers clicks.
        for picked in (run_picker.change, app.load):
            (picked(on_run_picked, run_picker, [run_info, checks, attempt, run_export])
             .then(results_for_attempt, [run_picker, attempt], attempt_files)
             .then(on_attempt_wave, [run_picker, attempt], wave_reset))
        attempt.input(results_for_attempt, [run_picker, attempt], attempt_files).then(
            on_attempt_wave, [run_picker, attempt], wave_reset)
        for trigger in (draw.click, wave_start.submit, wave_length.submit):
            trigger(on_draw, [run_picker, attempt, wave_start, wave_length], [wave_note, wave_plot])
        for shown in (bench_picker.change, app.load):
            shown(benchmark_charts, bench_picker, bench_outputs)
        bench_picker.change(lambda: None, None, bench_export)
        export_bench_button.click(export_benchmark_files, bench_picker, bench_export)
        bench_refresh.click(lambda: gr.update(choices=list_benchmarks()), None, bench_picker)

        def on_editor_load(source, workflow, loaded):
            found = editor_draft(source)
            if found is None:
                return (workflow, loaded, gr.update(), gr.update(), gr.update(), "Choose a strategy to load.",
                        *[gr.update()] * EDITOR_SLOTS)
            draft, name = found
            files = list(TEMPLATES[draft.workflow])
            boxes = [gr.update(value=draft.texts[files[i]], label=f"{files[i]}.txt", info=FILE_HELP[files[i]],
                               visible=True) if i < len(files) else gr.update(value="", visible=False)
                     for i in range(EDITOR_SLOTS)]
            return (draft.workflow, source, "Workflow: " + WORKFLOW_TEXT[draft.workflow], name, draft.notes,
                    f"Loaded `{source}`.", *boxes)

        def on_editor_check(workflow, notes, *texts):
            return check_markdown(draft_from_slots(workflow, texts, notes))

        def on_editor_save(loaded, workflow, name, notes, *texts):
            status_text, entry = editor_save(loaded, workflow, name, texts, notes)
            if entry is None:
                return status_text, loaded, gr.update(), gr.update()
            choices = strategy_choices()
            return status_text, entry, gr.update(choices=choices), gr.update(choices=choices, value=entry)

        def on_editor_preview(workflow, circuit_name, *texts):
            return preview(draft_from_slots(workflow, texts), get_circuit(circuit_name).spec)

        editor_load.click(on_editor_load, [editor_source, editor_workflow, editor_loaded],
                          [editor_workflow, editor_loaded, workflow_md, editor_name, editor_notes, editor_status,
                           *slots])
        editor_check.click(on_editor_check, [editor_workflow, editor_notes, *slots], editor_status)
        editor_save_button.click(on_editor_save, [editor_loaded, editor_workflow, editor_name, editor_notes, *slots],
                                 [editor_status, editor_loaded, strategy, editor_source])
        preview_button.click(on_editor_preview, [editor_workflow, preview_circuit, *slots], preview_text)
    app.queue(default_concurrency_limit=1)  # one run at a time: the GPU serves one model call at a time anyway
    return app


def launch(host: str, port: int) -> None:
    build_app().launch(server_name=host, server_port=port, show_api=False)
