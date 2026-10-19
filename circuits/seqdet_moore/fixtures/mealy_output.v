// Faulty: a Mealy detector. detected = (state == S101) && din reacts to din between
// clock edges and comes one cycle early.
module seqdet_moore(
    input  clk,
    input  rst,
    input  din,
    output detected
);
  localparam S0 = 2'd0, S1 = 2'd1, S10 = 2'd2, S101 = 2'd3;
  reg [1:0] state, next;

  always @(*) begin
    case (state)
      S0:      next = din ? S1   : S0;
      S1:      next = din ? S1   : S10;
      S10:     next = din ? S101 : S0;
      S101:    next = din ? S1   : S10;
      default: next = S0;
    endcase
  end

  always @(posedge clk)
    if (rst)
      state <= S0;
    else
      state <= next;

  assign detected = (state == S101) && din;
endmodule
