"""Turn agent events (see agent.py) into one-line progress messages, for the CLI and the dashboard."""

from __future__ import annotations


def checks_text(e: dict) -> str:
    """A short summary of a checks_done event: 'compiled, testbench 256/512, 17 cells'."""
    if not e.get("code_found"):
        return "no Verilog in the reply"
    if not e.get("has_top"):
        return "top module missing"
    if not e.get("compiled"):
        return "design does not compile"
    parts = ["compiled"]
    if not e.get("has_testbench"):
        parts.append("no testbench")
    elif e.get("sim_timed_out"):
        parts.append("testbench timed out")
    elif "tb_total" in e:
        parts.append(f"testbench {e['tb_passed']}/{e['tb_total']}" if e["tb_total"] is not None
                     else "testbench printed no RESULT")
    if e.get("synth_ok"):
        parts.append(f"{e['cells']} cells" + (f", {e['latches']} latches" if e["latches"] else ""))
    elif "synth_ok" in e:
        parts.append("synthesis failed")
    return ", ".join(parts)


def _who(e: dict) -> str:
    """Which step an event belongs to: 'attempt 2', 'testbench', 'attempt 2: testbench review'."""
    role, n = e.get("role", "design"), e.get("attempt")
    if role == "design":
        return f"attempt {n}"
    label = role.replace("_", " ")
    return f"attempt {n}: {label}" if n else label


def describe_event(e: dict) -> str | None:
    """The progress line for an event, or None for events that are not shown."""
    kind, n = e["event"], e.get("attempt")
    lines = {
        "run_started": lambda: f"{e['circuit']} ({e['split']}) with {e['model']}: up to {e['max_attempts']} "
                               f"attempt(s), budget {e['max_total_tokens']} tokens"
                               + (", testbench written first from the spec" if e.get("workflow") == "spec_first"
                                  else ""),
        "model_called": lambda: f"{_who(e)}: asking the model...",
        "model_replied": lambda: f"{_who(e)}: reply of {e['output_tokens']} tokens in {e['duration_s']:.1f} s"
                                 + (" (cut off at the token limit)" if e["truncated"] else ""),
        "testbench_checked": lambda: f"testbench version {e['version']}: "
                                     + ("usable" if e["usable"] else "not usable, asking for a fix"),
        "testbench_unusable": lambda: f"no usable testbench after {e['tries']} tries; "
                                      "checking compile and synthesis only",
        "testbench_confirmed": lambda: f"attempt {n}: the testbench's author confirmed the failed checks",
        "testbench_revised": lambda: f"attempt {n}: the testbench's author corrected the testbench; checking again",
        "testbench_revision_rejected": lambda: f"attempt {n}: the corrected testbench was not usable; keeping the old one",
        "checks_done": lambda: f"attempt {n}: development checks {'PASSED' if e['passed'] else 'FAILED'} "
                               f"({checks_text(e)})" + (" after the testbench fix" if e.get("recheck") else ""),
        "feedback_sent": lambda: f"attempt {n}: sent the check results back to the model ({e['chars']} chars)",
        "budget_exhausted": lambda: f"token budget used up ({e['used']}/{e['budget']}), stopping",
        "model_error": lambda: f"{_who(e)}: model error: {e['error']}",
        "reference_scored": lambda: f"hidden reference test on attempt {n}: {e['outcome'].upper()}"
                                    + (f" {e['passed']}/{e['total']}" if e["total"] else "")
                                    + ("" if e["final"] else " (earlier attempt, for comparison)"),
        "run_finished": lambda: f"finished: {e['status']} after {e['attempts']} attempt(s), "
                                f"{e['total_tokens']} tokens, {e['duration_s']:.1f} s",
    }
    return lines[kind]() if kind in lines else None
