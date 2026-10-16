# Example strategy: spec-first testbench

The testbench is written from the specification, in its own conversation, before any
design exists. The design is written in a second conversation and checked against that
testbench.

## The problem it targets

In the first benchmark (`docs/benchmark-report.html`), the model wrote the design and its
testbench in the same reply. When it misunderstood the specification, both files carried
the same mistake: an ALU's signed comparison written as an unsigned one passed its own
testbench and failed the hidden test. Revisions never fixed that bug. Separately, the
model's testbenches rejected 36–81% of correct designs, so most revisions went into
designs that were already right.

## How it works (`workflow = "spec_first"` in `strategy.toml`)

1. **Testbench first.** The testbench conversation gets only the specification and the
   module header. Its testbench is run against a placeholder design whose outputs are all
   0. It must compile, finish and print a `RESULT:` line; if not, the model gets up to two
   fixes (`tb_fix.txt`).
2. **Design second.** The design conversation gets only the specification and the header,
   and never sees the testbench. Any testbench in its reply is ignored.
3. **Disagreements go to the testbench's author first.** When the design fails some checks,
   the failing check lines (inputs, expected, actual), but not the design, go back to the
   testbench conversation (`tb_review.txt`). It either corrects its testbench, after which
   the same design is checked again, or replies `TESTBENCH OK`. Only then do the failures
   go to the design conversation as feedback.

Neither conversation ever sees the other's code. The prompts use the same rules as
`prompts/default`, split by role, and add no Verilog tips, so a difference in results
comes from the workflow rather than from extra advice.

## Try it

```bash
vlsi-agent run alu4 --prompts prompts/examples/spec_first
vlsi-agent bench --prompts prompts/default prompts/examples/spec_first
```

The run folder gets a `testbench/` folder with each testbench version, its check against
the placeholder, and every review reply. `history.json` lists every model call with its
role (`testbench`, `testbench_fix`, `design`, `testbench_review`).

## Cost

At least two model calls per run instead of one, and a review call whenever a design fails
checks. Whether the extra testbench calls pay for themselves depends on how many wasted
design revisions they prevent. The benchmark measures both.
