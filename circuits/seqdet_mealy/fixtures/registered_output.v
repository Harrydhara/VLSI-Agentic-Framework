// Faulty: detected is registered, which turns it into a Moore-style output: it no
// longer follows din between edges and comes one cycle late.
module seqdet_mealy(
    input      clk,
    input      rst_n,
    input      din,
    output reg detected
);
  localparam S0 = 2'd0, S1 = 2'd1, S11 = 2'd2, S110 = 2'd3;
  reg [1:0] state, next;

  always @(*) begin
    case (state)
      S0:      next = din ? S1  : S0;
      S1:      next = din ? S11 : S0;
      S11:     next = din ? S11 : S110;
      S110:    next = din ? S1  : S0;
      default: next = S0;
    endcase
  end

  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      state <= S0;
      detected <= 1'b0;
    end else begin
      state <= next;
      detected <= (state == S110) && din;
    end
endmodule
