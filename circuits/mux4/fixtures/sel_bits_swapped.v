// Faulty: sel[0] is treated as the high bit, so sel = 1 gives d2 and sel = 2 gives d1.
module mux4(
    input  [3:0] d0,
    input  [3:0] d1,
    input  [3:0] d2,
    input  [3:0] d3,
    input  [1:0] sel,
    output [3:0] y
);
  assign y = sel[0] ? (sel[1] ? d3 : d2) : (sel[1] ? d1 : d0);
endmodule
