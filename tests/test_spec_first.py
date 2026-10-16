"""The spec_first workflow: testbench from the spec first, design in a separate conversation."""

import dataclasses
import json

import pytest

from samples import ADDER, ADDER_NO_CARRY, TB, TB_FORGETS_CIN, block
from vlsi_agent.agent import run_agent
from vlsi_agent.circuits import get_circuit
from vlsi_agent.config import PROJECT_ROOT, load_config
from vlsi_agent.llm import Completion, FakeLLM

pytestmark = pytest.mark.integration  # every run uses the real iverilog/yosys

_base = load_config()
CONFIG = dataclasses.replace(_base, tools=dataclasses.replace(_base.tools, sim_timeout_s=2))
SPEC_FIRST = PROJECT_ROOT / "prompts" / "examples" / "spec_first"
ADDER4 = get_circuit("adder4")
BROKEN_TB = TB.replace("integer i,", "integer i")  # does not compile


def run(tmp_path, responses, **kwargs):
    fake = FakeLLM(responses)
    result = run_agent(CONFIG, ADDER4, fake, tmp_path / "run", prompts_dir=SPEC_FIRST,
                       score_every_attempt=True, **kwargs)
    return result, fake


def roles(result):
    return [c.role for c in result.calls]


def events(result):
    return [json.loads(line)["event"] for line in (result.run_dir / "events.jsonl").read_text().splitlines()]


def test_testbench_first_then_design(tmp_path):
    result, fake = run(tmp_path, [block(TB), block(ADDER)], max_attempts=3)
    assert roles(result) == ["testbench", "design"]
    assert result.status == "dev_passed" and result.reference.outcome == "pass"
    assert (result.run_dir / "attempt_1" / "tb.v").read_text().strip() == TB.strip()
    assert (result.run_dir / "testbench" / "v1" / "response.md").is_file()
    history = json.loads((result.run_dir / "history.json").read_text())
    assert history["settings"]["workflow"] == "spec_first"
    assert [c["role"] for c in history["calls"]] == ["testbench", "design"]
    assert history["tokens"]["total"] == sum(c["prompt_tokens"] + c["output_tokens"] for c in history["calls"])


def test_unusable_testbench_is_fixed_before_any_design(tmp_path):
    result, fake = run(tmp_path, [block(BROKEN_TB), block(TB), block(ADDER)], max_attempts=3)
    assert roles(result) == ["testbench", "testbench_fix", "design"]
    fix_request = fake.calls[1][-1].content
    assert "The testbench does not compile" in fix_request and "placeholder design" in fix_request
    assert result.status == "dev_passed"


def test_conversations_never_see_each_others_code(tmp_path):
    # Design attempt 1 is wrong; the testbench author confirms its checks; attempt 2 is right.
    result, fake = run(tmp_path, [block(TB), block(ADDER_NO_CARRY), "TESTBENCH OK", block(ADDER)], max_attempts=3)
    assert roles(result) == ["testbench", "design", "testbench_review", "design"]
    assert [a.reference.outcome for a in result.attempts] == ["fail", "pass"]
    assert result.status == "dev_passed"

    design_calls = [fake.calls[1], fake.calls[3]]
    testbench_calls = [fake.calls[0], fake.calls[2]]
    design_text = "\n".join(m.content for call in design_calls for m in call)
    testbench_text = "\n".join(m.content for call in testbench_calls for m in call)
    assert "module tb" not in design_text and "for (i = 0; i < 512" not in design_text
    assert "assign cout = 1'b0" not in testbench_text and "assign {cout, sum}" not in testbench_text

    review_request = fake.calls[2][-1].content
    assert "FAIL: a=1 b=15" in review_request and "RESULT: 256/512 passed" in review_request
    design_feedback = fake.calls[3][-1].content
    assert design_feedback.startswith("Attempt 1 of 3") and "256/512 checks passed" in design_feedback
    assert "testbench_confirmed" in events(result)


