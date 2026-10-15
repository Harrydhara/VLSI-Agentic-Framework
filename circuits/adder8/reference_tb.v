// Reference test for adder8: every one of the 131072 input combinations.
// Hidden from the agent. __RESULT_TOKEN__ is replaced with a fresh random token each run.
`timescale 1ns/1ps
module ref_tb;
  reg  [7:0] a, b;
  reg        cin;
  wire [7:0] sum;
  wire       cout;
  reg  [8:0] expected;
  integer i, passed = 0, total = 0, shown = 0;

  adder8 dut(.a(a), .b(b), .cin(cin), .sum(sum), .cout(cout));

  initial begin
    if ($test$plusargs("vcd")) begin
      $dumpfile("reference.vcd");
      $dumpvars(0, ref_tb);
    end
    for (i = 0; i < 131072; i = i + 1) begin
      {cin, a, b} = i[16:0];
      #1;
      expected = a + b + cin;  // 9-bit context, so the carry is kept
      total = total + 1;
      if ({cout, sum} === expected) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        $display("MISMATCH a=%0d b=%0d cin=%0d: expected sum=%0d cout=%0d, got sum=%0d cout=%0d",
                 a, b, cin, expected[7:0], expected[8], sum, cout);
      end
    end
    $display("RESULT __RESULT_TOKEN__: %0d/%0d passed", passed, total);
    $finish;
  end
endmodule
