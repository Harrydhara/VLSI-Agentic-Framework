// Faulty: ROL rotates right instead of left.
module alu8(
    input      [7:0] a,
    input      [7:0] b,
    input      [2:0] op,
    output reg [7:0] y,
    output           zero,
    output reg       carry,
    output reg       overflow,
    output           negative
);
  always @(*) begin
    carry = 1'b0;
    overflow = 1'b0;
    case (op)
      3'b000: y = a & b;
      3'b001: y = a | b;
      3'b010: y = a ^ b;
      3'b011: begin
        {carry, y} = a + b;
        overflow = (a[7] == b[7]) && (y[7] != a[7]);
      end
      3'b100: begin
        {carry, y} = {1'b0, a} + {1'b0, ~b} + 9'd1;
        overflow = (a[7] != b[7]) && (y[7] != a[7]);
      end
      3'b101: begin y = {a[7], a[7:1]}; carry = a[0]; end
      3'b110: begin y = {a[0], a[7:1]}; carry = a[0]; end
      default: y = ($signed(a) < $signed(b)) ? 8'd1 : 8'd0;
    endcase
  end

  assign zero = (y == 8'd0);
  assign negative = y[7];
endmodule
