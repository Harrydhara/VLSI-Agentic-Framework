"""Command-line entry point: `vlsi-agent <command>` or `python -m vlsi_agent <command>`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vlsi_agent import __version__, doctor
from vlsi_agent.config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vlsi-agent",
        description="Generate Verilog with an LLM and check it with Icarus Verilog and Yosys.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="TOML config file (default: config/default.toml)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check that the EDA tools and the LLM are ready")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.command == "doctor":
        return doctor.main(config)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
