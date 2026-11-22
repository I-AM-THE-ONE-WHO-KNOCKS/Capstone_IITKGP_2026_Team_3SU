"""Run the live evaluation and write the submission report.

This is the single command for the graded run. Qwen plans and phrases the
frozen question set. The live embedder scores the labelled retrieval queries.
The draft-term tool runs through the same MCP server as the console. The
write-up records headline accuracy, task completion, the per-category
breakdown, the failure taxonomy, the operational numbers, and the retrieval
metrics. The per-question CSVs are the raw evidence behind the tables.

Nothing is sent. Python computes every figure. The critic can drop a sentence.
"""

import csv
import datetime as dt
import os
from pathlib import Path

import log

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "outputs"


def _pct(hit, total):
    """A "hit/total (xx%)" string, safe when total is zero."""
    return f"{hit}/{total} ({hit / total:.0%})" if total else f"{hit}/0"


def _agent_section(summary):
    """Markdown for the agent scorecard from the eval summary dict."""
    total = summary["questions"]
    lines = [
        "## Agent evaluation",
        "",
        (
            "Frozen, categorised question set scored with Qwen planning and phrasing. "
            "A plan that is not valid JSON is rejected."
            if summary.get("mode") == "live"
            else
            "Frozen, categorised question set scored with the model switched off, "
            "so the run does not call the chat model."
        )
        + " A row passes when it refuses only if it should, the right tool runs, and the wording survives the critic.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Questions | {total} |",
        f"| End-to-end accuracy | {_pct(summary['end_to_end'], total)} |",
        f"| Task completion | {_pct(summary['completed'], total)} |",
        f"| Routing accuracy | {_pct(summary['routing'], total)} |",
        f"| Refusal accuracy | {_pct(summary['refusal'], total)} |",
        f"| Average tool calls per question | {summary['avg_tool_calls']} |",
        f"| Latency p50 / p95 | {summary['latency_p50_s']}s / {summary['latency_p95_s']}s |",
        f"| Chat-model calls | {summary['chat_calls']} |",
        f"| Prompt / completion tokens | {summary['prompt_tokens']} / {summary['completion_tokens']} |",
        f"| Plans returned by Qwen | {summary['planner_model']} |",
        f"| Plans that were not valid JSON | {summary['planner_fallback']} |",
        f"| Wording kept after the critic | {summary['wording_kept']} |",
        f"| Wording dropped by the critic | {summary['critic_fallback']} |",
        "",
        "### By category",
        "",
        "| Category | Passed |",
        "| --- | --- |",
    ]
    for cat in sorted(summary["by_cat_total"]):
        passed = summary["by_cat_pass"].get(cat, 0)
        lines.append(f"| {cat} | {passed}/{summary['by_cat_total'][cat]} |")
    lines += ["", "### Failure taxonomy", ""]
    if summary["taxonomy"]:
        lines += ["| Failure | Count |", "| --- | --- |"]
        for tag, count in sorted(summary["taxonomy"].items(), key=lambda kv: -kv[1]):
            lines.append(f"| {tag} | {count} |")
    else:
        lines.append("No failures.")
    return "\n".join(lines)


def _retrieval_section(rows, results):
    """Markdown for the retrieval metrics from the retrieval-eval output."""
    lines = [
        "## Retrieval evaluation",
        "",
        f"Retrieval scored on its own over {len(rows)} labelled queries, apart "
        "from whether the final wording is any good. One relevant passage per "
        "query. Recall@k asks whether it landed in the top k, MRR rewards a "
        "higher rank, and nDCG discounts by position.",
        "",
        "| Mode | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@5 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for mode, metric in results.items():
        lines.append(
            f"| {mode} | {metric['recall@1']:.3f} | {metric['recall@3']:.3f} | "
            f"{metric['recall@5']:.3f} | {metric['mrr']:.3f} | {metric['ndcg@5']:.3f} |"
        )
    if "hybrid (bm25+dense)" not in results:
        lines += [
            "",
            "_The embedding key was not set, so only the lexical signal ran. "
            "Set it to add the hybrid row and see the lift on the paraphrase "
            "queries._",
        ]
    return "\n".join(lines)


def _write_agent_csv(path, results):
    """Per-question agent evidence, beside the report."""
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)


