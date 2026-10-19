// Reference test for seqdet_mealy: directed sequences (overlap, near misses, resets in
// the middle of a pattern, an asynchronous reset between edges) and 300 random bits.
// detected is compared with a golden model just after din changes between edges (a
// Mealy output must follow din at once) and just after every rising edge.
// Hidden from the agent. __RESULT_TOKEN__ is replaced with a fresh random token each run.
`timescale 1ns/1ps
module ref_tb;
  reg        clk = 0, rst_n = 1, din = 0;
  wire       detected;
  reg  [2:0] hist;
  reg  [1:0] seen;
  integer i, seed = 9, passed = 0, total = 0, shown = 0;

  seqdet_mealy dut(.clk(clk), .rst_n(rst_n), .din(din), .detected(detected));

  always #5 clk = ~clk;

  // Golden model: remember the bits sampled since the (asynchronous) reset;
  // detected = last three were 1, 1, 0 and din is 1 right now.
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      hist <= 3'b0;
      seen <= 2'd0;
    end else begin
      hist <= {hist[1:0], din};
      if (seen != 2'd3) seen <= seen + 2'd1;
    end
  wire model = (seen == 2'd3) && (hist == 3'b110) && din;

  task check(input between_edges);
    begin
      total = total + 1;
      if (detected === model) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        if (between_edges && !rst_n)
          $display("MISMATCH at %0d ns: rst_n is 0, so every earlier bit must be forgotten; expected detected=%b, got %b",
                   $time, model, detected);
        else if (between_edges)
          $display("MISMATCH at %0d ns: din changed to %b between clock edges (last bits sampled %b); expected detected=%b at once, got %b (a Mealy output follows din)",
                   $time, din, hist, model, detected);
        else
          $display("MISMATCH at %0d ns: after the rising edge (last bits sampled %b, din=%b), expected detected=%b, got %b",
                   $time, hist, din, model, detected);
      end
    end
  endtask

  task check_async_reset;
    begin
      total = total + 1;
      if (detected === model) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        $display("MISMATCH at %0d ns: rst_n fell between clock edges and detected is still %b; expected %b at once (the reset is asynchronous)",
                 $time, detected, model);
      end
    end
  endtask

  // Change the inputs after a falling edge, check, then check again after the rising edge.
  task cycle(input r_n, input d);
    begin
      @(negedge clk);
      rst_n = r_n;
      din = d;
      #1 check(1);
      @(posedge clk);
      #1 check(0);
    end
  endtask

  task send(input [15:0] bits, input integer n);  // n bits, most significant first
    integer k;
    begin
      for (k = n - 1; k >= 0; k = k - 1) cycle(1'b1, bits[k]);
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

    #2 rst_n = 0;                                   // reset before any clock edge
    #1 check_async_reset;
    cycle(0, 1); cycle(0, 1);                       // held in reset: nothing is remembered
    send(16'b1101, 4);                              // the pattern
    send(16'b101, 3);                               // 1101101: found again (overlap)
    send(16'b00, 2);
    send(16'b11101, 5);                             // a run of 1s before the 0
    send(16'b1100, 4);
    send(16'b1011101, 7);
    send(16'b110, 3);                               // reset in the middle of a pattern...
    cycle(0, 1);
    send(16'b1, 1);                                 // ...so this 1 must not complete it
    send(16'b11, 2);
    cycle(0, 0);
    send(16'b1, 1);

    // rst_n falls between clock edges while detected is 1
    send(16'b110, 3);
    @(negedge clk);
    din = 1;                                        // completes the pattern
    #1 check(1);
    #2 rst_n = 0;
    #1 check_async_reset;
    @(posedge clk);
    #1 check(0);
    cycle(1, 0);

    for (i = 0; i < 300; i = i + 1)                 // random, occasional resets
      cycle(($random(seed) & 63) != 0, $random(seed) & 1);

    $display("RESULT __RESULT_TOKEN__: %0d/%0d passed", passed, total);
    $finish;
  end
endmodule
