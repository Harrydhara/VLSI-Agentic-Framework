"""Command-line entry point: `vlsi-agent <command>` or `python -m vlsi_agent <command>`."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from vlsi_agent import __version__, doctor
from vlsi_agent.circuits import CircuitError, get_circuit, load_circuits
from vlsi_agent.config import PROJECT_ROOT, Config, ConfigError, load_config
from vlsi_agent.evaluate import reference_test
from vlsi_agent.llm import LLMError, Message, OllamaLLM

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

    ask = sub.add_parser("ask", help="send one prompt to the configured model and print the reply")
    ask.add_argument("prompt")
    ask.add_argument("--system", help="optional system message")
    ask.add_argument("--show-thinking", action="store_true", help="also print the model's reasoning (needs think = true)")
    return parser


def cmd_circuits() -> int:
    for c in load_circuits():
        ports = ", ".join(f"{'in' if p.direction == 'input' else 'out'} {p.name}{p.range}" for p in c.spec.ports)
        print(f"{c.name:<10} {c.spec.family:<8} {c.spec.split:<8} {len(c.fixtures)} fixtures  ({ports})")
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
        if args.command == "ask":
            return cmd_ask(config, args.prompt, args.system, args.show_thinking)
    except CircuitError as e:
        print(f"circuit error: {e}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
