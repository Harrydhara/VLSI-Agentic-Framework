// Faulty interface: a and b are declared 4 bits wide instead of 8. iverilog only
// warns about the width mismatch and compiles, so this shows up as failed checks.
module adder8(
    input  [3:0] a,
    input  [3:0] b,
    input        cin,
    output [7:0] sum,
    output       cout
);
  assign {cout, sum} = a + b + cin;
endmodule
