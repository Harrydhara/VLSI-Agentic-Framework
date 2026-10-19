// Reference test for updown8. A golden model of the spec runs alongside the design;
// count is compared with it just after every rising clock edge, and just after rst_n
// falls between edges (an asynchronous reset must clear count at once).
// Hidden from the agent. __RESULT_TOKEN__ is replaced with a fresh random token each run.
`timescale 1ns/1ps
module ref_tb;
  reg        clk = 0, rst_n = 1, en = 0, up = 1;
  wire [7:0] count;
  reg  [7:0] model;
  integer i, seed = 11, passed = 0, total = 0, shown = 0;

  updown8 dut(.clk(clk), .rst_n(rst_n), .en(en), .up(up), .count(count));

  always #5 clk = ~clk;

  // Golden model: asynchronous active-low reset; when enabled, count up or down with wrap-around.
  always @(posedge clk or negedge rst_n)
    if (!rst_n) model <= 8'd0;
    else if (en) model <= up ? model + 8'd1 : model - 8'd1;

  task check;
    begin
      total = total + 1;
      if (count === model) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        $display("MISMATCH at %0d ns: rst_n=%b en=%b up=%b expected count=%0d, got %0d",
                 $time, rst_n, en, up, model, count);
      end
    end
  endtask

  task check_async_reset;
    begin
      total = total + 1;
      if (count === model) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        $display("MISMATCH at %0d ns: rst_n fell between clock edges and count is %0d; expected %0d at once (reset must be asynchronous)",
                 $time, count, model);
      end
    end
  endtask

  // Change inputs after a falling edge, then check just after the next rising edge.
  task cycle(input r_n, input e, input u);
    begin
      @(negedge clk);
      rst_n = r_n;
      en = e;
      up = u;
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

    #2 rst_n = 0;                                          // reset before any clock edge
    #1 check_async_reset;
    cycle(0, 1, 1); cycle(0, 1, 1);                        // held in reset even with en = 1
    for (i = 0; i < 20; i = i + 1) cycle(1, 1, 1);         // up: 0 -> 20
    for (i = 0; i < 25; i = i + 1) cycle(1, 1, 0);         // down through 0 -> 255, to 251
    for (i = 0; i < 10; i = i + 1) cycle(1, 1, 1);         // up through 255 -> 0, to 5
    for (i = 0; i < 4; i = i + 1) cycle(1, 0, i[0]);       // hold while disabled
    for (i = 0; i < 12; i = i + 1) cycle(1, 1, i[0]);      // direction toggling

    // rst_n falls between clock edges: count must clear at once, not at the next edge
    @(posedge clk);
    #2 rst_n = 0;
    #1 check_async_reset;
    @(posedge clk);                                        // still in reset across an edge
    #1 check;
    #2 rst_n = 1;                                          // released between edges...
    #1 check;                                              // ...count stays 0 until the next edge
    for (i = 0; i < 3; i = i + 1) cycle(1, 1, 1);

    for (i = 0; i < 300; i = i + 1)                        // random, occasional resets
      cycle(($random(seed) & 31) != 0, $random(seed) & 1, $random(seed) & 1);

    $display("RESULT __RESULT_TOKEN__: %0d/%0d passed", passed, total);
    $finish;
  end
endmodule
