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

## Version history

**v1** (commit 153e838) asked the testbench's author to re-check failing checks and either
fix the testbench or reply `TESTBENCH OK`. On the dev circuits (seeds 1–5) it reached 24/25
with one genuine fix and cut false rejections of correct designs to 21% (default: 51%).
But in all 12 reviews the author rewrote its testbench and never confirmed it, and once
(alu4, seed 2) it changed a correct expected value to match a buggy design.

**v2** (current) requires the author to quote the sentence of the specification behind
each failing expected value, and to change a value only when that sentence shows the
testbench was wrong, never just to match the design. A reply with a `TESTBENCH OK` line,
or the same testbench sent back unchanged, counts as confirmed.

**Dev results compared:** v1 went 23/25 one-shot to 24/25 iterative (1 fixed, 0 broken,
5,985 tokens per run). v2 went 23/25 to 23/25 (2 fixed, 2 broken, 6,744 tokens per run).
The two fail in opposite directions. v1's author revised its testbench in 12 of 12 reviews,
twice giving in to a buggy design. v2's author confirmed in 13 of 14, but 10 of those kept
a testbench that sampled clocked outputs at the wrong time, which justifying values from
the specification cannot reveal.

## Held-out evaluation (declared before running)

Both versions are evaluated once on the held-out circuits, as two variants declared in
advance: `prompts/examples/spec_first_v1` and `prompts/examples/spec_first` (v2), with seeds
1–5, the same settings as the earlier held-out runs, and the agent code frozen at the commit
that adds this section. Both results are reported. Neither version is tuned further
afterwards, and the held-out results are not used to pick or adjust either one.

**Held-out results** (run at commit `63c4a39`, seeds 1–5, 25 runs per strategy):

| | One-shot → iterative | Fixed / broken | Correct designs rejected | Wrong accepted | Tokens per run |
|---|---|---|---|---|---|
| default | 22 → 24 | 2 / 0 | 81% | 0 of 5 | 10,501 |
| tb_timing | 25 → 25 | 0 / 0 | 54% | none | 6,780 |
| spec_first v1 | 20 → 21 | 2 / 1 | 65% | 2 of 11 | 12,526 |
| spec_first v2 | 20 → 23 | 3 / 0 | 59% | 1 of 8 | 12,817 |

Neither version improved on the earlier strategies, and both cost more tokens. The dev
reduction in false rejections (21%) did not hold on the harder held-out circuits (59–65%).
The lower one-shot score has a separate cause: with the design-only prompt, the model wrote
seqdet_mealy's output inside `always @(*)` without declaring it `reg` in 7 of 10 runs,
where the default prompt led it to `assign`. Three runs ended on a final revision that did
not compile. Across both splits the strategies score 45–48 of 50, and their order flips
between dev and held-out, so the score differences are mostly noise at this sample size.

## Cost

At least two model calls per run instead of one, and a review call whenever a design fails
checks. Whether the extra testbench calls pay for themselves depends on how many wasted
design revisions they prevent. The benchmark measures both.
