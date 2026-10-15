// Faulty: the select is inverted, so sel = 0 picks b and sel = 1 picks a.
module mux2(
    input  [7:0] a,
    input  [7:0] b,
    input        sel,
    output [7:0] y
);
  assign y = sel ? a : b;
endmodule
