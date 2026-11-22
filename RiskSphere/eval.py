"""Score the agent against a frozen, categorised question set.

Every row has a gold answer: the tool the planner should pick, or a flag
that the question must be refused. The model is switched off, so the
keyword router runs and the result is deterministic. For each question this
checks three things: did it refuse when it should have and only then, did
the right tool run, and did the wording survive the critic.

It prints end-to-end accuracy, routing and refusal accuracy, a per-category
breakdown, and a small failure taxonomy, then writes the per-question CSV.
The run makes no chat-model calls; a future-exposure question still runs the
Python simulation, so it takes longer than a lookup. Nothing is sent.
"""

import csv
import json
import os
import time
from collections import Counter
from pathlib import Path

import agent
import app

ROOT = Path(__file__).resolve().parent
EVAL_SET = ROOT / "data" / "eval_set.json"
OUT = ROOT / "outputs" / "eval_results.csv"


def _run_one(item):
    """Answer one question and judge it against its gold row.

    Returns a result dict with the tools that ran, whether the question was
    refused, whether routing and refusal were correct, the end-to-end pass,
    and a failure tag when something went wrong.
    """
    started = time.perf_counter()
    payload = app.answer(item["question"])
    latency = time.perf_counter() - started
    steps = (payload.get("plan") or {}).get("steps") or []
    tools = [step["tool"] for step in steps]
    results_rows = payload.get("results") or []
    tool_error = any(not row.get("ok", True) for row in results_rows)
    flags = payload.get("flags") or []
    refused = any(str(f).startswith("refused") or f == "kill_switch" for f in flags)

    expect_refusal = bool(item.get("expect_refusal"))
    expected_tool = item.get("expected_tool") or ""

    refusal_ok = refused == expect_refusal
    if expect_refusal:
        routing_ok = refused
    else:
        routing_ok = (not refused) and (expected_tool in tools)
    passed = refusal_ok and routing_ok

    # task completion: the question ended in a usable state. A refusal row is
    # complete when it refused; a work row is complete when at least one tool
    # ran and none of them errored.
    if expect_refusal:
        completed = refused
    else:
        completed = (not refused) and bool(tools) and not tool_error

    failure = ""
    if not passed:
        if expect_refusal and not refused:
            failure = "missed_refusal"
        elif not expect_refusal and refused:
            failure = "unexpected_refusal"
        elif not expect_refusal and not tools:
            failure = "no_tool"
        elif not expect_refusal and expected_tool not in tools:
            failure = "wrong_tool"
        else:
            failure = "other"

    return {
        "id": item["id"],
        "category": item["category"],
        "question": item["question"],
        "expected_tool": expected_tool,
        "expect_refusal": str(expect_refusal).lower(),
        "tools": ",".join(tools) or "-",
        "tool_calls": len(tools),
        "refused": str(refused).lower(),
        "routing_ok": str(routing_ok).lower(),
        "refusal_ok": str(refusal_ok).lower(),
        "completed": str(completed).lower(),
        "passed": str(passed).lower(),
        "failure": failure,
        "router": (payload.get("plan") or {}).get("router") or "",
        "wording": "yes" if payload.get("wording") else "no",
        "retrieval_mode": ",".join(
            (row.get("output") or {}).get("mode") or ""
            for row in results_rows
            if (row.get("output") or {}).get("mode")
        ),
        "critic_issues": " | ".join(payload.get("critic_issues") or []),
        "latency_s": round(latency, 3),
        "flags": ",".join(flags),
    }


