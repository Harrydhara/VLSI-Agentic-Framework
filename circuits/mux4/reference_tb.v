// Reference test for mux4: for every select value, every 4-bit pattern on the selected
// input with the complement on the other three (sel held while the data changes),
// then 1000 random cases (fixed seed, so every run applies the same inputs).
// Hidden from the agent. __RESULT_TOKEN__ is replaced with a fresh random token each run.
`timescale 1ns/1ps
module ref_tb;
  reg  [3:0] d0, d1, d2, d3;
  reg  [1:0] sel;
  wire [3:0] y;
  reg  [3:0] expected;
  integer i, s, p, seed = 3, passed = 0, total = 0, shown = 0;

  mux4 dut(.d0(d0), .d1(d1), .d2(d2), .d3(d3), .sel(sel), .y(y));

  task check;
    begin
      #1;
      case (sel)
        2'd0: expected = d0;
        2'd1: expected = d1;
        2'd2: expected = d2;
        default: expected = d3;
      endcase
      total = total + 1;
      if (y === expected) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        $display("MISMATCH at %0d ns, d0=%h d1=%h d2=%h d3=%h sel=%0d: expected y=%h, got y=%h",
                 $time, d0, d1, d2, d3, sel, expected, y);
      end
    end
  endtask

  // Waveforms on request: +vcd dumps every signal; +vcd_from=<ns> and +vcd_until=<ns>
  // limit the dump to that window (the dashboard uses this).
  integer vcd_from = 0, vcd_until = 0;
  initial if ($test$plusargs("vcd")) begin
    $dumpfile("reference.vcd");
    $dumpvars(0, ref_tb);
    if ($value$plusargs("vcd_from=%d", vcd_from) && vcd_from > 0) begin
      $dumpoff;
      #(vcd_from) $dumpon;
    end
    if ($value$plusargs("vcd_until=%d", vcd_until) && vcd_until > vcd_from)
      #(vcd_until - vcd_from) $dumpoff;
  end

  initial begin
    for (s = 0; s < 4; s = s + 1)
      for (p = 0; p < 16; p = p + 1) begin
        sel = s[1:0];
        {d0, d1, d2, d3} = {4{~p[3:0]}};
        case (s)
          0: d0 = p[3:0];
          1: d1 = p[3:0];
          2: d2 = p[3:0];
          3: d3 = p[3:0];
        endcase
        check;
      end
    for (i = 0; i < 1000; i = i + 1) begin
      {d0, d1, d2, d3} = $random(seed);
      sel = $random(seed);
      check;
    end
    $display("RESULT __RESULT_TOKEN__: %0d/%0d passed", passed, total);
    $finish;
  end
endmodule
