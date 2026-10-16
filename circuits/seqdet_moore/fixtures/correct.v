// Known-correct: five-state Moore FSM. Each state names the longest suffix of the bits
// seen so far that is also a prefix of 1011.
module seqdet_moore(
    input  clk,
    input  rst,
    input  din,
    output detected
);
  localparam S0 = 3'd0, S1 = 3'd1, S10 = 3'd2, S101 = 3'd3, S1011 = 3'd4;
  reg [2:0] state, next;

  always @(*) begin
    case (state)
      S0:      next = din ? S1    : S0;
      S1:      next = din ? S1    : S10;
      S10:     next = din ? S101  : S0;
      S101:    next = din ? S1011 : S10;
      S1011:   next = din ? S1    : S10;
      default: next = S0;
    endcase
  end

  always @(posedge clk)
    if (rst)
      state <= S0;
    else
      state <= next;

  assign detected = (state == S1011);
endmodule
