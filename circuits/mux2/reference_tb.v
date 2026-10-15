// Reference test for mux2: corner patterns and walking ones for both select values,
// then 500 random cases (fixed seed, so every run applies the same inputs).
// Hidden from the agent. __RESULT_TOKEN__ is replaced with a fresh random token each run.
`timescale 1ns/1ps
module ref_tb;
  reg  [7:0] a, b;
  reg        sel;
  wire [7:0] y;
  reg  [7:0] expected;
  integer i, j, s, seed = 1, passed = 0, total = 0, shown = 0;

  mux2 dut(.a(a), .b(b), .sel(sel), .y(y));

  task check;
    begin
      #1;
      expected = sel ? b : a;
      total = total + 1;
      if (y === expected) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        $display("MISMATCH a=%h b=%h sel=%b: expected y=%h, got y=%h", a, b, sel, expected, y);
      end
    end
  endtask

  initial begin
    if ($test$plusargs("vcd")) begin
      $dumpfile("reference.vcd");
      $dumpvars(0, ref_tb);
    end
    for (s = 0; s < 2; s = s + 1) begin
      sel = s[0];
      a = 8'h00; b = 8'hFF; check;
      a = 8'hFF; b = 8'h00; check;
      a = 8'hAA; b = 8'h55; check;
      a = 8'h55; b = 8'hAA; check;
      for (j = 0; j < 8; j = j + 1) begin
        a = 8'd1 << j;
        b = ~(8'd1 << j);
        check;
      end
    end
    for (i = 0; i < 500; i = i + 1) begin
      a = $random(seed);
      b = $random(seed);
      sel = $random(seed);
      check;
    end
    $display("RESULT __RESULT_TOKEN__: %0d/%0d passed", passed, total);
    $finish;
  end
endmodule
