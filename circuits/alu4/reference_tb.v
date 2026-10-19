// Reference test for alu4: every combination of op, a and b (2048 cases), all eight
// output bits compared against a golden model of the specification.
// Hidden from the agent. __RESULT_TOKEN__ is replaced with a fresh random token each run.
`timescale 1ns/1ps
module ref_tb;
  reg  [3:0] a, b;
  reg  [2:0] op;
  wire [3:0] y;
  wire       zero, carry, overflow, negative;
  reg  [3:0] ey;
  reg        ez, ec, ev, en;
  reg  [4:0] wide;
  integer i, passed = 0, total = 0, shown = 0;

  alu4 dut(.a(a), .b(b), .op(op), .y(y), .zero(zero), .carry(carry), .overflow(overflow), .negative(negative));

  // Golden model. Note {1'b0, ~b}: inside a wider expression, ~b alone would be widened first.
  task model;
    begin
      ec = 1'b0;
      ev = 1'b0;
      case (op)
        3'b000: begin
          wide = {1'b0, a} + {1'b0, b};
          ey = wide[3:0];
          ec = wide[4];
          ev = (a[3] == b[3]) && (ey[3] != a[3]);
        end
        3'b001: begin
          wide = {1'b0, a} + {1'b0, ~b} + 5'd1;
          ey = wide[3:0];
          ec = (a >= b);
          ev = (a[3] != b[3]) && (ey[3] != a[3]);
        end
        3'b010: ey = a & b;
        3'b011: ey = a | b;
        3'b100: ey = a ^ b;
        3'b101: begin ey = {a[2:0], 1'b0}; ec = a[3]; end
        3'b110: begin ey = {1'b0, a[3:1]}; ec = a[0]; end
        default: ey = ($signed(a) < $signed(b)) ? 4'd1 : 4'd0;
      endcase
      ez = (ey == 4'd0);
      en = ey[3];
    end
  endtask

  // Waveforms on request: +vcd dumps every signal; +vcd_from=<ns> and +vcd_until=<ns>
  // limit the dump to that window (the dashboard uses this).
  integer vcd_from = 0, vcd_until = 0;
  initial if ($test$plusargs("vcd")) begin
    $dumpfile("reference.vcd");
    $dumpvars(0, ref_tb);
    if ($value$plusargs("vcd_from=%d", vcd_from) && vcd_from > 0) begin
      $dumpoff;
      #(vcd_from) $dumpon;
    end
    if ($value$plusargs("vcd_until=%d", vcd_until) && vcd_until > vcd_from)
      #(vcd_until - vcd_from) $dumpoff;
  end

  initial begin
    for (i = 0; i < 2048; i = i + 1) begin
      {op, a, b} = i[10:0];
      #1;
      model;
      total = total + 1;
      if ({y, zero, carry, overflow, negative} === {ey, ez, ec, ev, en}) passed = passed + 1;
      else if (shown < 5) begin
        shown = shown + 1;
        $display("MISMATCH at %0d ns, op=%b a=%0d b=%0d: expected y=%0d zero=%b carry=%b overflow=%b negative=%b, got y=%0d zero=%b carry=%b overflow=%b negative=%b",
                 $time, op, a, b, ey, ez, ec, ev, en, y, zero, carry, overflow, negative);
      end
    end
    $display("RESULT __RESULT_TOKEN__: %0d/%0d passed", passed, total);
    $finish;
  end
endmodule
