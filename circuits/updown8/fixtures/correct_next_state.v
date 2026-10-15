// Known-correct: separate next-state logic and state register.
module updown8(
    input        clk,
    input        rst_n,
    input        en,
    input        up,
    output [7:0] count
);
  reg  [7:0] state;
  wire [7:0] step = up ? 8'd1 : 8'hFF;  // +1, or -1 in two's complement
  wire [7:0] next = en ? state + step : state;

  always @(posedge clk or negedge rst_n)
    if (!rst_n)
      state <= 8'd0;
    else
      state <= next;

  assign count = state;
endmodule
