import dataclasses
import json
import shutil

import pytest

from samples import ADDER, ADDER_NO_CARRY, reply
from vlsi_agent.agent import STATUSES, run_agent
from vlsi_agent.circuits import OUTCOMES, get_circuit
from vlsi_agent.config import PROJECT_ROOT, load_config
from vlsi_agent.llm import Completion, FakeLLM, OllamaLLM
from vlsi_agent.prompts import PromptError

pytestmark = pytest.mark.integration  # every run uses the real iverilog/yosys

_base = load_config()
CONFIG = dataclasses.replace(_base, tools=dataclasses.replace(_base.tools, sim_timeout_s=2))
ADDER4 = get_circuit("adder4")
GOOD = reply(ADDER)
BAD = reply(ADDER_NO_CARRY)


def run(tmp_path, responses, **kwargs):
    fake = FakeLLM(responses)
    result = run_agent(CONFIG, ADDER4, fake, tmp_path / "run", **kwargs)
    return result, fake


def test_one_shot_pass(tmp_path):
    result, fake = run(tmp_path, [GOOD], max_attempts=1)
    assert result.status == "dev_passed" and len(fake.calls) == 1
    assert result.reference.outcome == "pass" and result.reference.passed == 512
    assert result.final_attempt == 1


def test_one_shot_does_not_revise(tmp_path):
    result, fake = run(tmp_path, [BAD], max_attempts=1)
    assert result.status == "attempts_exhausted" and len(fake.calls) == 1
    assert result.reference.outcome == "fail"


def test_revision_uses_the_feedback_and_fixes_the_design(tmp_path):
    result, fake = run(tmp_path, [BAD, GOOD], max_attempts=3)
    assert result.status == "dev_passed" and len(result.attempts) == 2
    assert result.reference.outcome == "pass"

    second = fake.calls[1]
    assert [m.role for m in second] == ["system", "user", "assistant", "user"]
    assert second[2].content == BAD  # the model sees its own previous reply
    feedback = second[3].content
    assert feedback.startswith("Attempt 1 of 3 did not pass")
    assert "256/512 checks passed" in feedback and "FAIL: a=1 b=15" in feedback


def test_attempt_limit_stops_the_loop(tmp_path):
    # FakeLLM raises on a 4th call, so status would become llm_error if the loop overran.
    result, fake = run(tmp_path, [BAD, BAD, BAD], max_attempts=3)
    assert result.status == "attempts_exhausted"
    assert len(fake.calls) == 3 and len(result.attempts) == 3


def test_token_budget_stops_before_the_next_call(tmp_path):
    heavy = Completion(BAD, "fake", prompt_tokens=4000, output_tokens=2000, duration_s=0.0, truncated=False)
    result, fake = run(tmp_path, [heavy] * 5, max_attempts=5, max_total_tokens=10000)
    assert result.status == "token_budget_exhausted"
    assert len(fake.calls) == 2 and result.total_tokens == 12000
    events = [json.loads(line) for line in (result.run_dir / "events.jsonl").read_text().splitlines()]
    assert any(e["event"] == "budget_exhausted" and e["used"] == 12000 and e["budget"] == 10000 for e in events)


def test_reply_without_code_gets_feedback(tmp_path):
    result, fake = run(tmp_path, ["Sorry, I cannot write Verilog today.", GOOD], max_attempts=2)
    assert result.status == "dev_passed" and result.final_attempt == 2
    assert result.attempts[0].code is None
    assert "No Verilog module was found" in fake.calls[1][3].content


def test_truncated_reply_is_reported(tmp_path):
    cut = Completion("```verilog\nmodule adder4(input [3:0] a,", "fake", 100, 4096, 0.0, truncated=True)
    result, fake = run(tmp_path, [cut, GOOD], max_attempts=2)
    assert result.status == "dev_passed"
    assert "cut off at the output token limit" in fake.calls[1][3].content


def test_model_error_stops_the_run_but_history_is_written(tmp_path):
    result, fake = run(tmp_path, [BAD], max_attempts=3)  # the 2nd call has no scripted reply
    assert result.status == "llm_error" and "no scripted response" in result.error
    history = json.loads((result.run_dir / "history.json").read_text())
    assert history["status"] == "llm_error" and len(history["attempts"]) == 1
    assert history["reference"]["outcome"] == "fail"  # attempt 1 was still scored


def test_reference_test_never_reaches_the_model(tmp_path):
    result, fake = run(tmp_path, [BAD, BAD, GOOD], max_attempts=3)
    assert result.reference.outcome == "pass"  # the reference test did run...
    sent = "\n".join(m.content for call in fake.calls for m in call)
    for secret in ("ref_tb", "MISMATCH", "__RESULT_TOKEN__", "reference"):  # ...but none of it was sent
        assert secret not in sent


def test_run_folder_has_events_history_and_attempt_files(tmp_path):
    result, _ = run(tmp_path, [BAD, GOOD], max_attempts=3)
    events = [json.loads(line) for line in (result.run_dir / "events.jsonl").read_text().splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
    assert kinds.count("model_called") == 2 and "feedback_sent" in kinds and "reference_scored" in kinds
    assert all(events[i]["t"] <= events[i + 1]["t"] for i in range(len(events) - 1))

    history = json.loads((result.run_dir / "history.json").read_text())
    assert history["circuit"] == "adder4" and history["status"] == "dev_passed"
    assert [a["dev_passed"] for a in history["attempts"]] == [False, True]
    assert history["attempts"][0]["checks"]["tb_passed"] == 256
    assert history["reference"] == {"outcome": "pass", "passed": 512, "total": 512,
                                    "detail": "512/512 checks passed"}
    for name in ("response.md", "design.v", "tb.v", "feedback.txt", "sim.log"):
        assert (result.run_dir / "attempt_1" / name).is_file(), name
    assert (result.run_dir / "reference" / "design.v").is_file()


def test_student_prompts_folder_is_used(tmp_path):
    mine = tmp_path / "my_prompts"
    shutil.copytree(PROJECT_ROOT / "prompts" / "default", mine)
    (mine / "task.txt").write_text("STUDENT STRATEGY\n{{description}}\n{{interface}}\n")
    result, fake = run(tmp_path, [GOOD], max_attempts=1, prompts_dir=mine)
    assert fake.calls[0][1].content.startswith("STUDENT STRATEGY")
    assert result.status == "dev_passed"


def test_bad_prompt_template_fails_before_any_model_call(tmp_path):
    mine = tmp_path / "my_prompts"
    shutil.copytree(PROJECT_ROOT / "prompts" / "default", mine)
    (mine / "feedback.txt").write_text("{{feedbak}}")
    fake = FakeLLM([GOOD])
    with pytest.raises(PromptError):
        run_agent(CONFIG, ADDER4, fake, tmp_path / "run", prompts_dir=mine)
    assert fake.calls == []


def test_zero_attempts_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="at least 1"):
        run(tmp_path, [GOOD], max_attempts=0)


def test_real_model_run_completes(tmp_path):
    result = run_agent(CONFIG, ADDER4, OllamaLLM(CONFIG.llm), tmp_path / "run", max_attempts=2)
    assert result.status in STATUSES and result.status != "llm_error", result.error
    assert result.reference is not None and result.reference.outcome in OUTCOMES
    assert (result.run_dir / "history.json").is_file()
