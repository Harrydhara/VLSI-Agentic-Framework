"""Circuit specifications, hidden reference tests, and fixtures with known outcomes.

Each circuit lives in circuits/<name>/:

    spec.toml        what the agent is given: a description and the exact ports
    reference_tb.v   hidden reference test, used only for final scoring
    fixtures/
        expected.toml    fixture file -> expected outcome of the reference test
        correct.v        at least one design expected to "pass"
        ...              deliberately faulty designs

The split field separates circuits for tuning prompts ("dev") from circuits kept
aside for the final comparison ("heldout").
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from vlsi_agent.config import PROJECT_ROOT

CIRCUITS_DIR = PROJECT_ROOT / "circuits"
SPLITS = ("dev", "heldout")
DIRECTIONS = ("input", "output")
OUTCOMES = ("pass", "fail", "timeout", "compile_error", "error")

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SPEC_KEYS = {"name", "family", "split", "top", "description", "ports"}
_PORT_KEYS = {"name", "direction", "width"}


class CircuitError(ValueError):
    """A circuit folder is incomplete or its spec/fixture files are invalid."""


@dataclass(frozen=True)
class Port:
    name: str
    direction: str  # "input" or "output"
    width: int

    @property
    def range(self) -> str:
        return f"[{self.width - 1}:0]" if self.width > 1 else ""


@dataclass(frozen=True)
class Spec:
    name: str
    family: str
    split: str
    top: str
    description: str
    ports: tuple[Port, ...]

    def interface(self) -> str:
        """The module header a design must match exactly."""
        range_width = max(len(p.range) for p in self.ports)
        lines = [f"    {p.direction:<6} {p.range:<{range_width}} {p.name}".rstrip() for p in self.ports]
        return f"module {self.top}(\n" + ",\n".join(lines) + "\n);"


@dataclass(frozen=True)
class Circuit:
    spec: Spec
    root: Path
    fixtures: dict[str, str]  # file name in fixtures/ -> expected outcome

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def reference_tb(self) -> Path:
        return self.root / "reference_tb.v"

    def fixture_path(self, name: str) -> Path:
        return self.root / "fixtures" / name


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise CircuitError(f"missing file: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise CircuitError(f"{path}: invalid TOML: {e}") from None


def _check_keys(where: str, data: dict, expected: set[str]) -> None:
    unknown = sorted(set(data) - expected)
    missing = sorted(expected - set(data))
    if unknown:
        raise CircuitError(f"{where}: unknown key(s): {', '.join(unknown)}")
    if missing:
        raise CircuitError(f"{where}: missing key(s): {', '.join(missing)}")


def _parse_port(where: str, data: object) -> Port:
    if not isinstance(data, dict):
        raise CircuitError(f"{where}: each port must be a table")
    _check_keys(where, data, _PORT_KEYS)
    name, direction, width = data["name"], data["direction"], data["width"]
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        raise CircuitError(f"{where}: invalid port name {name!r}")
    if direction not in DIRECTIONS:
        raise CircuitError(f"{where}: port {name} direction must be one of {DIRECTIONS}, got {direction!r}")
    if not isinstance(width, int) or isinstance(width, bool) or width < 1:
        raise CircuitError(f"{where}: port {name} width must be a positive integer, got {width!r}")
    return Port(name, direction, width)


def _parse_spec(path: Path, folder_name: str) -> Spec:
    data = _read_toml(path)
    _check_keys(str(path), data, _SPEC_KEYS)
    for key in ("name", "family", "split", "top", "description"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise CircuitError(f"{path}: {key} must be a non-empty string")
    if data["name"] != folder_name:
        raise CircuitError(f"{path}: name {data['name']!r} must match its folder {folder_name!r}")
    if data["split"] not in SPLITS:
        raise CircuitError(f"{path}: split must be one of {SPLITS}, got {data['split']!r}")
    if not _IDENTIFIER.match(data["top"]):
        raise CircuitError(f"{path}: invalid top module name {data['top']!r}")
    if not isinstance(data["ports"], list) or not data["ports"]:
        raise CircuitError(f"{path}: ports must be a non-empty list of [[ports]] tables")

    ports = tuple(_parse_port(f"{path} port {i + 1}", p) for i, p in enumerate(data["ports"]))
    names = [p.name for p in ports]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise CircuitError(f"{path}: duplicate port name(s): {', '.join(duplicates)}")
    return Spec(data["name"], data["family"], data["split"], data["top"],
                data["description"].strip(), ports)


def _parse_fixtures(root: Path) -> dict[str, str]:
    path = root / "fixtures" / "expected.toml"
    data = _read_toml(path)
    for name, outcome in data.items():
        if outcome not in OUTCOMES:
            raise CircuitError(f"{path}: {name} expects {outcome!r}, must be one of {OUTCOMES}")
        if not (root / "fixtures" / name).is_file():
            raise CircuitError(f"{path}: fixture {name} not found in {root / 'fixtures'}")
    if "pass" not in data.values():
        raise CircuitError(f"{path}: needs at least one fixture expected to pass (a known-correct design)")
    return dict(data)


def load_circuit(root: Path | str) -> Circuit:
    root = Path(root)
    spec = _parse_spec(root / "spec.toml", root.name)
    circuit = Circuit(spec, root, _parse_fixtures(root))
    if not circuit.reference_tb.is_file():
        raise CircuitError(f"missing file: {circuit.reference_tb}")
    return circuit


def load_circuits(root: Path | str = CIRCUITS_DIR, split: str | None = None) -> list[Circuit]:
    """Load every circuit folder under root, sorted by name, optionally one split only."""
    if split is not None and split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
    circuits = [load_circuit(p) for p in sorted(Path(root).iterdir()) if (p / "spec.toml").is_file()]
    return [c for c in circuits if split is None or c.spec.split == split]


def get_circuit(name: str, root: Path | str = CIRCUITS_DIR) -> Circuit:
    path = Path(root) / name
    if not (path / "spec.toml").is_file():
        known = ", ".join(c.name for c in load_circuits(root)) or "none"
        raise CircuitError(f"unknown circuit {name!r} (known: {known})")
    return load_circuit(path)
