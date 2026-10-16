# spec_first v1

The first version of [`spec_first`](../spec_first/README.md), kept so both versions can be
evaluated side by side. It is identical to v2 except `tb_review.txt`: v1 asks the
testbench's author to re-check failing checks and either correct the testbench or reply
`TESTBENCH OK`, without requiring a justification from the specification.

The files are those of commit `153e838`. One small difference from running v1 at that
commit: the agent now also counts a review that sends the same testbench back unchanged
as "confirmed" (before, it was a "revision" that changed nothing). Either way the failing
checks then go to the design conversation, so the run proceeds the same.
