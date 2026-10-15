"""Load and validate the TOML config file.

Validation is strict: a missing key, an unknown key (usually a typo), or a value of
the wrong type is an error, so a mistake in an edited config fails loudly instead of
silently falling back to a default.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.toml"


class ConfigError(ValueError):
    """The config file is unreadable, incomplete, or has a bad value."""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    request_timeout_s: float


@dataclass(frozen=True)
class ToolsConfig:
    iverilog: str
    vvp: str
    yosys: str
    compile_timeout_s: float
    sim_timeout_s: float
    synth_timeout_s: float
    max_log_kb: int


@dataclass(frozen=True)
class Config:
    llm: LLMConfig
    tools: ToolsConfig
    source: Path


_SECTIONS = {"llm": LLMConfig, "tools": ToolsConfig}


def _check_type(section: str, key: str, value: object, type_name: str) -> object:
    # bool is a subclass of int in Python; never accept it for a number.
    if type_name == "str" and isinstance(value, str):
        return value
    if type_name == "float" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if type_name == "int" and isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ConfigError(f"[{section}] {key} should be {type_name}, got {type(value).__name__} {value!r}")


def _build_section(section: str, cls: type, data: object) -> object:
    if not isinstance(data, dict):
        raise ConfigError(f"[{section}] must be a table")
    expected = {f.name: f for f in fields(cls)}
    unknown = sorted(set(data) - set(expected))
    if unknown:
        raise ConfigError(f"[{section}] unknown key(s): {', '.join(unknown)}")
    missing = sorted(set(expected) - set(data))
    if missing:
        raise ConfigError(f"[{section}] missing key(s): {', '.join(missing)}")
    values = {name: _check_type(section, name, data[name], f.type) for name, f in expected.items()}
    return cls(**values)


def load_config(path: Path | str | None = None) -> Config:
    """Read a config file (default: config/default.toml) and return a validated Config."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from None

    unknown = sorted(set(data) - set(_SECTIONS))
    if unknown:
        raise ConfigError(f"{path}: unknown section(s): {', '.join(unknown)}")
    missing = sorted(set(_SECTIONS) - set(data))
    if missing:
        raise ConfigError(f"{path}: missing section(s): {', '.join(missing)}")

    sections = {name: _build_section(name, cls, data[name]) for name, cls in _SECTIONS.items()}
    return Config(**sections, source=path)
