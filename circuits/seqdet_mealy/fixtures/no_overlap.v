// Faulty: after a detection it starts from scratch (S110 on 1 goes to S0), so the
// second pattern in 1101101 is missed.
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
      S11:     next = din ? S11 : S110;
      S110:    next = S0;
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
