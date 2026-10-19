// Known-correct: ADD and SUB share one adder (SUB adds the complement of b plus 1).
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
  wire       sub  = (op == 3'b001);
  wire [3:0] b_in = sub ? ~b : b;
  wire [4:0] sum  = {1'b0, a} + {1'b0, b_in} + {4'b0, sub};
  wire       sum_overflow = (a[3] == b_in[3]) && (sum[3] != a[3]);

  always @(*) begin
    carry = 1'b0;
    overflow = 1'b0;
    case (op)
      3'b000, 3'b001: begin y = sum[3:0]; carry = sum[4]; overflow = sum_overflow; end
      3'b010: y = a & b;
      3'b011: y = a | b;
      3'b100: y = a ^ b;
      3'b101: begin y = a << 1; carry = a[3]; end
      3'b110: begin y = a >> 1; carry = a[0]; end
      default: y = {3'b000, $signed(a) < $signed(b)};
    endcase
  end

  assign zero = ~|y;
  assign negative = y[3];
endmodule
