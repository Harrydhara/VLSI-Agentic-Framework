// Faulty: synchronous reset. count only clears at the next rising clock edge after
// rst_n falls, instead of immediately.
module updown8(
    input            clk,
    input            rst_n,
    input            en,
    input            up,
    output reg [7:0] count
);
  always @(posedge clk) begin
    if (!rst_n)
      count <= 8'd0;
    else if (en)
      count <= up ? count + 8'd1 : count - 8'd1;
  end
endmodule