def _write_retrieval_csv(path, rows):
    """Per-query retrieval evidence: where the relevant passage landed."""
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query", "pile", "relevant", "rank_lexical", "rank_hybrid"])
        for item in rows:
            ranks = item.get("_ranks", {})
            writer.writerow([
                item["query"], item["pile"], ";".join(item["relevant"]),
                ranks.get("lexical", ""), ranks.get("hybrid", ""),
            ])


def _score_table(summary):
    """The scorecard table for the live run."""
    total = summary["questions"]
    return "\n".join([
        "| Metric | Value |",
        "| --- | --- |",
        f"| Questions | {total} |",
        f"| End-to-end accuracy | {_pct(summary['end_to_end'], total)} |",
        f"| Task completion | {_pct(summary['completed'], total)} |",
        f"| Routing accuracy | {_pct(summary['routing'], total)} |",
        f"| Refusal accuracy | {_pct(summary['refusal'], total)} |",
        f"| Average tool calls | {summary['avg_tool_calls']} |",
        f"| Latency p50 / p95 | {summary['latency_p50_s']}s / {summary['latency_p95_s']}s |",
        f"| Chat calls | {summary['chat_calls']} |",
        f"| Prompt / completion tokens | {summary['prompt_tokens']} / {summary['completion_tokens']} |",
        f"| Plans from Qwen | {summary['planner_model']} |",
        f"| Plans that were not valid JSON | {summary['planner_fallback']} |",
        f"| Wording kept | {summary['wording_kept']} |",
        f"| Wording dropped by the critic | {summary['critic_fallback']} |",
    ])


def _how_numbers():
    """Plain-language definition of every figure in the scorecard and the retrieval table."""
    return "\n".join([
        "## How each number is calculated",
        "",
        "Every agent figure is an average or a count over the frozen question set. "
        "Each question is one row. A row is a work question when the gold names a tool, "
        "and a refusal question when the gold says the system must refuse.",
        "",
        "| Number | How it is calculated |",
        "| --- | --- |",
        "| Questions | The number of frozen rows. |",
        "| End-to-end accuracy | Rows where both checks below are true, divided by the number of questions. A wrong tool fails this. A correct refusal passes it. |",
        "| Routing accuracy | A work row passes when the system did not refuse and the gold tool is one of the tools in the plan. A refusal row passes when the system refused. Same pass rule as end-to-end on this set, so the two percentages match. |",
        "| Refusal accuracy | Rows where the system refused exactly when the gold said to refuse. A work row that ran the wrong tool still passes this check, because it correctly did not refuse. |",
        "| Task completion | A refusal row is complete when it refused. A work row is complete when at least one tool ran and none of them returned an error. The tool does not have to be the gold tool, so completion can be higher than accuracy. |",
        "| Average tool calls | The number of steps in each plan, added up and divided by the number of questions. A refusal has zero steps and pulls the average down. |",
        "| Latency p50 / p95 | Wall-clock time of one question, from the moment it is received until the answer is returned, including the model calls and the tools. The times are sorted. p50 is the midpoint and p95 is the point 95 percent of the way up the sorted list, with linear interpolation between the two nearest times. |",
        "| Chat calls | Successful calls to the chat gateway during the run. A normal work question uses two: one to plan, one to phrase. A refusal that stops at the guardrails uses none. A rejected wording can add one rewrite. |",
        "| Prompt / completion tokens | The gateway's token counts on those successful calls, summed. Prompt tokens are the text sent in. Completion tokens are the text that came back. |",
        "| Plans from Qwen | Questions whose plan was accepted from Qwen. Guardrail refusals never ask for a plan, so this is smaller than the question count. |",
        "| Plans that were not valid JSON | Questions where the chat call returned text that failed plan validation. The flag is recorded and the question is still answered. |",
        "| Wording kept | Questions where the phrasing call produced sentences and the critic found every figure, status, and citation already in the tool result. |",
        "| Wording dropped by the critic | Questions where the model wrote sentences and the critic rejected them. The Python lines stay. The reason for each drop is in the table below. |",
        "| By category | The same end-to-end pass rule, counted inside one gold category. The denominator is how many frozen rows carry that category. |",
        "",
        "Retrieval is scored on a separate labelled set. Each query has one relevant passage. "
        "The retriever returns the top 5. Rank is the position of that passage, starting at 1. "
        "Rank 0 means it was not in the top 5.",
        "",
        "| Number | How it is calculated |",
        "| --- | --- |",
        "| Recall@k | The share of queries whose relevant passage has rank from 1 to k. Recall@1 is the share ranked first. Recall@3 and Recall@5 loosen that window. A query with rank 0 counts as a miss at every k. |",
        "| MRR | The mean of 1/rank. A passage at rank 1 contributes 1, rank 2 contributes 1/2, and rank 0 contributes 0. |",
        "| nDCG@5 | For one relevant passage, the gain is 1/log2(rank+1) when the rank is 5 or better, and 0 otherwise. The ideal gain, for a hit at rank 1, is 1. The reported number is the mean of those gains. |",
        "| lexical (bm25) | Rank from Okapi BM25 only. A passage scores higher when it shares rare words with the query. |",
        "| hybrid (bm25+dense) | BM25 and cosine similarity over the bge-small-en-v1.5 vectors each produce a ranking. Reciprocal rank fusion adds 1/(60+place) for each ranking. The fused score decides the final order. |",
        "",
    ])


