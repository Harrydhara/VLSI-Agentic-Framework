"""Verilog snippets and scripted model replies shared by the tests."""

ADDER = """\
module adder4(input [3:0] a, input [3:0] b, input cin, output [3:0] sum, output cout);
  assign {cout, sum} = a + b + cin;
endmodule"""

ADDER_NO_CARRY = ADDER.replace("assign {cout, sum} = a + b + cin;", "assign sum = a + b + cin;\n  assign cout = 1'b0;")

TB = """\
module tb;
  reg [3:0] a, b; reg cin; wire [3:0] sum; wire cout;
  integer i, passed = 0, total = 0;
  adder4 dut(.a(a), .b(b), .cin(cin), .sum(sum), .cout(cout));
  initial begin
    for (i = 0; i < 512; i = i + 1) begin
      {cin, a, b} = i[8:0]; #1;
      total = total + 1;
      if ({cout, sum} === a + b + cin) passed = passed + 1;
      else if (total - passed <= 3) $display("FAIL: a=%0d b=%0d cin=%0d got %0d", a, b, cin, {cout, sum});
    end
    $display("RESULT: %0d/%0d passed", passed, total);
    $finish;
  end
endmodule"""


TB_FORGETS_CIN = TB.replace("{cout, sum} === a + b + cin", "{cout, sum} === a + b")  # a wrong testbench


def block(code: str) -> str:
    """A model reply with one Verilog code block."""
    return f"Here it is:\n```verilog\n{code}\n```\n"


def reply(design: str, testbench: str | None = TB) -> str:
    """A model reply in the requested format: design block, then testbench block."""
    text = f"Here is the design:\n```verilog\n{design}\n```\n"
    if testbench:
        text += f"And the testbench:\n```verilog\n{testbench}\n```\n"
    return text
