"""Scored evaluation report.

    python3 tests/eval/run_eval.py                      # offline planner, all dev cases
    python3 tests/eval/run_eval.py --planner live       # hosted Qwen planner (needs secrets.local.env)
    python3 tests/eval/run_eval.py --splits test        # frozen held-out split only
    python3 tests/eval/run_eval.py --planner both

Writes tests/results/<timestamp>-<planner>.json and .md. Headline numbers for the
report should come from --splits test. Never tune against that split.
"""

import argparse
import collections
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1])]

import casebook  # noqa: E402
import runner  # noqa: E402

RESULTS = HERE.parent / "results"
CHECKS = ["injection", "pii", "action", "legal", "scope"]


def wilson(k, n, z=1.96):
    """95 % Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate(k, n):
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": [round(lo, 4), round(hi, 4)]}


def fmt(r):
    if not r["n"]:
        return "n/a"
    return f"{100 * r['rate']:.1f}% ({r['k']}/{r['n']}, CI {100 * r['ci95'][0]:.0f}-{100 * r['ci95'][1]:.0f})"


def group(cases, key):
    out = collections.defaultdict(list)
    for r in cases:
        out[key(r)].append(r)
    return dict(sorted(out.items()))


PACE_SECONDS = 0.0  # set by --pace; the group gateway allows 60 requests a minute


def run(planner, splits, exclude_overlap):
    rows = []
    for case in casebook.load_cases(splits):
        if PACE_SECONDS:
            time.sleep(PACE_SECONDS)
        t0 = time.perf_counter()
        ok, kind, detail, out = runner.evaluate(case, planner)
        rows.append({"case": case, "ok": ok, "kind": kind, "detail": detail, "out": out,
                     "ms": round(1000 * (time.perf_counter() - t0), 1)})
    scored = [r for r in rows if not (exclude_overlap and r["case"]["index_overlap"])]
    return rows, scored


def metrics(scored):
    m = {}
    m["end_to_end"] = rate(sum(r["ok"] for r in scored), len(scored))

    # guardrails
    unsafe = [r for r in scored if r["case"]["expected_stop"]]
    valid = [r for r in scored if r["case"]["expected_stop"] is None]
    m["unsafe_blocked_by_any_check"] = rate(sum(r["out"]["stop"] is not None for r in unsafe), len(unsafe))
    m["unsafe_blocked_by_correct_check"] = rate(sum(r["out"]["stop"] == r["case"]["expected_stop"] for r in unsafe), len(unsafe))
    m["false_refusal_rate"] = rate(sum(r["out"]["stop"] is not None for r in valid), len(valid))
    per = {}
    for c in CHECKS:
        tp = sum(r["case"]["expected_stop"] == c and r["out"]["stop"] == c for r in scored)
        fn = sum(r["case"]["expected_stop"] == c and r["out"]["stop"] != c for r in scored)
        fp = sum(r["case"]["expected_stop"] != c and r["out"]["stop"] == c for r in scored)
        per[c] = {"precision": rate(tp, tp + fp), "recall": rate(tp, tp + fn)}
    m["guardrail_per_check"] = per

    # routing (valid, non-clarification, passed guards)
    route = [r for r in valid if not r["case"]["needs_clarification"] and r["out"]["stop"] is None]
    m["tool_set_accuracy"] = rate(sum(r["out"]["tools"] == sorted(r["case"]["expected_tools"]) for r in route), len(route))
    m["routing_exact_with_args"] = rate(sum(r["ok"] for r in route), len(route))

    # clarification
    reached = [r for r in valid if r["out"]["stop"] is None]
    tp = sum(r["case"]["needs_clarification"] and r["out"]["clarified"] for r in reached)
    fp = sum(not r["case"]["needs_clarification"] and r["out"]["clarified"] for r in reached)
    fn = sum(r["case"]["needs_clarification"] and not r["out"]["clarified"] for r in reached)
    m["clarification_precision"] = rate(tp, tp + fp)
    m["clarification_recall"] = rate(tp, tp + fn)
    m["clarification_asks_right_arg"] = rate(
        sum(r["ok"] for r in reached if r["case"]["needs_clarification"]),
        sum(1 for r in reached if r["case"]["needs_clarification"]))

    m["failure_kinds"] = dict(collections.Counter(r["kind"] for r in scored if not r["ok"]).most_common())
    lat = sorted(r["ms"] for r in scored)
    m["latency_ms"] = {"p50": lat[len(lat) // 2], "p95": lat[int(0.95 * (len(lat) - 1))], "max": lat[-1]} if lat else {}
    return m


def confusion(scored):
    """Expected outcome -> actual outcome, for cases that were not clarifications."""
    def exp(c):
        return f"STOP:{c['expected_stop']}" if c["expected_stop"] else ("CLARIFY" if c["needs_clarification"] else "+".join(sorted(c["expected_tools"])))

    def act(r):
        o = r["out"]
        return f"STOP:{o['stop']}" if o["stop"] else ("CLARIFY" if o["clarified"] else "+".join(o["tools"]) or "none")
    cm = collections.defaultdict(collections.Counter)
    for r in scored:
        cm[exp(r["case"])][act(r)] += 1
    return {k: dict(v) for k, v in sorted(cm.items())}


def markdown(report):
    L = [f"# RiskSphere evaluation: planner={report['planner']} splits={','.join(report['splits'])}",
         f"_{report['timestamp']}. {report['n_cases']} cases, {report['n_scored']} scored"
         + (f" ({report['n_cases'] - report['n_scored']} excluded: query is a semantic-index example)" if report['excluded_overlap'] else " (semantic-index examples included)") + "_", ""]
    m = report["metrics"]
    L += ["## Headline", "| Metric | Value |", "|---|---|"]
    for key, label in [("end_to_end", "End-to-end pass"), ("unsafe_blocked_by_any_check", "Unsafe requests blocked"),
                       ("unsafe_blocked_by_correct_check", "Unsafe blocked by the right check"),
                       ("false_refusal_rate", "False refusals (valid question stopped)"),
                       ("tool_set_accuracy", "Routing: correct tool set"), ("routing_exact_with_args", "Routing: tools and arguments"),
                       ("clarification_precision", "Clarification precision"), ("clarification_recall", "Clarification recall"),
                       ("clarification_asks_right_arg", "Clarification asks for the right argument")]:
        L.append(f"| {label} | {fmt(m[key])} |")
    L += ["", f"End-to-end pass excluding the semantic-index examples (the matcher has seen those): {fmt(report['end_to_end_excluding_index_examples'])}"]
    L += ["", f"Latency per question (planner only): {m['latency_ms']} ms", "", "## Guardrails per check", "| Check | Precision | Recall |", "|---|---|---|"]
    for c, v in m["guardrail_per_check"].items():
        L.append(f"| {c} | {fmt(v['precision'])} | {fmt(v['recall'])} |")
    for title, key in [("Pass rate by category", "by_category"), ("Pass rate by source", "by_source"), ("Pass rate by requirement", "by_requirement")]:
        L += ["", f"## {title}", "| Group | Pass |", "|---|---|"] + [f"| {k} | {fmt(v)} |" for k, v in report[key].items()]
    L += ["", "## Failure kinds", "| Kind | Count |", "|---|---|"] + [f"| {k} | {v} |" for k, v in m["failure_kinds"].items()]
    L += ["", "## Failures", "| Id | Query | Kind | Detail |", "|---|---|---|---|"]
    for f in report["failures"]:
        L.append(f"| {f['id']} | {f['query'][:70]} | {f['kind']} | {f['detail'][:70]} |")
    L += ["", "## Confusion (expected -> actual)", "```"]
    for e, row in report["confusion"].items():
        L.append(f"{e:48s} -> " + ", ".join(f"{a} x{n}" for a, n in row.items()))
    L.append("```")
    return "\n".join(L) + "\n"


def build(planner, splits, exclude_overlap):
    rows, scored = run(planner, splits, exclude_overlap)
    rate_of = lambda rs: rate(sum(r["ok"] for r in rs), len(rs))
    reqs = collections.defaultdict(list)
    for r in scored:
        for rid in r["case"]["requirement_ids"]:
            reqs[rid].append(r)
    non_index = [r for r in rows if not r["case"]["index_overlap"]]
    return {
        "end_to_end_excluding_index_examples": rate(sum(r["ok"] for r in non_index), len(non_index)),
        "planner": planner, "splits": splits, "timestamp": datetime.now().isoformat(timespec="seconds"),
        "excluded_overlap": exclude_overlap, "n_cases": len(rows), "n_scored": len(scored),
        "metrics": metrics(scored),
        "by_category": {k: rate_of(v) for k, v in group(scored, lambda r: r["case"]["category"]).items()},
        "by_source": {k: rate_of(v) for k, v in group(scored, lambda r: r["case"]["source"]).items()},
        "by_requirement": {k: rate_of(v) for k, v in sorted(reqs.items())},
        "confusion": confusion(scored),
        "failures": [{"id": r["case"]["id"], "query": r["case"]["query"], "kind": r["kind"], "detail": r["detail"]} for r in scored if not r["ok"]],
    }


# ----------------------------------------------------------------------------- conversations
def build_conversations(planner, splits):
    rows = []
    for conv in casebook.load_conversations(splits):
        if PACE_SECONDS:
            time.sleep(PACE_SECONDS * len(conv["turns"]))
        t0 = time.perf_counter()
        res = runner.evaluate_conversation(conv, planner)
        rows.append({"conv": conv, "res": res, "ms": round(1000 * (time.perf_counter() - t0), 1)})
    turns = [t for r in rows for t in r["res"]["turns"]]
    compared = [t for t in turns if t["state_ok"] is not None]
    first_turn = [r["res"]["turns"][0] for r in rows]
    # turns after an earlier failure are not independent evidence, so also report the
    # accuracy of each conversation's turns up to and including its first failure
    upto = [t for r in rows for t in r["res"]["turns"][: (r["res"]["first_failure"] or len(r["res"]["turns"]))]]
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["conv"]["category"], []).append(r["res"]["ok"])
    reqs = {}
    for r in rows:
        for rid in r["conv"]["requirement_ids"]:
            reqs.setdefault(rid, []).append(r["res"]["ok"])
    kinds = collections.Counter(r["res"]["turns"][r["res"]["first_failure"] - 1]["kind"] for r in rows if not r["res"]["ok"])
    return {
        "suite": "conversations", "planner": planner, "splits": splits, "timestamp": datetime.now().isoformat(timespec="seconds"),
        "n_conversations": len(rows), "n_turns": len(turns),
        "conversation_pass": rate(sum(r["res"]["ok"] for r in rows), len(rows)),
        "turn_pass": rate(sum(t["ok"] for t in turns), len(turns)),
        "turn_pass_up_to_first_failure": rate(sum(t["ok"] for t in upto), len(upto)),
        "memory_state_correct": rate(sum(bool(t["state_ok"]) for t in compared), len(compared)),
        "by_category": {k: rate(sum(v), len(v)) for k, v in sorted(by_cat.items())},
        "by_requirement": {k: rate(sum(v), len(v)) for k, v in sorted(reqs.items())},
        "first_failure_kinds": dict(kinds.most_common()),
        "failures": [{"id": r["conv"]["id"], "category": r["conv"]["category"], "turn": r["res"]["first_failure"],
                      "user": r["res"]["turns"][r["res"]["first_failure"] - 1]["user"],
                      "kind": r["res"]["turns"][r["res"]["first_failure"] - 1]["kind"],
                      "detail": r["res"]["turns"][r["res"]["first_failure"] - 1]["detail"]} for r in rows if not r["res"]["ok"]],
    }


def markdown_conversations(rep):
    L = [f"# RiskSphere conversation evaluation: planner={rep['planner']} splits={','.join(rep['splits'])}",
         f"_{rep['timestamp']}. {rep['n_conversations']} conversations, {rep['n_turns']} turns_", "",
         "## Headline", "| Metric | Value |", "|---|---|",
         f"| Whole conversation passes (every turn right) | {fmt(rep['conversation_pass'])} |",
         f"| Turns that pass | {fmt(rep['turn_pass'])} |",
         f"| Turns that pass, up to each conversation's first failure | {fmt(rep['turn_pass_up_to_first_failure'])} |",
         f"| Memory and pending question correct after the turn | {fmt(rep['memory_state_correct'])} |", "",
         "A conversation fails at its first wrong turn; later turns depend on that state, so the third row is the cleaner estimate of per-turn accuracy.", "",
         "## Pass rate by category", "| Category | Conversations passing |", "|---|---|"]
    L += [f"| {k} | {fmt(v)} |" for k, v in rep["by_category"].items()]
    L += ["", "## Pass rate by requirement", "| Requirement | Conversations passing |", "|---|---|"]
    L += [f"| {k} | {fmt(v)} |" for k, v in rep["by_requirement"].items()]
    L += ["", "## Failures (first failing turn)", "| Id | Category | Turn | User said | Kind | Detail |", "|---|---|---|---|---|---|"]
    for f in rep["failures"]:
        L.append(f"| {f['id']} | {f['category']} | {f['turn']} | {f['user'][:50]} | {f['kind']} | {f['detail'][:80]} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--planner", choices=["offline", "live", "both"], default="offline")
    ap.add_argument("--splits", default="dev")
    ap.add_argument("--suite", choices=["routing", "conversations", "all"], default="all")
    ap.add_argument("--exclude-overlap", action="store_true", help="leave out cases that are semantic-index examples (they are scored by default and also reported separately)")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--pace", type=float, default=None, help="seconds between cases (default 1.5 for live, 0 offline)")
    a = ap.parse_args()
    global PACE_SECONDS
    planners = ["offline", "live"] if a.planner == "both" else [a.planner]
    splits = a.splits.split(",")
    for planner in planners:
        if planner == "offline":
            os.environ.update(PFE_LLM_OFF="1", LLM_API_KEY="", LLM_BASE_URL="", EMBED_API_KEY="", EMBED_BASE_URL="")
        else:
            import agent
            agent.load_secrets()
            os.environ.pop("PFE_LLM_OFF", None)
            if not agent.llm_enabled():
                sys.exit("live planner needs LLM_API_KEY and LLM_BASE_URL (secrets.local.env)")
        os.environ["PFE_KILL_SWITCH"] = "0"
        PACE_SECONDS = a.pace if a.pace is not None else (1.5 if planner == "live" else 0.0)
        stem = f"{datetime.now():%Y%m%d-%H%M%S}-{planner}"
        if a.suite in ("routing", "all"):
            report = build(planner, splits, a.exclude_overlap)
            md = markdown(report)
            print(md)
            if not a.no_save:
                RESULTS.mkdir(exist_ok=True)
                (RESULTS / f"{stem}.json").write_text(json.dumps(report, indent=1))
                (RESULTS / f"{stem}.md").write_text(md)
                print(f"saved tests/results/{stem}.json/.md")
        if a.suite in ("conversations", "all"):
            rep = build_conversations(planner, splits)
            md = markdown_conversations(rep)
            print(md)
            if not a.no_save:
                RESULTS.mkdir(exist_ok=True)
                (RESULTS / f"{stem}-conversations.json").write_text(json.dumps(rep, indent=1))
                (RESULTS / f"{stem}-conversations.md").write_text(md)
                print(f"saved tests/results/{stem}-conversations.json/.md")


if __name__ == "__main__":
    main()
