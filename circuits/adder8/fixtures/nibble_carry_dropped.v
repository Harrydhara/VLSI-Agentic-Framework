// Faulty: built from two 4-bit halves, but the carry out of the low half is never
// passed into the high half.
module adder8(
    input  [7:0] a,
    input  [7:0] b,
    input        cin,
    output [7:0] sum,
    output       cout
);
  wire carry_mid;
  assign {carry_mid, sum[3:0]} = a[3:0] + b[3:0] + cin;
  assign {cout, sum[7:4]} = a[7:4] + b[7:4];  // missing: + carry_mid
endmodule
