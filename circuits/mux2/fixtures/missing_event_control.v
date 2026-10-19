// Faulty: the always block has no sensitivity list (should be always @(*)).
// iverilog rejects it at compile time because it would loop forever at time 0.
module mux2(
    input      [7:0] a,
    input      [7:0] b,
    input            sel,
    output reg [7:0] y
);
  always
    y = sel ? b : a;
endmodule