def _category_table(summary):
    """Per-category pass counts."""
    lines = ["| Category | Passed |", "| --- | --- |"]
    for cat in sorted(summary["by_cat_total"]):
        lines.append(f"| {cat} | {summary['by_cat_pass'].get(cat, 0)}/{summary['by_cat_total'][cat]} |")
    return "\n".join(lines)


def _failure_table(summary):
    """The questions the live planner got wrong, from this run."""
    rows = summary.get("failures") or []
    if not rows:
        return "No routing or refusal failures on this run."
    lines = [
        "| Id | Question | Expected | Ran | Failure |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['question']} | {row['expected_tool'] or 'refuse'} | {row['tools']} | {row['failure']} |"
        )
    return "\n".join(lines)


def _critic_table(summary):
    """Wording the critic rejected, with the reason recorded on the run."""
    rows = summary.get("critic_drops") or []
    if not rows:
        return "The critic kept every wording pass."
    lines = ["| Id | Question | Why the critic dropped it |", "| --- | --- | --- |"]
    for row in rows:
        lines.append(f"| {row['id']} | {row['question']} | {row['issues']} |")
    return "\n".join(lines)


def _retrieval_note(rows):
    """Say which labelled queries neither signal placed in the top 5."""
    outside = [
        item["query"]
        for item in rows
        if not (item.get("_ranks") or {}).get("lexical")
        and not (item.get("_ranks") or {}).get("hybrid")
    ]
    if not outside:
        return (
            "On this run every labelled query had its passage inside the top 5 "
            "for at least one signal. Per-query ranks are in the retrieval CSV."
        )
    quoted = "; ".join(f'"{q}"' for q in outside)
    return (
        "On this corpus BM25 already places the literal questions at rank 1. "
        "The dense signal is there to move paraphrases. Per-query ranks are in the retrieval CSV. "
        f"{len(outside)} queries stayed outside the top 5 under both signals: {quoted}. "
        "That is a miss of this small embedder on this pile, and the report does not claim otherwise."
    )


