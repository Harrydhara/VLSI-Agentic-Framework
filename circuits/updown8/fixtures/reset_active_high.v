// Faulty: reset polarity inverted (treats rst_n = 1 as reset), so the counter is held
// at 0 during normal operation.
module updown8(
    input            clk,
    input            rst_n,
    input            en,
    input            up,
    output reg [7:0] count
);
  always @(posedge clk or posedge rst_n) begin
    if (rst_n)
      count <= 8'd0;
    else if (en)
      count <= up ? count + 8'd1 : count - 8'd1;
  end
endmodule
