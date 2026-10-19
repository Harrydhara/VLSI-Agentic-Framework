// Faulty interface: the carry-out port is named "carry" instead of "cout",
// so the reference test cannot connect to it. The logic itself is correct.
module adder4(
    input  [3:0] a,
    input  [3:0] b,
    input        cin,
    output [3:0] sum,
    output       carry
);
  assign {carry, sum} = a + b + cin;
endmodule
