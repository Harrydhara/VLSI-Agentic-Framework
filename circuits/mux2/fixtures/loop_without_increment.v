// Faulty: the while loop never increments i, so the simulation hangs as soon as
// an input changes. It compiles cleanly; only the simulation timeout catches it.
module mux2(
    input      [7:0] a,
    input      [7:0] b,
    input            sel,
    output reg [7:0] y
);
  integer i;
  always @(*) begin
    i = 0;
    while (i < 8)
      y[i] = sel ? b[i] : a[i];  // missing: i = i + 1;
  end
endmodule
