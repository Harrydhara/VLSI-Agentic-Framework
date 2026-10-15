// Faulty: only the low 4 bits are multiplexed; the high 4 bits always come from a.
module mux2(
    input  [7:0] a,
    input  [7:0] b,
    input        sel,
    output [7:0] y
);
  assign y = {a[7:4], sel ? b[3:0] : a[3:0]};
endmodule