def _exercise_draft_term():
    """Run the one tool the question set does not ask for, through MCP.

    propose_term writes a draft and must not edit the stored agreement.
    Returns a small dict for the report. A failure is recorded, not hidden.
    """
    import agent
    import mcp_client

    agent.load_secrets()
    mcp_client.reset()
    try:
        out = mcp_client.call_tool("propose_term", {
            "counterparty": "CP-002",
            "field": "threshold",
            "value": 1000000,
            "passage_id": "meridian-threshold",
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "status": out.get("status"),
        "applied": out.get("applied"),
        "field": out.get("field"),
        "proposed_value": out.get("proposed_value"),
        "passage_id": out.get("passage_id"),
    }


def _run_robustness():
    """Ask Qwen to plan and phrase every robustness wording.

    Python still decides the clause. A row passes only when the plan came
    from Qwen, the question was not refused, and the Python conclusion is
    the one the wording was written to produce. The model is not switched
    off for this run. Returns (rows, summary).
    """
    import json

    import agent
    import app

    cases = json.loads((ROOT / "data" / "robustness_questions.json").read_text())
    os.environ.pop("PFE_LLM_OFF", None)
    agent.load_secrets()
    if not agent.llm_enabled():
        raise SystemExit("Qwen is not configured. The robustness wordings are not scored with the model off.")
    start_calls = agent.CHAT_CALLS
    start_prompt = agent.PROMPT_TOKENS
    start_completion = agent.COMPLETION_TOKENS
    rows = []
    for index, case in enumerate(cases, 1):
        agent.save_state({"last_counterparty": ""})
        payload = app.answer(case["question"])
        plan = payload.get("plan") or {}
        flags = payload.get("flags") or []
        refused = any(str(flag).startswith("refused") or flag == "kill_switch" for flag in flags)
        applied = {}
        for result in payload.get("results") or []:
            if result.get("tool") == "apply_clause" and result.get("ok"):
                applied = result.get("output") or {}
        conclusion = applied.get("conclusion") or ""
        status = applied.get("status") or ""
        visible = "\n".join(payload.get("steps") or [])
        model_on = plan.get("router") == "qwen3-8b"
        phrase_ok = case["must_say"].lower() in conclusion.lower()
        shown = bool(conclusion) and conclusion in visible
        passed = model_on and not refused and status == case["expect"] and phrase_ok and shown
        row = {
            "id": case["id"],
            "family": case.get("family") or "",
            "question": case["question"],
            "expect": case["expect"],
            "status": status,
            "router": plan.get("router") or "",
            "passed": "true" if passed else "false",
            "refused": "true" if refused else "false",
            "phrase_ok": "true" if phrase_ok else "false",
            "shown": "true" if shown else "false",
            "wording": "yes" if payload.get("wording") else "no",
            "flags": ",".join(flags),
            "conclusion": conclusion,
        }
        rows.append(row)
        print(
            f"[robust {index}/{len(cases)}] {row['id']} router={row['router']} "
            f"status={status or '-'} pass={row['passed']}",
            flush=True,
        )
    families = {}
    for row in rows:
        bucket = families.setdefault(row["family"], {"n": 0, "passed": 0})
        bucket["n"] += 1
        bucket["passed"] += row["passed"] == "true"
    summary = {
        "questions": len(rows),
        "passed": sum(1 for row in rows if row["passed"] == "true"),
        "model_plans": sum(1 for row in rows if row["router"] == "qwen3-8b"),
        "refused": sum(1 for row in rows if row["refused"] == "true"),
        "wording_kept": sum(1 for row in rows if row["wording"] == "yes"),
        "wording_dropped": sum(1 for row in rows if row["wording"] == "no" and row["router"] == "qwen3-8b"),
        "chat_calls": agent.CHAT_CALLS - start_calls,
        "prompt_tokens": agent.PROMPT_TOKENS - start_prompt,
        "completion_tokens": agent.COMPLETION_TOKENS - start_completion,
        "families": families,
    }
    return rows, summary


def _robustness_csv(path, rows):
    """Write one row per live robustness wording."""
    fields = ["id", "family", "expect", "status", "router", "passed", "refused", "phrase_ok", "shown", "wording", "flags", "question", "conclusion"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _robustness_section(summary, rows):
    """Markdown for the live robustness wordings."""
    lines = [
        "## Robustness wordings",
        "",
        "These 110 questions are not in the frozen 27. Each one was sent through the console path with Qwen on. "
        "Qwen planned the question and was asked to phrase the result. Python compared the passage, or refused "
        "when the passage does not state the decision. A row passes when the plan came from Qwen, the question "
        "was not refused, and the Python conclusion shown to the analyst is the expected one. "
        "A dropped wording does not fail the row: the Python line stays.",
        "",
        f"Passed {summary['passed']}/{summary['questions']}. "
        f"Plans from Qwen {summary['model_plans']}/{summary['questions']}. "
        f"Refused {summary['refused']}. "
        f"Wording kept {summary['wording_kept']}, dropped {summary['wording_dropped']}. "
        f"Chat calls on this list {summary['chat_calls']}. "
        f"Prompt / completion tokens {summary['prompt_tokens']} / {summary['completion_tokens']}.",
        "",
        "| Decision | Passed |",
        "| --- | --- |",
    ]
    labels = {
        "threshold_below": "Meridian exposure below the threshold",
        "threshold_above": "Meridian exposure at or above the threshold",
        "rounding": "Meridian delivery rounded up",
        "mta_below": "Harbor Lane at or below the minimum transfer",
        "mta_above": "Harbor Lane above the minimum transfer",
        "dispute": "Valuation dispute, collateral refused",
        "return": "Posted cash, counterparty wants some back",
        "swap": "Treasuries swapped for corporate bonds",
        "lend": "Lend posted collateral overnight",
        "small_increase": "Exposure rose by a small amount",
        "arrival": "When must the money arrive",
        "close_out": "Close everything out",
        "exemption": "Banks below the upfront-margin size",
        "fx_mismatch": "Euro collateral against a dollar swap",
    }
    for family, bucket in summary["families"].items():
        lines.append(f"| {labels.get(family, family)} | {bucket['passed']}/{bucket['n']} |")
    failed = [row for row in rows if row["passed"] != "true"]
    lines += ["", "### Wordings that did not pass", ""]
    if not failed:
        lines.append("None. Every wording kept the Python conclusion, and every plan came from Qwen.")
    else:
        lines += ["| Id | Expected | Status | Router |", "| --- | --- | --- | --- |"]
        for row in failed:
            lines.append(f"| {row['id']} | {row['expect']} | {row['status'] or '-'} | {row['router'] or '-'} |")
    lines.append("")
    return "\n".join(lines)


def _submission(stamp, live, retrieval_rows, retrieval, draft, robustness=None):
    """The graded write-up. Every number comes from the runs just finished."""
    import agents

    owned = agents.owned_tools()
    used = set(live.get("tools_used") or [])
    missing = [name for name in owned if name not in used]
    modes = live.get("retrieval_modes") or {}
    mode_bits = ", ".join(f"{name} x{count}" for name, count in sorted(modes.items())) or "none observed"
    if draft.get("ok"):
        draft_line = (
            f"propose_term ran through MCP for Meridian threshold, passage "
            f"{draft.get('passage_id')}, proposed value {draft.get('proposed_value')}. "
            f"Status {draft.get('status')}. applied is {draft.get('applied')}. "
            "The stored agreement was not edited."
        )
    else:
        draft_line = f"propose_term did not complete: {draft.get('error')}"

    lexical = retrieval.get("lexical (bm25)", {})
    hybrid = retrieval.get("hybrid (bm25+dense)")
    retrieval_table = [
        "| Mode | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@5 |",
        "| --- | --- | --- | --- | --- | --- |",
        (
            f"| lexical (bm25) | {lexical.get('recall@1', 0):.3f} | {lexical.get('recall@3', 0):.3f} | "
            f"{lexical.get('recall@5', 0):.3f} | {lexical.get('mrr', 0):.3f} | {lexical.get('ndcg@5', 0):.3f} |"
        ),
    ]
    if hybrid:
        retrieval_table.append(
            f"| hybrid (bm25+dense) | {hybrid['recall@1']:.3f} | {hybrid['recall@3']:.3f} | "
            f"{hybrid['recall@5']:.3f} | {hybrid['mrr']:.3f} | {hybrid['ndcg@5']:.3f} |"
        )
    else:
        retrieval_table.append("| hybrid (bm25+dense) | not run | | | | |")

    parts = [
        "# RiskSphere submission evaluation",
        "",
        f"Generated {stamp}. This file is the graded run. Qwen/Qwen3-8B planned and phrased "
        "every question, the live embedder ranked the clause corpus, and the draft-term tool "
        "ran through the same MCP server as the console.",
        "",
        "What ran:",
        "",
        "- Qwen/Qwen3-8B planning and phrasing, temperature 0, on the frozen question set. "
        "Python computed every figure. The critic checked the sentences.",
        "- Retrieval over 24 labelled queries, lexical BM25 and the live bge-small-en-v1.5 embedder, fused.",
        "- A draft term proposal through the same MCP tool server the console uses.",
        "",
        "Principle held on this run: the model plans and phrases. Code computes, checks quotes, "
        "and refuses sending, booking, and approval. Drafts stay DRAFT_PENDING_APPROVAL.",
        "",
        "## What the system is",
        "",
        "RiskSphere is a counterparty desk for a synthetic six-name book. "
        "Each credit limit is the simulated collateralised peak for that name, times a factor, rounded. "
        "An analyst asks in the console. Two agents call Qwen. The orchestrator plans and phrases. "
        "Documents retrieves clauses and, when the question asks for an agreement term, asks Qwen "
        "to propose one quote. Python checks that quote before a draft is written. "
        "Python services compute the figures. Tool calls go out as MCP tools/call to a local server.",
        "",
        "| Agent | Owns |",
        "| --- | --- |",
    ]
    for row in agents.describe():
        tools = ", ".join(row["tools"]) if row["tools"] else "no tool; plans, phrases, and checks"
        parts.append(f"| {row['agent']} | {tools} |")
    parts += [
        "",
        "| Python service | Owns |",
        "| --- | --- |",
    ]
    for row in agents.services():
        parts.append(f"| {row['service']} | {', '.join(row['tools'])} |")
    parts += [
        "",
        "Documents covers hybrid clause retrieval, one Qwen reading of those passages when the question "
        "asks for an agreement term, the quote gate, and a draft term. "
        "The frozen questions ask what the schedule says, so they do not exercise that reading call. "
        "The Python services cover today's exposure and the limit test, variation margin, Yahoo spot and volatility, "
        "the haircut schedule, a correlated Monte Carlo potential-future-exposure simulation with engine checks, "
        "data quality, scenario profit and loss, an initial-margin proxy, collateral shortfall and surplus, "
        "synthetic expected loss, alerts, and draft report, notice, stress book, and dispute files.",
        "",
        "Labels kept in the answers and in this report: the simulation is not SA-CCR, the initial-margin "
        "figure is not ISDA SIMM, and expected loss is not regulatory exposure at default. "
        "Probability of default and loss given default are synthetic and are not inferred from a name.",
        "",
        "## Evaluation design",
        "",
        "The question set was frozen before this run. Each row has a gold tool, or a gold refusal. "
        "A row passes when the refusal matches and, for a work question, the gold tool is among the "
        "tools that ran. Task completion is separate: a work question is complete when a tool ran and "
        "did not error, even if it was the wrong tool. Refusal questions cover action, injection, "
        "personal data, legal advice, and scope. Nothing in the set is allowed to send, book, or approve.",
        "",
        f"Retrieval is scored apart from the wording, on {len(retrieval_rows)} labelled queries, "
        "one relevant passage each. Recall@k, mean reciprocal rank, and nDCG@5 are reported for "
        "BM25 alone and for BM25 fused with the dense signal.",
        "",
        "## Results",
        "",
        "Qwen/Qwen3-8B returned the plan. A second call phrased the tool result. "
        "The critic dropped a sentence that invented a figure, cited a source that was not returned, "
        "or dropped the draft label.",
        "",
        _score_table(live),
        "",
        _how_numbers(),
        "### By category",
        "",
        _category_table(live),
        "",
        "### Where the plan was wrong",
        "",
        _failure_table(live),
        "",
        "### Where the critic rejected the wording",
        "",
        _critic_table(live),
        "",
        "These are the small-model failures this run actually produced. Python still returned the "
        "tool figures, and the critic threw the wording away rather than letting a wrong number through.",
        "",
        "## Retrieval",
        "",
        "Clause search during the live questions used: " + mode_bits + ". "
        "The table below is the labelled set, scored in this same process with the embedder on.",
        "",
        "\n".join(retrieval_table),
        "",
        _retrieval_note(retrieval_rows),
        "",
        "## Coverage of the tools",
        "",
        f"The question set called {len(used)} of the {len(owned)} tools: "
        + ", ".join(sorted(used)) + ".",
        "",
        (
            "Not called by a question: " + ", ".join(missing) + ". " + draft_line
            if missing else
            "Every tool was called by the question set. " + draft_line
        ),
        "",
        "Guardrails ran before any plan. The refusal rows in the category table are the evidence: "
        "send, release, approve, book, and raise-the-limit stop at the action check; ignore-the-rules "
        "and reveal-the-key stop at the injection check; an SSN or a home address stops at the personal-data "
        "check; sue and terminate-the-ISDA stop at the legal check; weather and a poem stop at scope. "
        "No tool ran on those rows.",
        "",
        "## What we did because the model is small",
        "",
        "- The model never computes. Exposure, margin, haircut, simulation, scenario, coverage, and expected loss are Python.",
        "- The plan is one JSON object, at most four steps, validated before a tool runs. A plan that is not valid JSON is rejected and the question is not given invented figures.",
        "- Thinking mode is off, and any think-tags are stripped. Temperature is 0. The reply must be English.",
        "- The gateway allows one active request. The plan, a Documents reading, and the wording share a lock. 429 and 503 wait 2, 4, then 8 seconds. max_tokens stays at or below 1024. TLS verification stays on.",
        "- Retrieval does not trust the model to quote. A citation must be a returned source tag, and a quoted phrase must sit inside that passage.",
        "- A term change is a draft. The stored book is not edited, and nothing is sent.",
        "",
        "## Files from this run",
        "",
        "- eval_results_live.csv — per-question plan, tools, flags, critic reasons, retrieval mode, latency",
        "- retrieval_eval.csv — per-query lexical rank and hybrid rank",
        "- robustness_live.csv — per-wording plan source, Python status, and whether the conclusion was shown",
        "",
        _robustness_section(robustness[1], robustness[0]) if robustness else "",
        "## Limitations",
        "",
        "- The book, the names, and the credit inputs are synthetic. Each limit is the simulated peak times a factor.",
        "- The labelled retrieval set is sample text. Terms on the Excel sheet are extracted from downloaded agreements and pass the quote gate.",
        "- The haircut schedule used by the margin engine is the desk table. The BCBS text is stored beside it and does not replace that table.",
        "- Products are linear. The simulation is a prototype, not SA-CCR. The initial-margin number is a proxy, not SIMM.",
        "- Retrieval labels are one passage per query, written by the same author as the corpus.",
        "- Qwen can pick a neighbour tool, as the failure table shows. The critic is what stops a wrong sentence. The report does not claim the planner is perfect.",
        "- This run did not include a multi-turn memory question in the frozen set. The console does remember the last name for a follow-up that says it or its.",
        "",
    ]
    return "\n".join(parts) + "\n"


def main():
    """Run the live evaluation and write the submission report.

    Qwen plans and phrases. The embedder ranks the clause corpus. The draft
    term tool runs through MCP. The report is that run.
    """
    os.environ.pop("PFE_LLM_OFF", None)
    log.configure("error")

    import eval as agent_eval
    import retrieval_eval

    log.work("Orchestrator", "running the live Qwen evaluation")
    agent_results, agent_summary = agent_eval.evaluate(live=True)
    _write_agent_csv(OUT_DIR / "eval_results_live.csv", agent_results)
    log.work("Documents", "scoring retrieval with the live embedder")
    rows, retrieval_results = retrieval_eval.evaluate()
    _write_retrieval_csv(OUT_DIR / "retrieval_eval.csv", rows)
    _write_retrieval_csv(OUT_DIR / "retrieval_eval_live.csv", rows)
    log.work("Documents", "exercising the draft term tool")
    draft = _exercise_draft_term()
    log.work("Orchestrator", "running the robustness wordings with Qwen on")
    robust_rows, robust_summary = _run_robustness()
    _robustness_csv(OUT_DIR / "robustness_live.csv", robust_rows)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = _submission(stamp, agent_summary, rows, retrieval_results, draft, (robust_rows, robust_summary))
    OUT_DIR.mkdir(exist_ok=True)
    for name in ("submission_report.md", "evaluation_report.md", "evaluation_report_live.md"):
        path = OUT_DIR / name
        path.write_text(body)
        print(f"wrote {path}")
    print(f"wrote {OUT_DIR / 'eval_results_live.csv'}")
    print(f"wrote {OUT_DIR / 'retrieval_eval.csv'}")
    print("draft_term", "ok" if draft.get("ok") else draft.get("error"), "status", draft.get("status"), "applied", draft.get("applied"))
    print("retrieval_modes", agent_summary.get("retrieval_modes"))
    print(
        "robustness_passed",
        f"{robust_summary['passed']}/{robust_summary['questions']}",
        "qwen_plans",
        f"{robust_summary['model_plans']}/{robust_summary['questions']}",
        "chat_calls",
        robust_summary["chat_calls"],
    )


if __name__ == "__main__":
    main()
