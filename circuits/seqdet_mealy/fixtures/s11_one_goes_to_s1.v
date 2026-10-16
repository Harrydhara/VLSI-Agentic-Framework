// Faulty: from S11 a third 1 goes back to S1, but "111" still ends in "11", so it
// should stay in S11. The pattern in 11101 is missed.
module seqdet_mealy(
    input  clk,
    input  rst_n,
    input  din,
    output detected
);
  localparam S0 = 2'd0, S1 = 2'd1, S11 = 2'd2, S110 = 2'd3;
  reg [1:0] state, next;

  always @(*) begin
    case (state)
      S0:      next = din ? S1  : S0;
      S1:      next = din ? S11 : S0;
      S11:     next = din ? S1  : S110;
      S110:    next = din ? S1  : S0;
      default: next = S0;
    endcase
  end

  always @(posedge clk or negedge rst_n)
    if (!rst_n)
      state <= S0;
    else
      state <= next;

  assign detected = (state == S110) && din;
endmodule