@pytest.mark.parametrize("review_reply", [
    "1. The spec says {cout, sum} = a + b + cin, so 16 is right.\nTESTBENCH OK\n" + block(TB),  # OK plus pasted code
    "Checked every value against the specification.\n" + block(TB),                              # same testbench back
    "**TESTBENCH OK**",                                                                         # decorated OK line
])
def test_review_that_keeps_the_testbench_counts_as_confirmed(tmp_path, review_reply):
    result, fake = run(tmp_path, [block(TB), block(ADDER_NO_CARRY), review_reply, block(ADDER)], max_attempts=3)
    assert "testbench_confirmed" in events(result) and "testbench_revised" not in events(result)
    assert roles(result) == ["testbench", "design", "testbench_review", "design"]
    assert result.status == "dev_passed"


def test_review_request_asks_for_the_specification_not_the_design(tmp_path):
    result, fake = run(tmp_path, [block(TB), block(ADDER_NO_CARRY), "TESTBENCH OK", block(ADDER)], max_attempts=3)
    request = fake.calls[2][-1].content
    assert "quote the sentence of the specification" in request
    assert "Never change a value just to match what the design produced" in request


def test_wrong_testbench_is_corrected_on_review(tmp_path):
    # The testbench forgets cin, so it rejects a correct design; on review its author fixes it.
    result, fake = run(tmp_path, [block(TB_FORGETS_CIN), block(ADDER), block(TB)], max_attempts=3)
    assert roles(result) == ["testbench", "design", "testbench_review"]  # no second design call needed
    assert result.status == "dev_passed" and len(result.attempts) == 1
    assert result.attempts[0].feedback == "" and result.reference.outcome == "pass"
    assert "testbench_revised" in events(result)
    assert (result.run_dir / "attempt_1" / "tb.v").read_text().strip() == TB.strip()


def test_a_broken_testbench_revision_is_not_used(tmp_path):
    result, fake = run(tmp_path, [block(TB_FORGETS_CIN), block(ADDER), block(BROKEN_TB), block(ADDER),
                                  "TESTBENCH OK"], max_attempts=2)
    assert roles(result) == ["testbench", "design", "testbench_review", "design", "testbench_review"]
    assert "testbench_revision_rejected" in events(result)
    assert result.status == "attempts_exhausted"
    assert [a.reference.outcome for a in result.attempts] == ["pass", "pass"]  # correct, but the bad testbench stayed


def test_without_a_usable_testbench_only_compile_and_synthesis_are_checked(tmp_path):
    result, fake = run(tmp_path, [block(BROKEN_TB)] * 3 + [block(ADDER)], max_attempts=3)
    assert roles(result) == ["testbench", "testbench_fix", "testbench_fix", "design"]
    assert "testbench_unusable" in events(result)
    assert result.status == "dev_passed" and result.attempts[0].report.sim is None


def test_budget_counts_testbench_calls_too(tmp_path):
    heavy = Completion(block(TB), "fake", prompt_tokens=4000, output_tokens=2000, duration_s=0.0, truncated=False)
    result, fake = run(tmp_path, [heavy, block(ADDER)], max_attempts=3, max_total_tokens=5000)
    assert roles(result) == ["testbench"]
    assert result.status == "token_budget_exhausted" and result.attempts == [] and result.reference is None


def test_design_reply_with_its_own_testbench_is_checked_against_the_spec_testbench(tmp_path):
    lazy_tb = "module tb; initial begin $display(\"RESULT: 1/1 passed\"); $finish; end endmodule"
    result, fake = run(tmp_path, [block(TB), block(ADDER_NO_CARRY + "\n" + lazy_tb), "TESTBENCH OK", block(ADDER)],
                       max_attempts=3)
    assert not result.attempts[0].dev_passed  # its own one-line testbench was ignored
    assert result.attempts[0].report.sim.total == 512
