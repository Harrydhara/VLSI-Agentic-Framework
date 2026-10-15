// Faulty: counts on every clock edge, whether or not en is high.
module counter4(
    input            clk,
    input            rst,
    input            en,
    output reg [3:0] count
);
  always @(posedge clk) begin
    if (rst)
      count <= 4'd0;
    else
      count <= count + 4'd1;
  end
endmodule
