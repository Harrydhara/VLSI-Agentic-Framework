// Known-correct: ripple-carry structure built from full adders.
module full_adder(input a, input b, input cin, output s, output cout);
  assign s = a ^ b ^ cin;
  assign cout = (a & b) | (cin & (a ^ b));
endmodule

module adder8(
    input  [7:0] a,
    input  [7:0] b,
    input        cin,
    output [7:0] sum,
    output       cout
);
  wire [8:0] carry;
  assign carry[0] = cin;

  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : stage
      full_adder fa(.a(a[i]), .b(b[i]), .cin(carry[i]), .s(sum[i]), .cout(carry[i + 1]));
    end
  endgenerate

  assign cout = carry[8];
endmodule
