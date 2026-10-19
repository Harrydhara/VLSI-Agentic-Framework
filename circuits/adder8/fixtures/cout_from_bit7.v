// Faulty: the sum is computed into an 8-bit wire, which drops the carry, and then
// bit 7 of the sum is used as the carry-out (an off-by-one width mistake).
module adder8(
    input  [7:0] a,
    input  [7:0] b,
    input        cin,
    output [7:0] sum,
    output       cout
);
  wire [7:0] total = a + b + cin;
  assign sum = total;
  assign cout = total[7];
endmodule