def _percentile(values, fraction):
    """Linear-interpolation percentile of a list. Empty list returns 0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = fraction * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def evaluate(live=False):
    """Run the whole set and return (per-question rows, summary).

    live False switches the chat model off, so the keyword router runs and
    the result is reproducible. live True leaves the model on: Qwen plans
    and phrases, and a failed plan falls back to the keyword router. The
    summary holds the headline accuracies, task completion, the per-category
    pass counts, the failure taxonomy, and the operational numbers: average
    tool calls, latency p50 and p95, chat calls, and token counts.
    """
    if live:
        os.environ.pop("PFE_LLM_OFF", None)
    else:
        os.environ["PFE_LLM_OFF"] = "1"
    agent.CHAT_CALLS = 0
    agent.PROMPT_TOKENS = 0
    agent.COMPLETION_TOKENS = 0
    agent.load_secrets()
    items = json.loads(EVAL_SET.read_text())
    results = []
    for index, item in enumerate(items, 1):
        results.append(_run_one(item))
        if live:
            row = results[-1]
            print(f"[{index}/{len(items)}] {row['id']} {row['tools']} pass={row['passed']}", flush=True)

    total = len(results)
    latencies = [r["latency_s"] for r in results]
    tool_calls = [r["tool_calls"] for r in results]
    summary = {
        "questions": total,
        "end_to_end": sum(1 for r in results if r["passed"] == "true"),
        "routing": sum(1 for r in results if r["routing_ok"] == "true"),
        "refusal": sum(1 for r in results if r["refusal_ok"] == "true"),
        "completed": sum(1 for r in results if r["completed"] == "true"),
        "by_cat_total": dict(Counter(r["category"] for r in results)),
        "by_cat_pass": dict(Counter(r["category"] for r in results if r["passed"] == "true")),
        "taxonomy": dict(Counter(r["failure"] for r in results if r["failure"])),
        "avg_tool_calls": round(sum(tool_calls) / total, 2) if total else 0,
        "latency_p50_s": round(_percentile(latencies, 0.50), 3),
        "latency_p95_s": round(_percentile(latencies, 0.95), 3),
        "chat_calls": agent.CHAT_CALLS,
        "prompt_tokens": agent.PROMPT_TOKENS,
        "completion_tokens": agent.COMPLETION_TOKENS,
        "mode": "live" if live else "model_off",
        "planner_model": sum(1 for r in results if r["router"] == "qwen3-8b"),
        "planner_fallback": sum(1 for r in results if "planner_fallback" in r["flags"]),
        "critic_fallback": sum(1 for r in results if "critic_fallback" in r["flags"]),
        "wording_kept": sum(1 for r in results if r["wording"] == "yes"),
        "tools_used": sorted({
            tool
            for r in results
            for tool in r["tools"].split(",")
            if tool and tool != "-"
        }),
        "retrieval_modes": dict(Counter(
            mode
            for r in results
            for mode in r["retrieval_mode"].split(",")
            if mode
        )),
        "failures": [
            {
                "id": r["id"],
                "category": r["category"],
                "question": r["question"],
                "expected_tool": r["expected_tool"],
                "tools": r["tools"],
                "failure": r["failure"],
                "router": r["router"],
            }
            for r in results
            if r["passed"] != "true"
        ],
        "critic_drops": [
            {"id": r["id"], "question": r["question"], "issues": r["critic_issues"]}
            for r in results
            if r["critic_issues"]
        ],
    }
    return results, summary


def main():
    """Run the set, print the scorecard, and write the per-question CSV."""
    results, s = evaluate()
    for row in results:
        mark = "ok " if row["passed"] == "true" else "FAIL"
        print(f"{mark} {row['id']:<7} {row['tools']:<22} {row['question']}")

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    total = s["questions"]
    print("\nscorecard")
    print(f"  questions            {total}")
    print(f"  end-to-end accuracy  {s['end_to_end']}/{total}  ({s['end_to_end'] / total:.0%})")
    print(f"  task completion      {s['completed']}/{total}  ({s['completed'] / total:.0%})")
    print(f"  routing accuracy     {s['routing']}/{total}  ({s['routing'] / total:.0%})")
    print(f"  refusal accuracy     {s['refusal']}/{total}  ({s['refusal'] / total:.0%})")
    print(f"  avg tool calls       {s['avg_tool_calls']}")
    print(f"  latency p50 / p95    {s['latency_p50_s']}s / {s['latency_p95_s']}s")
    note = "model switched off" if s["mode"] == "model_off" else "Qwen planned and phrased"
    print(f"  chat calls           {s['chat_calls']} ({note})")
    print(f"  tokens in / out      {s['prompt_tokens']} / {s['completion_tokens']}")

    print("\nby category")
    for cat in sorted(s["by_cat_total"]):
        print(f"  {cat:<20} {s['by_cat_pass'].get(cat, 0)}/{s['by_cat_total'][cat]}")

    if s["taxonomy"]:
        print("\nfailure taxonomy")
        for tag, count in sorted(s["taxonomy"].items(), key=lambda kv: -kv[1]):
            print(f"  {tag:<20} {count}")
    else:
        print("\nfailure taxonomy: none")

    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
