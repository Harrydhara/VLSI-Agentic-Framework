// Faulty: reset clears detected but not the bit history, so bits from before a reset
// can still complete a pattern (and there is no check that four bits have arrived).
module seqdet_moore(
    input      clk,
    input      rst,
    input      din,
    output reg detected
);
  reg [3:0] last;

  always @(posedge clk) begin
    last <= {last[2:0], din};
    if (rst)
      detected <= 1'b0;
    else
      detected <= ({last[2:0], din} == 4'b1011);
  end
endmodule
