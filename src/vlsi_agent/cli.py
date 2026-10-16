"""Command-line entry point: `vlsi-agent <command>` or `python -m vlsi_agent <command>`."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from vlsi_agent import __version__, doctor
from vlsi_agent.agent import run_agent
from vlsi_agent.bench import parse_seeds, run_benchmark, summarize
from vlsi_agent.circuits import CircuitError, get_circuit, load_circuits
from vlsi_agent.config import PROJECT_ROOT, Config, ConfigError, load_config, project_path
from vlsi_agent.evaluate import reference_test
from vlsi_agent.llm import FakeLLM, LLMError, Message, OllamaLLM
from vlsi_agent.prompts import PromptError, load_prompts

RUNS_DIR = PROJECT_ROOT / "runs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vlsi-agent",
        description="Generate Verilog with an LLM and check it with Icarus Verilog and Yosys.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="TOML config file (default: config/default.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check that the EDA tools and the LLM are ready")
    sub.add_parser("circuits", help="list the available circuits")

    ev = sub.add_parser("evaluate", help="score a Verilog file with a circuit's hidden reference test")
    ev.add_argument("circuit", help="circuit name, e.g. adder4")
    ev.add_argument("design", type=Path, help="Verilog file containing the design")
    ev.add_argument("--waveform", action="store_true", help="also write reference.vcd")

    fx = sub.add_parser("check-fixtures", help="run every fixture and compare with its expected outcome")
    fx.add_argument("names", nargs="*", metavar="circuit", help="circuits to check (default: all)")

    run = sub.add_parser("run", help="run the agent on a circuit: generate, check, revise, then score")
    run.add_argument("circuit", help="circuit name, e.g. adder4")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--attempts", type=int, help="maximum attempts (default: [agent] max_attempts)")
    mode.add_argument("--one-shot", action="store_true", help="one attempt, no revision (the baseline)")
    run.add_argument("--budget", type=int, metavar="TOKENS", help="token budget (default: [agent] max_total_tokens)")
    run.add_argument("--prompts", type=Path, metavar="DIR", help="prompts folder (default: [agent] prompts_dir)")
    run.add_argument("--fake", type=Path, metavar="JSON",
                     help="replay scripted replies from a JSON list of strings instead of calling the model")
    run.add_argument("--score-every-attempt", action="store_true",
                     help="after the run, score every attempt with the hidden reference test, not just the last")

    bench = sub.add_parser("bench", help="benchmark circuits x strategies x seeds: one-shot vs iterative")
    which = bench.add_mutually_exclusive_group()
    which.add_argument("--split", choices=["dev", "heldout", "all"], default="dev",
                       help="which circuits (default: dev; ask for heldout only for the final comparison)")
    which.add_argument("--circuits", nargs="+", metavar="NAME", help="specific circuits instead of a split")
    bench.add_argument("--seeds", default="1-5", help="e.g. 1-5 or 1,3,7 (default: 1-5)")
    bench.add_argument("--attempts", type=int, help="maximum attempts per run (default: [agent] max_attempts)")
    bench.add_argument("--budget", type=int, metavar="TOKENS", help="token budget per run")
    bench.add_argument("--prompts", nargs="+", type=Path, metavar="DIR",
                       help="one or more strategy folders to compare (default: [agent] prompts_dir)")
    bench.add_argument("--out", type=Path, help="benchmark folder; give an existing one to resume it")
    bench.add_argument("--fake", type=Path, metavar="JSON", help="scripted replies; every run replays the same list")

    ask = sub.add_parser("ask", help="send one prompt to the configured model and print the reply")
    ask.add_argument("prompt")
    ask.add_argument("--system", help="optional system message")
    ask.add_argument("--show-thinking", action="store_true", help="also print the model's reasoning (needs think = true)")
    return parser


def cmd_circuits() -> int:
    circuits = load_circuits()
    width = max(len(c.name) for c in circuits)
    for c in circuits:
        ports = ", ".join(f"{'in' if p.direction == 'input' else 'out'} {p.name}{p.range}" for p in c.spec.ports)
        print(f"{c.name:<{width}}  {c.spec.family:<8} {c.spec.split:<8} {len(c.fixtures)} fixtures  ({ports})")
    return 0


def cmd_evaluate(config: Config, name: str, design: Path, waveform: bool) -> int:
    circuit = get_circuit(name)
    if not design.is_file():
        print(f"design file not found: {design}", file=sys.stderr)
        return 2
    workdir = RUNS_DIR / "evaluate" / f"{circuit.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    result = reference_test(config.tools, circuit, design, workdir, waveform=waveform)

    print(f"circuit:  {circuit.name} ({circuit.spec.split})")
    print(f"design:   {design}")
    print(f"outcome:  {result.outcome.upper()}  {result.detail}")
    if result.sim is not None:
        mismatches = [line for line in result.sim.run.output.splitlines() if line.startswith("MISMATCH")]
        for line in mismatches:
            print(f"  {line}")
    print(f"files:    {workdir}")
    return 0 if result.outcome == "pass" else 1


def cmd_check_fixtures(config: Config, names: list[str]) -> int:
    selected = [get_circuit(n) for n in names] if names else load_circuits()
    wrong = 0
    for circuit in selected:
        print(circuit.name)
        for fixture, expected in circuit.fixtures.items():
            with tempfile.TemporaryDirectory() as tmp:
                result = reference_test(config.tools, circuit, circuit.fixture_path(fixture), Path(tmp))
            ok = result.outcome == expected
            wrong += not ok
            print(f"  {'OK   ' if ok else 'WRONG'} {fixture:<28} expected {expected:<13} got {result.outcome:<13} {result.detail}")
    print("all fixtures behave as expected" if not wrong else f"{wrong} fixture(s) did not get their expected outcome")
    return 0 if not wrong else 1


def _checks_text(e: dict) -> str:
    if not e.get("code_found"):
        return "no Verilog in the reply"
    if not e.get("has_top"):
        return "top module missing"
    if not e.get("compiled"):
        return "design does not compile"
    parts = ["compiled"]
    if not e.get("has_testbench"):
        parts.append("no testbench")
    elif e.get("sim_timed_out"):
        parts.append("testbench timed out")
    elif "tb_total" in e:
        parts.append(f"testbench {e['tb_passed']}/{e['tb_total']}" if e["tb_total"] is not None
                     else "testbench printed no RESULT")
    if e.get("synth_ok"):
        parts.append(f"{e['cells']} cells" + (f", {e['latches']} latches" if e["latches"] else ""))
    elif "synth_ok" in e:
        parts.append("synthesis failed")
    return ", ".join(parts)


def print_event(e: dict) -> None:
    kind, n = e["event"], e.get("attempt")
    lines = {
        "run_started": lambda: f"{e['circuit']} ({e['split']}) with {e['model']}: up to {e['max_attempts']} "
                               f"attempt(s), budget {e['max_total_tokens']} tokens",
        "model_called": lambda: f"attempt {n}: asking the model...",
        "model_replied": lambda: f"attempt {n}: reply of {e['output_tokens']} tokens in {e['duration_s']:.1f} s"
                                 + (" (cut off at the token limit)" if e["truncated"] else ""),
        "checks_done": lambda: f"attempt {n}: development checks {'PASSED' if e['passed'] else 'FAILED'} "
                               f"({_checks_text(e)})",
        "feedback_sent": lambda: f"attempt {n}: sent the check results back to the model ({e['chars']} chars)",
        "budget_exhausted": lambda: f"token budget used up ({e['used']}/{e['budget']}), stopping",
        "model_error": lambda: f"attempt {n}: model error: {e['error']}",
        "reference_scored": lambda: f"hidden reference test on attempt {n}: {e['outcome'].upper()}"
                                    + (f" {e['passed']}/{e['total']}" if e["total"] else "")
                                    + ("" if e["final"] else " (earlier attempt, for comparison)"),
        "run_finished": lambda: f"finished: {e['status']} after {e['attempts']} attempt(s), "
                                f"{e['total_tokens']} tokens, {e['duration_s']:.1f} s",
    }
    if kind in lines:
        print(lines[kind](), flush=True)


class UsageError(ValueError):
    """A command-line value that cannot be used (reported with exit code 2)."""


def load_fake_script(path: Path) -> list[str]:
    try:
        responses = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise UsageError(f"cannot read --fake file: {e}") from None
    if not isinstance(responses, list) or not all(isinstance(r, str) for r in responses):
        raise UsageError("--fake file must contain a JSON list of strings")
    return responses


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    circuit = get_circuit(args.circuit)
    attempts = 1 if args.one_shot else (args.attempts if args.attempts is not None else config.agent.max_attempts)
    if attempts < 1:
        raise UsageError("--attempts must be at least 1")
    llm = FakeLLM(load_fake_script(args.fake)) if args.fake else OllamaLLM(config.llm)

    mode = "oneshot" if attempts == 1 else f"iter{attempts}"
    base = RUNS_DIR / "agent" / f"{time.strftime('%Y%m%d-%H%M%S')}-{circuit.name}-{mode}"
    run_dir, n = base, 1
    while run_dir.exists():
        n += 1
        run_dir = base.with_name(f"{base.name}-{n}")
    prompts = args.prompts.resolve() if args.prompts else None  # relative to where the command runs
    result = run_agent(config, circuit, llm, run_dir, max_attempts=attempts, max_total_tokens=args.budget,
                       prompts_dir=prompts, score_every_attempt=args.score_every_attempt, on_event=print_event)
    print(f"files: {result.run_dir}")
    return 0 if result.reference is not None and result.reference.outcome == "pass" else 1


def cmd_bench(config: Config, args: argparse.Namespace) -> int:
    try:
        seeds = parse_seeds(args.seeds)
    except ValueError as e:
        raise UsageError(f"--seeds: {e}") from None
    attempts = args.attempts if args.attempts is not None else config.agent.max_attempts
    if attempts < 1:
        raise UsageError("--attempts must be at least 1")
    if args.circuits:
        circuits, scope = [get_circuit(name) for name in args.circuits], "custom"
    else:
        circuits, scope = load_circuits(split=None if args.split == "all" else args.split), args.split

    folders = [p.resolve() for p in args.prompts] if args.prompts else [project_path(config.agent.prompts_dir)]
    for folder in folders:
        load_prompts(folder)  # fail now, not halfway through the benchmark
    names = [f.name for f in folders]
    strategies = dict(zip(names if len(set(names)) == len(names) else [str(f) for f in folders], folders))

    script = load_fake_script(args.fake) if args.fake else None

    def make_llm(run_config: Config):
        return FakeLLM(script) if script is not None else OllamaLLM(run_config.llm)

    out = args.out.resolve() if args.out else RUNS_DIR / "bench" / f"{time.strftime('%Y%m%d-%H%M%S')}-{scope}"
    total = len(circuits) * len(strategies) * len(seeds)
    print(f"benchmark: {len(circuits)} circuit(s) x {len(strategies)} strategy(ies) x {len(seeds)} seed(s) = "
          f"{total} runs, up to {attempts} attempts each\nfolder: {out}", flush=True)
    rows = run_benchmark(config, circuits, strategies, seeds, out, make_llm, max_attempts=attempts,
                         max_total_tokens=args.budget, on_progress=lambda line: print(line, flush=True))

    print()
    for name, g in summarize(rows, attempts)["by_strategy"].items():
        print(f"{name}: one-shot {g['oneshot_pass']}, iterative {g['final_pass']} "
              f"(fixed {g['fixed']}, broken {g['broken']}), mean {g['mean_tokens']} tokens per run")
    print(f"tables: {out / 'summary.md'}")
    return 0


def cmd_ask(config: Config, prompt: str, system: str | None, show_thinking: bool) -> int:
    messages = ([Message("system", system)] if system else []) + [Message("user", prompt)]
    try:
        reply = OllamaLLM(config.llm).complete(messages)
    except LLMError as e:
        print(f"LLM error: {e}", file=sys.stderr)
        return 1
    if show_thinking and reply.thinking:
        print(f"[thinking]\n{reply.thinking.strip()}\n[/thinking]\n")
    print(reply.text.strip())
    note = ", TRUNCATED at max_output_tokens" if reply.truncated else ""
    print(f"[{reply.model}: {reply.prompt_tokens} prompt + {reply.output_tokens} output tokens, "
          f"{reply.duration_s:.1f} s{note}]", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    try:
        if args.command == "doctor":
            return doctor.main(config)
        if args.command == "circuits":
            return cmd_circuits()
        if args.command == "evaluate":
            return cmd_evaluate(config, args.circuit, args.design, args.waveform)
        if args.command == "check-fixtures":
            return cmd_check_fixtures(config, args.names)
        if args.command == "run":
            return cmd_run(config, args)
        if args.command == "bench":
            return cmd_bench(config, args)
        if args.command == "ask":
            return cmd_ask(config, args.prompt, args.system, args.show_thinking)
    except CircuitError as e:
        print(f"circuit error: {e}", file=sys.stderr)
        return 2
    except PromptError as e:
        print(f"prompt error: {e}", file=sys.stderr)
        return 2
    except UsageError as e:
        print(str(e), file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
