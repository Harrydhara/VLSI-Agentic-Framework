// Faulty: reset polarity inverted (treats rst = 0 as reset).
module counter4(
    input            clk,
    input            rst,
    input            en,
    output reg [3:0] count
);
  always @(posedge clk) begin
    if (!rst)
      count <= 4'd0;
    else if (en)
      count <= count + 4'd1;
  end
endmodule
