// Known-correct: single clocked always block with asynchronous active-low reset.
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
    else if (en) begin
      if (up)
        count <= count + 8'd1;
      else
        count <= count - 8'd1;
    end
  end
endmodule
