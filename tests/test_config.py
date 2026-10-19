import pytest

from vlsi_agent.config import DEFAULT_CONFIG_PATH, ConfigError, load_config

VALID = """
[llm]
base_url = "http://localhost:11434"
model = "gemma4:26b"
request_timeout_s = 300
temperature = 0.2
seed = 42
think = false
keep_alive = "10m"
max_output_tokens = 4096

[agent]
max_attempts = 3
max_total_tokens = 50000
prompts_dir = "prompts/default"
feedback_max_chars = 3000

[tools]
iverilog = "/usr/bin/iverilog"
vvp = "/usr/bin/vvp"
yosys = "/usr/local/bin/yosys"
compile_timeout_s = 30
sim_timeout_s = 10
synth_timeout_s = 60
max_log_kb = 64
"""


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_default_config_loads():
    config = load_config()
    assert config.source == DEFAULT_CONFIG_PATH
    assert config.llm.model == "gemma4:26b"
    assert config.tools.yosys == "/usr/local/bin/yosys"


def test_valid_file_loads_and_int_becomes_float(tmp_path):
    config = load_config(write(tmp_path, VALID))
    assert config.llm.request_timeout_s == 300.0
    assert isinstance(config.llm.request_timeout_s, float)


def test_unknown_key_is_rejected(tmp_path):
    text = VALID.replace('model = "gemma4:26b"', 'model = "gemma4:26b"\nmodle = "typo"')
    with pytest.raises(ConfigError, match=r"\[llm\] unknown key\(s\): modle"):
        load_config(write(tmp_path, text))


def test_missing_key_is_rejected(tmp_path):
    text = VALID.replace('vvp = "/usr/bin/vvp"\n', "")
    with pytest.raises(ConfigError, match=r"\[tools\] missing key\(s\): vvp"):
        load_config(write(tmp_path, text))


def test_unknown_section_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"unknown section\(s\): extra"):
        load_config(write(tmp_path, VALID + "\n[extra]\nx = 1\n"))


@pytest.mark.parametrize("bad_value", ['"fast"', "true"])
def test_wrong_type_is_rejected(tmp_path, bad_value):
    text = VALID.replace("request_timeout_s = 300", f"request_timeout_s = {bad_value}")
    with pytest.raises(ConfigError, match=r"request_timeout_s should be float"):
        load_config(write(tmp_path, text))


@pytest.mark.parametrize("old,new,message", [
    ("think = false", 'think = "no"', "think should be bool"),
    ("think = false", "think = 0", "think should be bool"),
    ("seed = 42", "seed = 4.2", "seed should be int"),
    ("seed = 42", "seed = true", "seed should be int"),
])
def test_bool_and_int_are_not_interchangeable(tmp_path, old, new, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, VALID.replace(old, new)))


def test_invalid_toml_names_the_file(tmp_path):
    path = write(tmp_path, "[llm\nmodel = ")
    with pytest.raises(ConfigError, match="invalid TOML") as excinfo:
        load_config(path)
    assert str(path) in str(excinfo.value)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(tmp_path / "nope.toml")


def test_ollama_url_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("VLSI_AGENT_OLLAMA_URL", raising=False)
    assert load_config(write(tmp_path, VALID)).llm.base_url == "http://localhost:11434"
    monkeypatch.setenv("VLSI_AGENT_OLLAMA_URL", "http://ollama:11434/")
    config = load_config(write(tmp_path, VALID))
    assert config.llm.base_url == "http://ollama:11434" and config.llm.model == "gemma4:26b"
    monkeypatch.setenv("VLSI_AGENT_OLLAMA_URL", "ollama:11434")
    with pytest.raises(ConfigError, match="VLSI_AGENT_OLLAMA_URL should be an http"):
        load_config(write(tmp_path, VALID))
