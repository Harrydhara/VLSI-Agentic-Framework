// Known-correct: case statement in a combinational always block.
module mux4(
    input      [3:0] d0,
    input      [3:0] d1,
    input      [3:0] d2,
    input      [3:0] d3,
    input      [1:0] sel,
    output reg [3:0] y
);
  always @(*) begin
    case (sel)
      2'd0: y = d0;
      2'd1: y = d1;
      2'd2: y = d2;
      default: y = d3;
    endcase
  end
endmodule
