// Reference test for seqdet_moore: directed sequences (overlap, near misses, a reset in
// the middle of a pattern) and 300 random bits. detected is compared with a golden
// model just after every rising edge, and again just after din changes between edges
// (a Moore output must not react to din directly).
// Hidden from the agent. __RESULT_TOKEN__ is replaced with a fresh random token each run.
`timescale 1ns/1ps
module ref_tb;
  reg        clk = 0, rst = 1, din = 0;
  wire       detected;
  reg  [3:0] hist;
  reg  [2:0] seen;
  reg        model;
  integer i, seed = 5, passed = 0, total = 0, shown = 0;

  seqdet_moore dut(.clk(clk), .rst(rst), .din(din), .detected(detected));

  always #5 clk = ~clk;

  // Golden model: detected is registered; it is 1 for the cycle after the last four
  // bits sampled since the reset were 1, 0, 1, 1 (oldest first).
  always @(posedge clk)
    if (rst) begin
      hist <= 4'b0;
      seen <= 3'd0;
      model <= 1'b0;
    end else begin
      hist <= {hist[2:0], din};
      if (seen != 3'd4) seen <= seen + 3'd1;
      model <= (seen >= 3'd3) && ({hist[2:0], din} == 4'b1011);
    end

  task check(input between_edges);
    begin
      total = total + 1;
      if (detected === model) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        if (between_edges)
          $display("MISMATCH at %0d ns: din changed to %b between clock edges and detected is %b; expected %b (a Moore output changes only after a rising edge)",
                   $time, din, detected, model);
        else
          $display("MISMATCH at %0d ns: after the rising edge (rst=%b, last bits sampled %b), expected detected=%b, got %b",
                   $time, rst, hist, model, detected);
      end
    end
  endtask

  // Change the inputs after a falling edge, check, then check again after the rising edge.
  task cycle(input r, input d);
    begin
      @(negedge clk);
      rst = r;
      din = d;
      #1 check(1);
      @(posedge clk);
      #1 check(0);
    end
  endtask

  task send(input [15:0] bits, input integer n);  // n bits, most significant first
    integer k;
    begin
      for (k = n - 1; k >= 0; k = k - 1) cycle(1'b0, bits[k]);
    end
  endtask

  initial begin
    if ($test$plusargs("vcd")) begin
      $dumpfile("reference.vcd");
      $dumpvars(0, ref_tb);
    end

    cycle(1, 0); cycle(1, 0);                       // reset
    send(16'b1011, 4);                              // the pattern
    send(16'b011, 3);                               // 1011011: found again (overlap)
    send(16'b00, 2);
    send(16'b101011, 6);                            // 1010 then 11: found at the end
    send(16'b111011, 6);                            // a run of 1s before the pattern
    send(16'b1001011, 7);
    // A reset in the middle of a pattern: the bits after it must not complete the
    // bits before it. Two cases, for designs that do and do not keep shifting din in
    // during reset.
    send(16'b101, 3);
    cycle(1, 1);
    send(16'b1, 1);
    send(16'b10, 2);
    cycle(1, 1);
    send(16'b1, 1);
    send(16'b1011, 4);
    for (i = 0; i < 300; i = i + 1)                 // random, occasional resets
      cycle(($random(seed) & 63) == 0, $random(seed) & 1);

    $display("RESULT __RESULT_TOKEN__: %0d/%0d passed", passed, total);
    $finish;
  end
endmodule
