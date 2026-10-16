# Example strategy: testbench timing

A worked example of improving the starter agent by editing prompts only.

## The problem it targets

In the first real runs, gemma4's **designs** were usually right, but the **testbenches**
it wrote often checked outputs at the wrong moment: at the same instant the inputs
changed, or exactly at a clock edge instead of just after it. A wrong testbench rejects a
correct design, so the agent spends its attempts "fixing" things that were never broken,
and its development checks never pass.

## What changed compared with `prompts/default`

| File | Change |
|---|---|
| `system.txt` | New "Testbench timing" section: wait `#1` after changing inputs; for clocked designs change inputs after the falling edge, keep a reference model in the testbench, compare `#1` after the rising edge, check an asynchronous reset between edges; plus a short `check`/`cycle` task skeleton. |
| `feedback.txt` | Before changing the design, rule out the common testbench timing mistakes. |
| `task.txt` | Unchanged. |

Everything else (model, temperature, attempts, budget) stays the same, so any
difference in results comes from the prompts alone. The advice is deliberately
generic: it never describes a particular circuit, so it does not hand the model the
answer to any spec.

## Try it

```bash
vlsi-agent run counter4 --prompts prompts/examples/tb_timing
vlsi-agent run counter4                                   # the default prompts, for comparison
```

## Comparing fairly

- Tune on the `dev` circuits only. Keep `heldout` circuits for the final comparison, or
  the result will look better than the strategy really is.
- Compare the **hidden reference outcome** (the score), not just whether the agent's own
  checks passed.
- One run per setting is not enough: on a GPU, identical runs can give different answers.
  Repeat each setting several times with different seeds, and also compare attempts used and
  tokens spent.

## Results so far (dev circuits, 3 seeds each, up to 3 attempts)

| | default | tb_timing |
|---|---|---|
| Hidden reference test passed | 9/9 | 9/9 |
| Own testbench passed on the first try | 7/9 | 8/9 |
| Attempts used (total) | 11 | 10 |
| Tokens (total) | 18,044 | 20,352 (+13%: longer prompt) |

**Inconclusive.** gemma4 passes every dev circuit with either prompt set, so the score
cannot tell them apart, and one run in nine is within run-to-run noise. Most first-try
failures were not timing problems at all: a `$display` with a missing argument, and a call
to a system function that does not exist (`$bitstoh`). Next steps: more seeds, harder or
clocked circuits, and then the held-out comparison. An idea for a follow-up strategy: tell
the model to use only standard Verilog-2005 system tasks.

## Results on the harder dev set (5 circuits incl. alu4 and seqdet_moore, seeds 1-5)

| | default | tb_timing |
|---|---|---|
| One-shot / iterative reference pass | 23/25 -> 24/25 | 23/25 -> 22/25 |
| Revisions that fixed / broke a design | 1 / 0 | 0 / 1 |
| Testbench did not compile (all attempts) | 0 of 43 | 10 of 39 |
| Mean tokens per run | 7,320 | 5,906 |

**No improvement, and a new failure mode.** The extra guidance made the model write more
ambitious tests, for example signed comparisons, but with invalid Verilog: `4'd-1`
instead of `-4'd1`, and `always #5 clk = ~clk;` placed inside an `initial` block. On
alu4 its testbench then "passed" a design with an unsigned SLT twice: once from the start,
and once after a revision turned a correct design into a wrong one. The testbench shared
the design's misunderstanding. Lesson: better testbench *advice* is not enough; the
feedback has to come from tests that do not share the design's mistakes.

## Making your own strategy

1. Copy a prompts folder: `cp -r prompts/default prompts/mine`
2. Edit `system.txt`, `task.txt` and/or `feedback.txt`. Placeholders you can use:
   `task.txt`: `{{description}}`, `{{interface}}`, `{{top}}`;
   `feedback.txt`: `{{attempt}}`, `{{max_attempts}}`, `{{feedback}}`.
3. Run with `--prompts prompts/mine`. A misspelled placeholder is reported before any model call.
4. Write a README like this one: what problem you saw, what you changed, how it compared.
