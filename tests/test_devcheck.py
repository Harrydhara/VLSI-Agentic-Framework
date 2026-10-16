import dataclasses

import pytest

from samples import ADDER, ADDER_NO_CARRY, TB
from vlsi_agent.config import load_config
from vlsi_agent.devcheck import Sources, run_dev_checks, separate, split_modules

TOOLS = dataclasses.replace(load_config().tools, sim_timeout_s=2)


# --------------------------------------------------------------- splitting (no tools)


def test_split_modules_finds_each_module():
    assert [name for name, _ in split_modules(f"// header\n{ADDER}\n\n{TB}\n")] == ["adder4", "tb"]


def test_separate_puts_helpers_with_the_design_and_tb_apart():
    helper = "module fa(input a, output y);\n  assign y = a;\nendmodule"
    src = separate(f"`timescale 1ns/1ps\n{helper}\n{ADDER}\n{TB}", top="adder4")
    assert src.module_names == ("fa", "adder4", "tb")
    assert src.design.startswith("`timescale 1ns/1ps\n") and "module fa" in src.design and "module adder4" in src.design
    assert src.testbench.startswith("`timescale 1ns/1ps\n") and "module tb" in src.testbench
    assert "module tb" not in src.design


def test_testbench_with_another_name_is_found_by_finish():
    src = separate(ADDER + "\n" + TB.replace("module tb;", "module adder4_test;"), top="adder4")
    assert src.testbench is not None and "adder4_test" in src.testbench
    assert "adder4_test" not in src.design


def test_repeated_module_keeps_its_last_definition():
    first = ADDER.replace("a + b + cin", "a - b")  # an early draft the model rewrote later
    helper_v1 = "module fa(input a, output y);\n  assign y = a;\nendmodule"
    helper_v2 = helper_v1.replace("assign y = a;", "assign y = ~a;")
    code = f"{helper_v1}\n{first}\n{helper_v2}\n{ADDER}\n{TB}\n{TB}"
    assert [name for name, _ in split_modules(code)] == ["fa", "adder4", "tb"]
    src = separate(code, top="adder4")
    assert src.module_names == ("fa", "adder4", "tb")
    assert "a + b + cin" in src.design and "a - b" not in src.design
    assert "~a" in src.design and src.design.count("module fa") == 1
    assert src.testbench.count("module tb") == 1


def test_cut_off_rewrite_keeps_the_last_complete_definition():
    code = f"{ADDER}\n\nmodule adder4(input [3:0] a, input [3:0] b,"  # reply ended mid-module
    assert separate(code, top="adder4").design.count("module adder4") == 1


def test_no_testbench():
    src = separate(ADDER, top="adder4")
    assert src.testbench is None and "module adder4" in src.design


# --------------------------------------------------------------- checks (real tools)

integration = pytest.mark.integration


@integration
def test_good_design_and_testbench_pass(tmp_path):
    report = run_dev_checks(TOOLS, tmp_path, separate(ADDER + "\n" + TB, "adder4"), "adder4")
    assert report.passed and report.feedback == ""
    assert report.sim.passed == 512 and report.synth.cells > 0
    assert (tmp_path / "design.v").is_file() and (tmp_path / "tb.v").is_file()


@integration
def test_failing_testbench_checks_are_fed_back(tmp_path):
    report = run_dev_checks(TOOLS, tmp_path, separate(ADDER_NO_CARRY + "\n" + TB, "adder4"), "adder4")
    assert not report.passed
    assert "The testbench reported failures (256/512 checks passed)" in report.feedback
    assert "FAIL: a=1 b=15" in report.feedback
    assert "$finish called" not in report.feedback  # simulator noise is filtered out


@integration
def test_compile_error_is_fed_back_with_its_line(tmp_path):
    broken = ADDER.replace("a + b + cin;", "a + b + cin")
    report = run_dev_checks(TOOLS, tmp_path, separate(broken + "\n" + TB, "adder4"), "adder4")
    assert not report.passed and report.tb_compile is None and report.sim is None
    assert "The design does not compile" in report.feedback
    assert "design.v:3: error: syntax error" in report.feedback


@integration
def test_missing_top_module(tmp_path):
    report = run_dev_checks(TOOLS, tmp_path, separate(ADDER.replace("adder4", "add4"), "adder4"), "adder4")
    assert not report.passed and not report.has_top
    assert "no module named adder4 (modules found: add4)" in report.feedback


@integration
def test_missing_testbench_is_reported_but_synthesis_still_runs(tmp_path):
    report = run_dev_checks(TOOLS, tmp_path, separate(ADDER, "adder4"), "adder4")
    assert not report.passed and not report.has_testbench
    assert "No testbench was found" in report.feedback
    assert report.synth is not None and report.synth.ok


@integration
def test_testbench_without_finish_times_out(tmp_path):
    tb = TB.replace("$finish;", "").replace("initial begin", "always #5 a = a; initial begin")
    report = run_dev_checks(TOOLS, tmp_path, Sources(ADDER + "\n", tb + "\n", ("adder4", "tb")), "adder4")
    assert not report.passed
    assert "did not finish within 2 s" in report.feedback


@integration
def test_latch_is_reported(tmp_path):
    latchy = """\
module adder4(input [3:0] a, input [3:0] b, input cin, output reg [3:0] sum, output reg cout);
  always @(*) if (cin) {cout, sum} = a + b + 1;
endmodule"""
    report = run_dev_checks(TOOLS, tmp_path, separate(latchy + "\n" + TB, "adder4"), "adder4")
    assert not report.passed
    assert "Synthesis inferred 5 latch(es)" in report.feedback


@integration
def test_feedback_is_capped(tmp_path):
    report = run_dev_checks(TOOLS, tmp_path, separate(ADDER_NO_CARRY + "\n" + TB, "adder4"), "adder4",
                            max_chars=80)
    assert report.feedback.endswith("(feedback cut to 80 characters)")
    assert len(report.feedback) < 80 + 60
