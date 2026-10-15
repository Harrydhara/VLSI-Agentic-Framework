import dataclasses
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from vlsi_agent import eda
from vlsi_agent.config import load_config

TOOLS = load_config().tools

ADDER_OK = """\
module adder4(input [3:0] a, input [3:0] b, input cin, output [3:0] sum, output cout);
  assign {cout, sum} = a + b + cin;
endmodule
"""

ADDER_NO_CARRY = """\
module adder4(input [3:0] a, input [3:0] b, input cin, output [3:0] sum, output cout);
  assign sum = a + b + cin;
  assign cout = 1'b0;
endmodule
"""

ADDER_SYNTAX_ERROR = """\
module adder4(input [3:0] a, input [3:0] b, input cin, output [3:0] sum, output cout);
  assign {cout, sum} = a + b + cin
endmodule
"""

ADDER_TB = """\
`timescale 1ns/1ps
module tb;
  reg [3:0] a, b; reg cin; wire [3:0] sum; wire cout;
  integer i, errors = 0;
  adder4 dut(.a(a), .b(b), .cin(cin), .sum(sum), .cout(cout));
  initial begin
    $dumpfile("wave.vcd");
    $dumpvars(0, tb);
    for (i = 0; i < 512; i = i + 1) begin
      {cin, a, b} = i[8:0]; #1;
      if ({cout, sum} !== a + b + cin) errors = errors + 1;
    end
    $display("RESULT: %0d/512 passed", 512 - errors);
    $finish;
  end
endmodule
"""


