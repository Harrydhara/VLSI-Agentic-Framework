// Faulty: counts down when up is 1 and up when up is 0.
module updown8(
    input            clk,
    input            rst_n,
    input            en,
    input            up,
    output reg [7:0] count
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n)
      count <= 8'd0;
    else if (en)
      count <= up ? count - 8'd1 : count + 8'd1;
  end
endmodule
