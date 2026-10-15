// Known-correct: separate next-state logic and state register.
module counter4(
    input        clk,
    input        rst,
    input        en,
    output [3:0] count
);
  reg  [3:0] state;
  wire [3:0] next = rst ? 4'd0 : (en ? state + 4'd1 : state);

  always @(posedge clk)
    state <= next;

  assign count = state;
endmodule
