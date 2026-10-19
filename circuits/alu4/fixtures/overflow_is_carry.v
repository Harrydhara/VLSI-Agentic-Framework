// Faulty: overflow is set to the carry, mixing up unsigned carry and signed overflow.
module alu4(
    input      [3:0] a,
    input      [3:0] b,
    input      [2:0] op,
    output reg [3:0] y,
    output           zero,
    output reg       carry,
    output reg       overflow,
    output           negative
);
  always @(*) begin
    carry = 1'b0;
    overflow = 1'b0;
    case (op)
      3'b000: begin
        {carry, y} = a + b;
        overflow = carry;
      end
      3'b001: begin
        {carry, y} = {1'b0, a} + {1'b0, ~b} + 5'd1;
        overflow = carry;
      end
      3'b010: y = a & b;
      3'b011: y = a | b;
      3'b100: y = a ^ b;
      3'b101: begin y = {a[2:0], 1'b0}; carry = a[3]; end
      3'b110: begin y = {1'b0, a[3:1]}; carry = a[0]; end
      default: y = ($signed(a) < $signed(b)) ? 4'd1 : 4'd0;
    endcase
  end

  assign zero = (y == 4'd0);
  assign negative = y[3];
endmodule
