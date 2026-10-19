// Reference test for counter4. A golden model of the spec runs alongside the design;
// count is compared with it just after every rising clock edge, plus once just after
// rst rises between edges (a synchronous reset must wait for the next rising edge).
// Hidden from the agent. __RESULT_TOKEN__ is replaced with a fresh random token each run.
`timescale 1ns/1ps
module ref_tb;
  reg        clk = 0, rst = 1, en = 0;
  wire [3:0] count;
  reg  [3:0] model;
  integer i, seed = 7, passed = 0, total = 0, shown = 0;

  counter4 dut(.clk(clk), .rst(rst), .en(en), .count(count));

  always #5 clk = ~clk;

  // Golden model: synchronous active-high reset with priority over enable, wraps 15 -> 0.
  always @(posedge clk)
    if (rst) model <= 4'd0;
    else if (en) model <= model + 4'd1;

  task check;
    begin
      total = total + 1;
      if (count === model) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        $display("MISMATCH at %0d ns: rst=%b en=%b expected count=%0d, got %0d", $time, rst, en, model, count);
      end
    end
  endtask

  // Change inputs after a falling edge, then check just after the next rising edge.
  task cycle(input r, input e);
    begin
      @(negedge clk);
      rst = r;
      en = e;
      @(posedge clk);
      #1 check;
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

    cycle(1, 0); cycle(1, 0);                              // reset
    for (i = 0; i < 20; i = i + 1) cycle(0, 1);            // count up through 15 -> 0
    for (i = 0; i < 3; i = i + 1) cycle(0, 0);             // hold while disabled
    for (i = 0; i < 16; i = i + 1) cycle(0, i[0]);         // enable toggling
    cycle(1, 1);                                           // reset wins over enable
    for (i = 0; i < 5; i = i + 1) cycle(0, 1);

    // rst rises between clock edges: count must not change until the next rising edge
    @(posedge clk);
    #2 rst = 1;
    #1 begin
      total = total + 1;
      if (count === model) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        $display("MISMATCH at %0d ns: count became %0d before a clock edge after rst rose; expected %0d (reset must be synchronous)",
                 $time, count, model);
      end
    end
    @(posedge clk);
    #1 check;                                              // now the reset takes effect

    for (i = 0; i < 200; i = i + 1)                        // random, occasional resets
      cycle(($random(seed) & 15) == 0, $random(seed) & 1);

    $display("RESULT __RESULT_TOKEN__: %0d/%0d passed", passed, total);
    $finish;
  end
endmodule
