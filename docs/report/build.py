"""Build docs/benchmark-report.html from the benchmark runs in runs/bench/.

Every table and run grid is generated from the runs' history.json and events.jsonl,
using the same summary code as `vlsi-agent bench`, so no result is typed by hand. The
template holds the text and {{PLACEHOLDERS}} for the generated parts.

    python docs/report/build.py                     # writes docs/benchmark-report.html
    python docs/report/build.py --artifact OUT.html # also a body-only copy for publishing
    python docs/report/build.py --facts             # print the numbers the text relies on
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from vlsi_agent.bench import row_from_history, summarize_group  # noqa: E402

BENCH = ROOT / "runs" / "bench"
FOLDERS = {"heldout": "heldout-seeds1-5", "dev": "dev-seeds1-5"}
SPLIT_LABEL = {"heldout": "Held-out", "dev": "Dev"}
CIRCUITS = {"heldout": ["adder8", "mux4", "updown8", "alu8", "seqdet_mealy"],
            "dev": ["adder4", "mux2", "counter4", "alu4", "seqdet_moore"]}
STRATEGIES = ["default", "tb_timing", "spec_first_v1", "spec_first"]
LABEL = {"default": "default", "tb_timing": "tb_timing", "spec_first_v1": "spec_first v1", "spec_first": "spec_first v2"}
ROUNDS = {1: ["default", "tb_timing"], 2: ["spec_first_v1", "spec_first"]}
SEEDS = range(1, 6)
MAX_ATTEMPTS = 3
WORDS = {"pass": "passed", "fail": "failed", "compile_error": "did not compile", "timeout": "timed out", "error": "error"}


def run_dir(split: str, circuit: str, strategy: str, seed: int) -> Path:
    return BENCH / FOLDERS[split] / "runs" / circuit / strategy / f"seed{seed}"


def history(split: str, circuit: str, strategy: str, seed: int) -> dict:
    return json.loads((run_dir(split, circuit, strategy, seed) / "history.json").read_text(encoding="utf-8"))


def rows(split: str, strategy: str, circuit: str | None = None) -> list[dict]:
    circuits = [circuit] if circuit else CIRCUITS[split]
    return [row_from_history(history(split, c, strategy, s), strategy, s, "", run_dir(split, c, strategy, s))
            for c in circuits for s in SEEDS]


def n(text: str) -> int:
    return int(text.split("/")[0])


# --------------------------------------------------------------------------- tables


def table_by_strategy() -> str:
    head = ("<tr><th>Split / strategy</th><th class=\"num\">One-shot</th><th class=\"num\">Iterative</th>"
            "<th class=\"num\">Fixed</th><th class=\"num\">Broken</th><th class=\"num\">Pass after 1 / 2 / 3 attempts</th>"
            "<th class=\"num\">Own checks passed</th><th class=\"num\">Mean attempts</th><th class=\"num\">Mean tokens</th>"
            "<th class=\"num\">Mean time</th></tr>")
    body = []
    for split in ("heldout", "dev"):
        body.append(f'<tr class="split"><th colspan="10">{SPLIT_LABEL[split]}</th></tr>')
        for strategy in STRATEGIES:
            g = summarize_group(rows(split, strategy), MAX_ATTEMPTS)
            change = n(g["final_pass"]) - n(g["oneshot_pass"])
            cls = " up" if change > 0 else " down" if change < 0 else ""
            after = " / ".join(str(n(v)) for v in g["pass_after_attempt"].values())
            body.append(
                f'<tr><th scope="row">{LABEL[strategy]}</th><td class="num">{g["oneshot_pass"]}</td>'
                f'<td class="num{cls}">{g["final_pass"]}</td><td class="num">{g["fixed"]}</td><td class="num">{g["broken"]}</td>'
                f'<td class="num">{after}</td><td class="num">{g["own_checks_passed"]}</td>'
                f'<td class="num">{g["mean_attempts"]}</td><td class="num">{round(g["mean_tokens"]):,}</td>'
                f'<td class="num">{g["mean_seconds"]} s</td></tr>')
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def table_by_circuit(split: str) -> str:
    head = ("<tr><th>Circuit</th>" + "".join(f'<th class="num">{LABEL[s]}</th>' for s in STRATEGIES)
            + '<th class="num">Median cells (passing)</th></tr>')
    body = []
    for circuit in CIRCUITS[split]:
        cells, areas = [], []
        for strategy in STRATEGIES:
            rs = rows(split, strategy, circuit)
            g = summarize_group(rs, MAX_ATTEMPTS)
            change = n(g["final_pass"]) - n(g["oneshot_pass"])
            final = (f'<span class="up">{g["final_pass"]}</span>' if change > 0 else
                     f'<span class="down">{g["final_pass"]}</span>' if change < 0 else g["final_pass"])
            cells.append(f'<td class="num">{g["oneshot_pass"]} → {final}<br><span class="tok">'
                         f'{round(g["mean_tokens"]):,} tokens</span></td>')
            areas += [r["final_cells"] for r in rs if r["final_outcome"] == "pass" and r["final_cells"]]
        median = statistics.median(areas) if areas else None
        area = "" if median is None else (f"{median:g}" + (f" ({min(areas)}–{max(areas)})" if min(areas) != max(areas) else ""))
        body.append(f'<tr><th scope="row">{circuit}</th>{"".join(cells)}<td class="num">{area}</td></tr>')
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def accuracy(split: str, strategy: str) -> tuple[int, int, int, int]:
    """(correct designs rejected, correct designs, wrong designs accepted, wrong designs) over all attempts."""
    rej = cor = acc = wrong = 0
    for c in CIRCUITS[split]:
        for s in SEEDS:
            for a in history(split, c, strategy, s)["attempts"]:
                if not a["reference"]:
                    continue
                if a["reference"]["outcome"] == "pass":
                    cor += 1
                    rej += not a["dev_passed"]
                else:
                    wrong += 1
                    acc += a["dev_passed"]
    return rej, cor, acc, wrong


def table_accuracy() -> str:
    head = ("<tr><th>Split / strategy</th><th>Correct designs rejected by own checks</th>"
            '<th class="num">Wrong designs accepted</th></tr>')
    body = []
    for split in ("heldout", "dev"):
        body.append(f'<tr class="split"><th colspan="3">{SPLIT_LABEL[split]}</th></tr>')
        for strategy in STRATEGIES:
            rej, cor, acc, wrong = accuracy(split, strategy)
            pct = round(100 * rej / cor)
            accepted = f"{acc} of {wrong}" if wrong else "none to accept"
            cls = " down" if acc else ""
            body.append(f'<tr><th scope="row">{LABEL[strategy]}</th><td><span class="bar"><i style="width:{pct}px"></i>'
                        f'{rej} of {cor} ({pct}%)</span></td><td class="num{cls}">{accepted}</td></tr>')
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def reviews(split: str, strategy: str) -> dict[str, int]:
    """Testbench reviews, judged by the hidden test of the design under review."""
    tally = {"kept_correct": 0, "kept_wrong": 0, "revised_correct": 0, "revised_wrong": 0}
    for c in CIRCUITS[split]:
        for s in SEEDS:
            d = run_dir(split, c, strategy, s)
            ref = {a["number"]: a["reference"]["outcome"] for a in history(split, c, strategy, s)["attempts"]
                   if a["reference"]}
            for line in (d / "events.jsonl").read_text(encoding="utf-8").splitlines():
                e = json.loads(line)
                if e["event"] not in ("testbench_confirmed", "testbench_revised", "testbench_revision_rejected"):
                    continue
                action = "revised" if e["event"] == "testbench_revised" else "kept"
                design = "correct" if ref.get(e["attempt"]) == "pass" else "wrong"
                tally[f"{action}_{design}"] += 1
    return tally


def table_reviews() -> str:
    head = ('<tr><th>Split / version</th><th class="num">Reviews</th>'
            '<th class="num">Revised; design was correct<br><span class="tok">right call</span></th>'
            '<th class="num">Kept; design was wrong<br><span class="tok">right call</span></th>'
            '<th class="num">Kept; design was correct<br><span class="tok">wrong call</span></th>'
            '<th class="num">Revised; design was wrong<br><span class="tok">gave in to the bug</span></th></tr>')
    body = []
    for split in ("heldout", "dev"):
        body.append(f'<tr class="split"><th colspan="6">{SPLIT_LABEL[split]}</th></tr>')
        for strategy in ROUNDS[2]:
            t = reviews(split, strategy)
            body.append(f'<tr><th scope="row">{LABEL[strategy]}</th><td class="num">{sum(t.values())}</td>'
                        f'<td class="num">{t["revised_correct"]}</td><td class="num">{t["kept_wrong"]}</td>'
                        f'<td class="num down">{t["kept_correct"]}</td><td class="num down">{t["revised_wrong"]}</td></tr>')
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


# --------------------------------------------------------------------------- run grids


def strip(h: dict) -> tuple[str, str]:
    parts, summary = [], []
    attempts = h["attempts"]
    for k in range(MAX_ATTEMPTS):
        if k >= len(attempts):
            parts.append('<span class="cell idle" title="not needed"></span>')
            continue
        a = attempts[k]
        ref = a["reference"]
        if ref is None:
            cls, text = "nocode", "no Verilog in reply"
        else:
            cls = ref["outcome"].replace("_", "-")
            text = "hidden test " + WORDS.get(ref["outcome"], ref["outcome"])
            if ref["total"]:
                text += f" {ref['passed']}/{ref['total']}"
        own = a["dev_passed"]
        title = f"attempt {k + 1}: {text}; own checks {'passed' if own else 'failed'}"
        parts.append(f'<span class="cell {cls}{" own" if own else ""}" title="{html.escape(title)}"></span>')
        summary.append(title)
    return "".join(parts), "; ".join(summary)


def grid(split: str, strategies: list[str]) -> str:
    head = ('<tr><th scope="col">circuit</th>'
            + '<th class="gap"></th>'.join(f'<th scope="col" colspan="5">{LABEL[s]}</th>' for s in strategies) + "</tr>")
    seeds = "".join(f'<th scope="col" class="seed">seed {k}</th>' for k in SEEDS)
    sub = "<tr><th></th>" + '<th class="gap"></th>'.join(seeds for _ in strategies) + "</tr>"
    body = []
    for circuit in CIRCUITS[split]:
        groups = []
        for strategy in strategies:
            tds = []
            for seed in SEEDS:
                cells, summary = strip(history(split, circuit, strategy, seed))
                label = html.escape(f"{circuit}, {LABEL[strategy]}, seed {seed}: {summary}")
                tds.append(f'<td><span class="strip" role="img" aria-label="{label}">{cells}</span></td>')
            groups.append("".join(tds))
        body.append(f'<tr><th scope="row">{circuit}</th>' + '<td class="gap"></td>'.join(groups) + "</tr>")
    return f'<table class="grid"><thead>{head}{sub}</thead><tbody>\n' + "\n".join(body) + "\n</tbody></table>"


# --------------------------------------------------------------------------- facts for the text


def facts() -> None:
    fixed = broken = runs = 0
    for split in ("heldout", "dev"):
        for strategy in STRATEGIES:
            g = summarize_group(rows(split, strategy), MAX_ATTEMPTS)
            fixed, broken, runs = fixed + g["fixed"], broken + g["broken"], runs + g["runs"]
            rej, cor, acc, wrong = accuracy(split, strategy)
            print(f"{split:8} {LABEL[strategy]:14} one-shot {g['oneshot_pass']:6} iterative {g['final_pass']:6} "
                  f"fixed {g['fixed']} broken {g['broken']} tokens {round(g['mean_tokens']):>6,} "
                  f"rejected {rej}/{cor} ({round(100 * rej / cor)}%) accepted {acc}/{wrong}")
    print(f"total runs {runs}, fixed {fixed}, broken {broken}")
    for strategy in STRATEGIES:
        both = [summarize_group(rows(sp, strategy), MAX_ATTEMPTS) for sp in ("dev", "heldout")]
        print(f"combined {LABEL[strategy]:14} one-shot {sum(n(g['oneshot_pass']) for g in both)}/50 "
              f"iterative {sum(n(g['final_pass']) for g in both)}/50")
    for strategy in ROUNDS[2]:
        for split in ("dev", "heldout"):
            print(f"reviews {split:8} {LABEL[strategy]:14} {reviews(split, strategy)}")
    ce = sum(1 for s in ROUNDS[2] for k in SEEDS
             if (history("heldout", "seqdet_mealy", s, k)["attempts"][0]["reference"] or {}).get("outcome") == "compile_error")
    print(f"seqdet_mealy spec_first one-shot compile errors: {ce} of 10")


# --------------------------------------------------------------------------- build


def build(template: str) -> str:
    parts = {
        "TABLE_BY_STRATEGY": table_by_strategy(),
        "TABLE_BY_CIRCUIT_HELDOUT": table_by_circuit("heldout"),
        "TABLE_BY_CIRCUIT_DEV": table_by_circuit("dev"),
        "TABLE_ACCURACY": table_accuracy(),
        "TABLE_REVIEWS": table_reviews(),
        "GRID_R1_HELDOUT": grid("heldout", ROUNDS[1]),
        "GRID_R1_DEV": grid("dev", ROUNDS[1]),
        "GRID_R2_HELDOUT": grid("heldout", ROUNDS[2]),
        "GRID_R2_DEV": grid("dev", ROUNDS[2]),
    }
    for key, value in parts.items():
        template = template.replace("{{" + key + "}}", value)
    if "{{" in template:
        raise SystemExit("unfilled placeholder: " + template[template.index("{{"):][:40])
    return template


def standalone(page: str) -> str:
    """The published page gets its document skeleton from the artifact host; the repo copy needs its own."""
    split = page.index('<div class="page">')
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f"{page[:split].strip()}\n</head>\n<body>\n{page[split:].strip()}\n</body>\n</html>\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, help="also write a body-only copy here (for publishing)")
    parser.add_argument("--facts", action="store_true", help="print the numbers the text relies on and stop")
    args = parser.parse_args()
    if args.facts:
        facts()
        return
    page = build((Path(__file__).parent / "template.html").read_text(encoding="utf-8"))
    out = ROOT / "docs" / "benchmark-report.html"
    out.write_text(standalone(page), encoding="utf-8", newline="\n")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    if args.artifact:
        args.artifact.write_text(page, encoding="utf-8", newline="\n")
        print(f"wrote {args.artifact}")


if __name__ == "__main__":
    main()
