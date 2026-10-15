// Faulty: asynchronous reset. Identical to the spec while rst only changes near clock
// edges; caught when rst rises between edges and count clears immediately.
module counter4(
    input            clk,
    input            rst,
    input            en,
    output reg [3:0] count
);
  always @(posedge clk or posedge rst) begin
    if (rst)
      count <= 4'd0;
    else if (en)
      count <= count + 4'd1;
  end
endmodule
