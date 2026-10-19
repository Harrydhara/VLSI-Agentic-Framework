// Known-correct: keep the last three bits and how many have arrived since reset; the
// output compares them, together with the current din, combinationally.
module seqdet_mealy(
    input  clk,
    input  rst_n,
    input  din,
    output detected
);
  reg [2:0] last;  // most recent sampled bit in last[0]
  reg [1:0] seen;  // bits sampled since reset, stops counting at 3

  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      last <= 3'b0;
      seen <= 2'd0;
    end else begin
      last <= {last[1:0], din};
      if (seen != 2'd3) seen <= seen + 2'd1;
    end

  assign detected = (seen == 2'd3) && (last == 3'b110) && din;
endmodule
