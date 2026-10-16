// Known-correct: keep the last four bits and how many bits have arrived since reset,
// and register the comparison.
module seqdet_moore(
    input      clk,
    input      rst,
    input      din,
    output reg detected
);
  reg [3:0] last;  // most recent bit in last[0]
  reg [2:0] seen;  // bits sampled since reset, stops counting at 4

  always @(posedge clk) begin
    if (rst) begin
      last <= 4'b0;
      seen <= 3'd0;
      detected <= 1'b0;
    end else begin
      last <= {last[2:0], din};
      if (seen != 3'd4) seen <= seen + 3'd1;
      detected <= (seen >= 3'd3) && ({last[2:0], din} == 4'b1011);
    end
  end
endmodule