def put(workdir: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (workdir / name).write_text(text)
    return workdir


def alive(pid: int) -> bool:
    """True if the process exists and is not a zombie waiting to be reaped."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):  # ProcessLookupError if it exits while we read
        return False
    return state not in ("Z", "X")


# --------------------------------------------------------------- no EDA tools needed


def test_capped_output_keeps_start_and_end():
    capture = eda._CappedOutput(10)
    capture.feed(b"0123456789abcdefghij")
    text, truncated = capture.text()
    assert truncated
    assert text.startswith("01234") and text.endswith("fghij")
    assert "[10 bytes of output omitted]" in text


def test_capped_output_under_limit_is_untouched():
    capture = eda._CappedOutput(100)
    capture.feed(b"hello ")
    capture.feed(b"world")
    assert capture.text() == ("hello world", False)


def test_parse_diagnostics_from_real_iverilog_messages():
    output = (
        "design.v:3: syntax error\n"
        "I give up.\n"
        "design.v:2: error: Unknown module type: foo\n"
        "design.v:7: warning: Port 1 (a) of m expects 4 bits, got 1.\n"
        "design.v:9: sorry: constant selects in always_* processes are not supported.\n"
        "2 error(s) during elaboration.\n"
    )
    found = eda.parse_diagnostics(output)
    assert [(d.line, d.severity) for d in found] == [(3, "error"), (2, "error"), (7, "warning"), (9, "error")]
    assert found[1].message == "Unknown module type: foo"
    assert all(d.file == "design.v" for d in found)


def test_parse_result_line_uses_the_last_one():
    assert eda.parse_result_line("RESULT: 1/4 passed\nmore\nRESULT: 3/4 passed\n") == (3, 4)
    assert eda.parse_result_line("no summary here") == (None, None)


def test_parse_result_line_with_token_only_trusts_that_token():
    output = "RESULT: 9/9 passed\nRESULT abc123: 2/9 passed\nRESULT other: 9/9 passed\n"
    assert eda.parse_result_line(output, token="abc123") == (2, 9)
    assert eda.parse_result_line(output, token="missing") == (None, None)
    assert eda.parse_result_line(output) == (9, 9)  # no token: plain lines only


def test_timeout_kills_the_whole_process_group(tmp_path):
    script = ("import subprocess, time; p = subprocess.Popen(['sleep', '30']); "
              "print(p.pid, flush=True); time.sleep(30)")
    run = eda.run_tool([sys.executable, "-c", script], tmp_path, timeout_s=1, max_output_bytes=4096)
    assert run.timed_out and run.returncode is None
    assert run.duration_s < 5
    child = int(run.output.split()[0])
    deadline = time.monotonic() + 3
    while alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(child), "the grandchild `sleep 30` survived the timeout"


def test_stray_process_holding_the_output_cannot_hang_the_run(tmp_path):
    # The grandchild starts its own session, so the group kill cannot reach it, and it
    # inherits the output pipe. run_tool must still return soon after the timeout.
    script = ("import subprocess, time; p = subprocess.Popen(['sleep', '30'], start_new_session=True); "
              "print(p.pid, flush=True); time.sleep(30)")
    run = eda.run_tool([sys.executable, "-c", script], tmp_path, timeout_s=1, max_output_bytes=4096)
    stray = int(run.output.split()[0])
    try:
        assert run.timed_out
        assert run.duration_s < 8
        assert "kept its output open" in run.output
    finally:
        os.kill(stray, signal.SIGKILL)


def test_huge_output_is_capped(tmp_path):
    script = "import sys\nfor i in range(20000): sys.stdout.write('x' * 500 + '\\n')"
    run = eda.run_tool([sys.executable, "-c", script], tmp_path, timeout_s=30, max_output_bytes=8192)
    assert run.returncode == 0 and run.truncated
    assert len(run.output) < 8192 + 200


def test_missing_tool_raises_instead_of_failing_the_design(tmp_path):
    with pytest.raises(eda.EDAToolError, match="cannot start"):
        eda.run_tool(["/nonexistent/tool"], tmp_path, timeout_s=5, max_output_bytes=1024)


@pytest.mark.parametrize("name", ["../design.v", "a b.v", "x.v;rm", "/etc/passwd"])
def test_unsafe_file_names_are_rejected(tmp_path, name):
    with pytest.raises(ValueError, match="unsafe file name"):
        eda.compile_verilog(TOOLS, tmp_path, [name])


# --------------------------------------------------------------- real iverilog / yosys

integration = pytest.mark.integration


@integration
def test_correct_adder_compiles_passes_and_synthesizes(tmp_path):
    put(tmp_path, {"design.v": ADDER_OK, "tb.v": ADDER_TB})

    comp = eda.compile_verilog(TOOLS, tmp_path, ["design.v", "tb.v"])
    assert comp.ok, comp.run.output
    assert comp.errors == ()

    sim = eda.simulate(TOOLS, tmp_path)
    assert sim.completed and (sim.passed, sim.total) == (512, 512)
    assert sim.all_passed
    assert sim.vcd_files == ("wave.vcd",)

    syn = eda.synthesize(TOOLS, tmp_path, ["design.v"], top="adder4")
    assert syn.ok, syn.run.output
    assert syn.cells and syn.cells > 0
    assert sum(syn.cells_by_type.values()) == syn.cells
    assert syn.latches == 0


@integration
def test_syntax_error_is_reported_with_its_line(tmp_path):
    put(tmp_path, {"design.v": ADDER_SYNTAX_ERROR})
    comp = eda.compile_verilog(TOOLS, tmp_path, ["design.v"])
    assert not comp.ok
    assert comp.run.returncode != 0
    assert [(e.file, e.line, e.message) for e in comp.errors] == [("design.v", 3, "syntax error")]


@integration
def test_unknown_module_is_reported(tmp_path):
    put(tmp_path, {"design.v": "module m(input a, output y);\n  foo u1(.a(a), .y(y));\nendmodule\n"})
    comp = eda.compile_verilog(TOOLS, tmp_path, ["design.v"])
    assert not comp.ok
    assert any("Unknown module type: foo" in e.message for e in comp.errors)


@integration
def test_failed_compile_removes_the_old_sim_file(tmp_path):
    put(tmp_path, {"design.v": ADDER_OK})
    assert eda.compile_verilog(TOOLS, tmp_path, ["design.v"]).ok
    put(tmp_path, {"design.v": ADDER_SYNTAX_ERROR})
    assert not eda.compile_verilog(TOOLS, tmp_path, ["design.v"]).ok
    assert not (tmp_path / eda.COMPILED_NAME).exists()


@integration
def test_wrong_carry_compiles_but_fails_the_tests(tmp_path):
    put(tmp_path, {"design.v": ADDER_NO_CARRY, "tb.v": ADDER_TB})
    assert eda.compile_verilog(TOOLS, tmp_path, ["design.v", "tb.v"]).ok
    sim = eda.simulate(TOOLS, tmp_path)
    assert sim.completed
    assert sim.total == 512 and sim.passed < 512
    assert not sim.all_passed


@integration
def test_hung_simulation_times_out_even_after_printing_result(tmp_path):
    tb = ("module tb; reg clk = 0; always #5 clk = ~clk;\n"
          "  initial $display(\"RESULT: 1/1 passed\");\nendmodule\n")
    put(tmp_path, {"tb.v": tb})
    tools = dataclasses.replace(TOOLS, sim_timeout_s=1)
    assert eda.compile_verilog(tools, tmp_path, ["tb.v"]).ok
    sim = eda.simulate(tools, tmp_path)
    assert sim.run.timed_out and not sim.completed
    assert not sim.all_passed
    assert sim.run.duration_s < 5
    assert "killed: still running after 1 s" in sim.run.output


@integration
def test_runaway_simulation_output_is_capped(tmp_path):
    put(tmp_path, {"tb.v": "module tb; initial forever $display(\"spam spam spam spam\"); endmodule\n"})
    tools = dataclasses.replace(TOOLS, sim_timeout_s=1, max_log_kb=16)
    assert eda.compile_verilog(tools, tmp_path, ["tb.v"]).ok
    sim = eda.simulate(tools, tmp_path)
    assert sim.run.timed_out and sim.run.truncated
    assert len(sim.run.output) < 16 * 1024 + 200


@integration
def test_testbench_without_result_line_is_not_a_pass(tmp_path):
    put(tmp_path, {"tb.v": "module tb; initial begin $display(\"hello\"); $finish; end endmodule\n"})
    assert eda.compile_verilog(TOOLS, tmp_path, ["tb.v"]).ok
    sim = eda.simulate(TOOLS, tmp_path)
    assert sim.completed
    assert (sim.passed, sim.total) == (None, None)
    assert not sim.all_passed


@integration
def test_fatal_does_not_count_as_completed(tmp_path):
    put(tmp_path, {"tb.v": "module tb; initial $fatal(1, \"boom\"); endmodule\n"})
    assert eda.compile_verilog(TOOLS, tmp_path, ["tb.v"]).ok
    sim = eda.simulate(TOOLS, tmp_path)
    assert sim.run.returncode == 1 and not sim.completed
    assert "boom" in sim.run.output


@integration
def test_latch_is_counted(tmp_path):
    put(tmp_path, {"design.v": "module l(input en, input d, output reg q);\n  always @* if (en) q = d;\nendmodule\n"})
    syn = eda.synthesize(TOOLS, tmp_path, ["design.v"], top="l")
    assert syn.ok
    assert syn.latches == 1


@integration
def test_wrong_top_module_fails_synthesis(tmp_path):
    put(tmp_path, {"design.v": ADDER_OK})
    syn = eda.synthesize(TOOLS, tmp_path, ["design.v"], top="nosuch")
    assert not syn.ok and syn.cells is None
    assert "nosuch" in syn.run.output
