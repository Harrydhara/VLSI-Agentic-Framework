// Known-correct: ADD and SUB share one adder (SUB adds the complement of b plus 1).
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
  wire       sub  = (op == 3'b100);
  wire [7:0] b_in = sub ? ~b : b;
  wire [8:0] sum  = {1'b0, a} + {1'b0, b_in} + {8'b0, sub};
  wire       sum_overflow = (a[7] == b_in[7]) && (sum[7] != a[7]);

  always @(*) begin
    carry = 1'b0;
    overflow = 1'b0;
    case (op)
      3'b000: y = a & b;
      3'b001: y = a | b;
      3'b010: y = a ^ b;
      3'b011, 3'b100: begin y = sum[7:0]; carry = sum[8]; overflow = sum_overflow; end
      3'b101: begin y = $signed(a) >>> 1; carry = a[0]; end
      3'b110: begin y = {a[6:0], a[7]}; carry = a[7]; end
      default: y = {7'b0, $signed(a) < $signed(b)};
    endcase
  end

  assign zero = ~|y;
  assign negative = y[7];
endmodule
