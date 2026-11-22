"""RiskSphere console.

Start with `python3 app.py`. The process prints a ready line and waits
for a question. An empty line, quit, or exit stops it. Each question is
answered by answer(), which runs the guardrails, the planner, then each
calculation as an MCP tools/call, then an optional wording pass.

Nothing in this module computes a mark. It explains what the tools returned.
"""

import json
import re
import time

import agent
import log
import mcp_client
import stage4


def _say(steps, line):
    """Keep one explanation line and mirror it to the trace log.

    steps is the list that will be printed after the answer. The log line
    is attributed to the caller, not to this helper.
    """
    steps.append(line)
    log.trace(line)


def _money(n):
    """Format a dollar amount as $1,234 or -$1,234, rounded to the nearest dollar."""
    sign = "-" if n < 0 else ""
    return sign + f"${abs(int(round(n))):,}"


def _trade_line(trade):
    """One console sentence for a single trade mark.

    trade is a row from the current-exposure result: id, symbol, quantity,
    entry, Yahoo close, as-of date, and the mark. The sentence states the
    formula quantity times (close minus entry).
    """
    qty = trade["quantity"]
    qty_txt = str(int(qty)) if qty == int(qty) else str(qty)
    return (
        f"Mark for {trade['trade_id']}. Downloaded two years of daily closes for {trade['symbol']} from Yahoo "
        f"and wrote {trade['csv']}. The mark uses the last close in that file, {trade['yahoo_price']} on {trade['asof']}. "
        f"The contract in trades.csv is quantity {qty_txt} at entry {trade['entry']}. "
        f"MtM is quantity times (close minus entry), which is {_money(trade['mtm_usd'])}."
    )


def _collateral_sentence(name, line):
    """One sentence for a posted asset. Ineligible assets count as zero."""
    if line.get("eligible") is False:
        return (
            f"Collateral for {name}. {line['asset']} is not an eligible asset, "
            f"face {_money(line['amount'])}. It counts as zero."
        )
    return (
        f"Collateral for {name}. {line['asset']} face {_money(line['amount'])}. "
        f"The schedule row is {line['asset_class']}, {line['rating_bucket']}, {line['maturity_bucket']}, "
        f"a {line['haircut_pct']}% haircut. Amount that counts is {_money(line['counted'])}."
    )


def _status_how(output):
    """Sentence that says why the status is BREACH, WARNING, or OK.

    BREACH is utilization over 100%. WARNING is at least 85% and not over
    100%. OK is under 85%. output must contain status.
    """
    status = output["status"]
    if status == "BREACH":
        rule = "Status is BREACH because exposure is over 100% of the limit."
    elif status == "WARNING":
        rule = "Status is WARNING because exposure is at least 85% of the limit and not over 100%."
    else:
        rule = "Status is OK because exposure is under 85% of the limit."
    return rule


def _tool_lines(row):
    """Turn one tool result into the sentences printed under the answer.

    row is {tool, ok, output} or {tool, ok: False, error}. A failed tool
    becomes a single stopped line. A successful tool is explained in the
    order a person would recompute it: marks, collateral, then the formula.
    The model is not called here.
    """
    if not row.get("ok"):
        return [f"{row.get('tool')}: stopped. {row.get('error')}"]
    output = row["output"]
    name = row["tool"]
    if name == "current_exposure":
        lines = [_trade_line(trade) for trade in output.get("trades") or []]
        for line in output.get("collateral") or []:
            lines.append(_collateral_sentence(output["counterparty_name"], line))
        lines.append(
            f"Current exposure, in Python, no model. Added the trade marks to get gross MtM "
            f"{_money(output['gross_mtm_usd'])}. Subtracted collateral {_money(output['collateral_after_haircut_usd'])}. "
            f"Exposure is the larger of that difference and zero, {_money(output['current_exposure_usd'])}. "
            f"The limit {_money(output['credit_limit_usd'])} is the number stored in limits.csv, not a simulated PFE. "
            f"Headroom is limit minus exposure, {_money(output['headroom_usd'])}. "
            f"Utilization is exposure divided by the limit, {output['utilization_pct']}%. "
            f"{_status_how(output)}"
        )
        return lines
    if name == "calculate_margin_call":
        lines = []
        for line in output.get("collateral") or []:
            lines.append(_collateral_sentence(output["counterparty_name"], line))
        lines.append(
            f"Margin call, in Python, no model. Read threshold {_money(output['threshold_usd'])}, "
            f"minimum transfer {_money(output['mta_usd'])}, and rounding {_money(output['rounding_usd'])} from csa_terms.csv. "
            f"Credit support is the larger of zero and (mark {_money(output['net_mtm_usd'])} "
            f"plus independent amount {_money(output['independent_amount_usd'])} "
            f"minus threshold), which is {_money(output['credit_support_amount_usd'])}. "
            f"Raw delivery is credit support minus collateral {_money(output['collateral_after_haircut_usd'])}, "
            f"which is {_money(output['raw_delivery_usd'])}. "
            f"A positive raw amount at least as large as the minimum transfer is rounded up, "
            f"so delivery is {_money(output['delivery_amount_usd'])}. "
            f"A return would be rounded down, and that amount is {_money(output['return_amount_usd'])}. "
            f"Action {output['action']}."
        )
        if output.get("requires_human_approval"):
            lines.append(
                "Approval flag. The action is not NO_ACTION, so the tool sets requires_human_approval. "
                "This page does not send the call. A person still has to review it."
            )
        return lines
    if name == "check_pfe_limit":
        fan = ", ".join(
            f"step {row['step']} p95 {_money(row['p95'])}" for row in output.get("fan") or []
        )
        return [
            f"Future exposure, in Python, no model. Engine checks ran first. "
            f"Then {output['n_paths']} correlated GBM paths, flat vol from the Yahoo history, "
            f"margin period of risk {output['mpor_days']} steps. "
            f"Collateral in the paths is variation margin, not the cash balance in collateral.csv. "
            f"Peak collateralised PFE at quantile {output['pfe_quantile']} is "
            f"{_money(output['peak_pfe_collateralized_usd'])} at step {output['peak_step']}. "
            f"Uncollateralised peak {_money(output['peak_pfe_uncollateralized_usd'])}. "
            f"Today's simulated mark {_money(output['current_mtm_usd'])}. "
            f"Stored limit {_money(output['credit_limit_usd'])}. "
            f"Utilization {output['utilization_pct']}%. Status {output['status']}. "
            f"Fan, collateralised p95: {fan}."
        ]
    if name == "get_market_data":
        return [
            f"Market data for {output['ticker']}. Downloaded two years of Yahoo daily closes and wrote {output['csv']}. "
            f"Spot is the last close, {output['spot']} on {output['asof']}. "
            f"One-year volatility is the standard deviation of the last {output['closes_used']} daily log-returns, "
            f"times the square root of 252, which is {output['ann_vol_1y_pct']}%. The model did not compute this."
        ]
    if name == "regulatory_haircut":
        return [
            f"Haircut lookup, in Python, no model. The plan asked for {output['asset_class']}, "
            f"{output['rating_bucket']}, {output['maturity_bucket']}. "
            f"books.py holds the d499 table and returned {output['haircut_pct']}%. "
            f"{output['source']}"
        ]
    extra = _extra_lines(output)
    if extra is not None:
        return extra
    return [json.dumps(output)]


def _extra_lines(output):
    """Console sentences for stages 3 to 5. None when this result is an earlier tool."""
    name = output.get("tool")
    if name == "check_data_quality":
        if output.get("passed"):
            return [
                f"Data check, in Python. I read {output['rows_checked']} rows. "
                "Ids, dates, units, and signs passed. The model did not fill a blank."
            ]
        problems = "; ".join(
            f"{row['where']}: {row['problem']}" for row in output.get("problems") or []
        )
        return [f"Data check failed. No figure was computed. {problems}"]
    if name == "scenario_pnl":
        return [
            f"Scenario, in Python. {output['scenario_label']}. "
            f"Today's mark {_money(output['base_mtm_usd'])}. "
            f"Shocked mark {_money(output['shocked_mtm_usd'])}. "
            f"Profit and loss is the difference, {_money(output['scenario_pnl_usd'])}. "
            "Trades the shock does not name stay at today's mark."
        ]
    if name == "initial_margin_proxy":
        return [
            f"Initial margin proxy, in Python. {output['label']}. "
            f"Horizon {output['horizon_days']} days, percentile {output['percentile']}. "
            f"The figure is {_money(output['initial_margin_usd'])}. "
            "This is not the variation-margin call."
        ]
    if name == "collateral_coverage":
        skipped = ", ".join(output.get("ineligible_assets") or []) or "none"
        return [
            f"Coverage, in Python. Requirement {_money(output['requirement_usd'])}. "
            f"Eligible collateral {_money(output['eligible_collateral_usd'])}. "
            f"Shortfall {_money(output['shortfall_usd'])}. Surplus {_money(output['surplus_usd'])}. "
            f"Ineligible assets: {skipped}."
        ]
    if name == "expected_loss":
        return [
            f"Expected loss, in Python. PD {output['pd']} times LGD {output['lgd']} times "
            f"EAD {_money(output['ead_usd'])}. The EAD is {output['ead_label']}. "
            f"Expected loss {_money(output['expected_loss_usd'])}. Source {output['pd_source']}."
        ]
    if name == "search_clauses":
        hits = output.get("hits") or []
        if not hits:
            return [f"Clause search in the {output['pile']} pile. No sentence matched."]
        shown = "  ".join(f"[{hit.get('source', 'S?')}] {hit['text']}" for hit in hits[:2])
        return [
            f"Clause search in the {output['pile']} pile, {output['mode']}. "
            f"A hit does not change the stored terms. {shown}"
        ]
    if name == "propose_term":
        return [
            f"Draft term for {output['counterparty_id']} {output['field']}. "
            f"Proposed {output['proposed_value']}, current {output['current_value']}. "
            f"Status {output['status']}. The stored agreement was not changed."
        ]
    if name == "read_term":
        draft = "A draft was written." if output.get("draft_status") else "No draft was written."
        reason = output.get("reason") or ""
        return [
            f"Documents asked Qwen to read the retrieved clause. "
            f"Field {output.get('field') or 'none'}. Status {output.get('status')}. "
            f"{reason} {draft} The stored agreement was not changed."
        ]
    if name == "list_alerts":
        if not output.get("alerts"):
            return [f"Alerts. None. Stale window is {output['stale_after_days']} days."]
        shown = "; ".join(row["detail"] for row in output["alerts"])
        return [f"Alerts, {output['alert_count']}. {shown}"]
    if name == "draft_report":
        return [
            f"Draft report {output['run_id']} for {output['counterparty_name']}. "
            f"Status {output['status']}. Today's exposure {_money(output['current_exposure_usd'])}, "
            f"status {output['exposure_status']}. Shortfall {_money(output['shortfall_usd'])}. "
            f"Expected loss {_money(output['expected_loss_usd'])}. "
            f"Seed {output['pfe_seed']}, paths {output['pfe_paths']}. Nothing was sent."
        ]
    if name == "draft_margin_notice":
        return [
            f"Draft margin notice {output['run_id']} for {output['counterparty_name']}. "
            f"Action {output['action']}. Delivery {_money(output['delivery_amount_usd'])}. "
            f"Return {_money(output['return_amount_usd'])}. Status {output['status']}. It was not sent."
        ]
    if name == "draft_stress_book":
        shown = "; ".join(
            f"{row['counterparty_id']} {row['scenario']} {_money(row['scenario_pnl_usd'])}"
            for row in (output.get("rows") or [])[:6]
        )
        return [f"Draft stress book {output['run_id']}. Status {output['status']}. {shown}"]
    if name == "draft_dispute":
        return [
            f"Draft dispute {output['run_id']} for {output['counterparty_name']}. "
            f"Our mark {_money(output['our_mtm_usd'])}. Their mark {_money(output['their_mtm_usd'])}. "
            f"Gap {_money(output['gap_usd'])}. Verdict {output['verdict']}. "
            f"Disputed {_money(output['disputed_usd'])}. Status {output['status']}. Nothing was sent."
        ]
    return None


def _remember(question, results, state):
    """Store the counterparty this question was about, for a later "its".

    A name inside a successful tool result wins. Otherwise a name typed in
    the question is stored. A haircut or a ticker question leaves the
    previous name in place when neither source has one.
    """
    cid = books_find(question)
    for row in results:
        output = row.get("output") or {}
        if output.get("counterparty_id"):
            cid = output["counterparty_id"]
    if cid:
        state["last_counterparty"] = cid
        agent.save_state(state)


def _documents_reading(question, plan, results, state, steps, flags):
    """Have Documents read the retrieved passages and propose one term.

    Used only when the plan came from Qwen and the question asks for an
    agreement number, or the planner tried to supply that number itself.
    Retrieval stays BM25 plus the embedder. Qwen sees the hits and returns
    a quote. The caller appends the reading row. A missing passage does not
    call the model.
    """
    hits = []
    for row in results:
        if not (row.get("ok") and row.get("tool") == "search_clauses"):
            continue
        output = row.get("output") or {}
        pile = output.get("pile") or (row.get("args") or {}).get("pile")
        if pile == "csa":
            hits = output.get("hits") or []
    planned = [step for step in plan.get("steps") or [] if step["tool"] == "propose_term"]
    if not hits and planned:
        import books
        passage_id = planned[-1]["args"].get("passage_id")
        for clause in books.clause_rows():
            if clause.get("id") == passage_id and (clause.get("pile") or "csa") == "csa":
                hits = [{
                    "id": clause["id"],
                    "source": "S1",
                    "text": clause["text"],
                    "title": clause.get("title", ""),
                }]
    if not hits:
        try:
            found = mcp_client.call_tool("search_clauses", {"query": question[:500], "pile": "csa"})
        except Exception as exc:
            flags.append("tool_error")
            _say(steps, f"Documents could not retrieve a passage: {exc}")
            return None
        hits = found.get("hits") or []
        results.append({
            "tool": "search_clauses",
            "args": {"query": question[:500], "pile": "csa"},
            "ok": True,
            "output": found,
        })
        for line in _tool_lines(results[-1]):
            _say(steps, line)
    if not hits:
        _say(steps, "Documents did not call Qwen. Retrieval returned no passage to read.")
        return None
    cid = planned[-1]["args"].get("counterparty") if planned else ""
    if not cid:
        cid = books_find(question) or (state.get("last_counterparty") or "")
    try:
        output = agent.read_clause(question, hits, cid)
    except Exception as exc:
        log.error(f"read_term failed: {exc}")
        flags.append("tool_error")
        return {"tool": "read_term", "args": {"counterparty": cid}, "ok": False, "error": str(exc), "output": {}}
    return {"tool": "read_term", "args": {"counterparty": cid}, "ok": True, "output": output}


def books_find(question):
    """Return the counterparty id named in the question, or an empty string.

    Matches the id, the full name, or the first word (Meridian, Apex, Harbor).
    Does not call Yahoo.
    """
    import books
    return books.find_party(books.names_only(), question)


def answer(question):
    """Run one question end to end and return the payload for the printer.

    Order: load settings and the remembered name, run prefilter, plan with
    Qwen or with keywords, call each tool through the local MCP server,
    optionally ask for wording and run the critic, then append a trace record.

    question is the raw text, already stripped, at most 1000 characters
    as enforced by main(). Returns a dict with question, steps, plan
    (prompt removed), results, wording, and flags. wording is empty when
    the critic dropped the sentences or the model was not called. The
    Python step lines are still in steps.
    """
    started = time.time()
    agent.load_secrets()
    cfg = agent.load_config()
    state = agent.load_state()
    steps = []
    _say(steps, f"Question received, no calculation yet: {question}")
    plan = {}
    results = []
    flags = []
    wording = ""

    report = agent.prefilter(question)
    why = report["stop"]
    for check in report["checks"]:
        if not check["ran"]:
            _say(steps, f"Guardrail {check['name']}: not run. An earlier check already stopped the question.")
            continue
        verdict = "passed" if check["passed"] else "stopped the question"
        _say(steps, f"Guardrail {check['name']}: {verdict}. {check['how']}")
    if why == "kill":
        flags.append("kill_switch")
        _finish(question, plan, results, flags, steps, started)
        return _payload(question, steps, plan, results, wording, flags)
    if why:
        sentence = cfg["refusals"].get(why, cfg["refusals"]["scope"])
        _say(steps, f"Stop. {sentence} No plan was sent and no tool ran.")
        flags.append("refused:" + why)
        _finish(question, plan, results, flags, steps, started)
        return _payload(question, steps, plan, results, wording, flags)
    model_error = ""
    if agent.llm_enabled():
        try:
            plan = agent.plan_with_model(question, state)
        except Exception as exc:
            model_error = str(exc)
            plan = agent.heuristic_plan(question, state)
            flags.append("planner_fallback")
    else:
        plan = agent.heuristic_plan(question, state)
        model_error = "The model was not called (no key, or PFE_LLM_OFF=1)."
        flags.append("rules_only")

    who = "Qwen/Qwen3-8B" if plan.get("router") == "qwen3-8b" else "the keyword planner"
    step_txt = ", ".join(
        f"{step['tool']}({', '.join(f'{k}={v}' for k, v in step['args'].items())})"
        for step in plan.get("steps") or []
    ) or "no steps"
    _say(steps, 
        f"Planner. {who} read the question and had to return one JSON object naming a tool. "
        f"It chose {step_txt}. Intent label '{plan.get('intent')}'. {plan.get('reason', '')} "
        f"This step does not calculate a dollar figure."
    )
    if model_error and plan.get("router") != "qwen3-8b":
        _say(steps, "Model note: " + model_error)

    if plan.get("refuse") or not plan.get("steps"):
        _say(steps, plan.get("refusal_reason") or "No tool was run.")
        flags.append("refused:planner")
        _finish(question, plan, results, flags, steps, started)
        return _payload(question, steps, plan, results, wording, flags)

    mcp_client.reset()
    seen = set()
    documents_reads = agent.llm_enabled() and plan.get("router") == "qwen3-8b" and (
        agent.term_request(question)
        or any(step["tool"] == "propose_term" for step in plan.get("steps") or [])
    )
    for step in plan["steps"]:
        if documents_reads and step["tool"] == "propose_term":
            flags.append("documents_reads")
            _say(steps, "Documents will read the retrieved clause. The planner's number is not written as a draft.")
            continue
        key = (step["tool"], json.dumps(step["args"], sort_keys=True))
        if key in seen:
            flags.append("duplicate_step")
            _say(steps, f"Skipped a repeated {step['tool']} with the same arguments.")
            continue
        seen.add(key)
        try:
            output = mcp_client.call_tool(step["tool"], step["args"])
            row = {"tool": step["tool"], "args": step["args"], "ok": True, "output": output}
        except Exception as exc:
            log.error(f"{step['tool']} failed: {exc}")
            row = {"tool": step["tool"], "args": step["args"], "ok": False, "error": str(exc), "output": {}}
            flags.append("tool_error")
        results.append(row)
        for line in _tool_lines(row):
            _say(steps, line)

    reading = _documents_reading(question, plan, results, state, steps, flags) if documents_reads else None
    if reading:
        results.append(reading)
        for line in _tool_lines(reading):
            _say(steps, line)

    _remember(question, results, state)
    usable = [row for row in results if row["ok"]]
    critic_issues = []
    if agent.llm_enabled() and plan.get("router") == "qwen3-8b" and usable:
        issues = ["not checked"]
        for _ in range(cfg.get("critic_retries", 1) + 1):
            try:
                wording, _prompt = agent.compose(question, usable, issues if issues != ["not checked"] else None)
            except Exception as exc:
                model_error = (model_error + " " + str(exc)).strip()
                wording = ""
                break
            issues = agent.critic(wording, usable)
            if not issues:
                break
        if wording and not issues:
            _say(steps, 
                "Wording. Sent the tool JSON to Qwen/Qwen3-8B and asked for a few sentences that only use figures already in that JSON. "
                + wording
            )
            _say(steps, 
                "Critic, in Python. Scanned the sentences for large numbers and for the status word, "
                "and checked each one against the tool JSON. All of them were already there, so the sentences stay."
            )
        elif wording and issues:
            flags.append("critic_fallback")
            critic_issues = list(issues)
            _say(steps, 
                "Critic, in Python. Scanned the sentences against the tool JSON and rejected them: "
                + "; ".join(issues)
                + ". The Python lines above are the result."
            )
            wording = ""
        elif model_error:
            _say(steps, "Model note: " + model_error)
    elif not agent.llm_enabled():
        _say(steps, "No wording call. The Python lines are the answer.")

    _finish(question, plan, results, flags, steps, started)
    payload = _payload(question, steps, plan, results, wording, flags, critic_issues)
    log.debug("plan " + json.dumps(payload["plan"]))
    log.debug("flags " + json.dumps(flags))
    return payload


def _payload(question, steps, plan, results, wording, flags, critic_issues=None):
    """Assemble the dict main() prints. The planner prompt is left out."""
    return {
        "question": question,
        "steps": [step for step in steps if step],
        "plan": {k: v for k, v in plan.items() if k != "prompt"},
        "results": results,
        "wording": wording,
        "flags": flags,
        "critic_issues": list(critic_issues or []),
    }


def _finish(question, plan, results, flags, steps, started):
    """Append the trace line for this question. Does not include the prompt.

    started is time.time() from the beginning of answer(), used for latency.
    """
    agent.trace({
        "ts": time.time(),
        "query": question,
        "plan": {k: v for k, v in plan.items() if k != "prompt"},
        "tools": [{"tool": row["tool"], "ok": row["ok"], "error": row.get("error")} for row in results],
        "flags": flags,
        "latency_s": round(time.time() - started, 3),
    })


def _print_answer(payload):
    """Print the wording, or a note that the Python lines are the answer, then each step."""
    print()
    print(payload["wording"] or "Python lines are the answer.")
    print()
    for step in payload["steps"]:
        print(step)
        print()


def main():
    """Prompt loop. Loads secrets and the log level once, then reads questions.

    EOF, an empty line, quit, or exit returns. A question longer than 1000
    characters is rejected and the loop continues.
    """
    agent.load_secrets()
    log.configure()
    print("RiskSphere ready. Type a question. Press Enter on an empty line to stop.")
    print("A draft stays unsigned until you type: approve <id> <field> by <your name>")
    print("Type pending to see drafts that are still waiting.")
    while True:
        try:
            question = input("Question: ").strip()
        except EOFError:
            print()
            mcp_client.stop()
            return
        if not question or question.lower() in ("quit", "exit"):
            mcp_client.stop()
            return
        if question.lower() == "pending":
            waiting = stage4.list_pending()
            if not waiting:
                print("No draft is waiting.")
            for row in waiting:
                print(
                    f"{row['counterparty_id']} {row['field']} "
                    f"{row.get('current_value')} -> {row.get('proposed_value')} "
                    f"passage {row.get('passage_id')}"
                )
            continue
        signed = re.fullmatch(r"approve\s+(\S+)\s+(\S+)\s+by\s+(.+)", question, re.I)
        if signed:
            try:
                applied = stage4.approve_term(signed.group(1).lower(), signed.group(2).lower(), signed.group(3))
            except ValueError as exc:
                print(exc)
            else:
                print(
                    f"Signed by {applied['approved_by']}. {applied['counterparty_id']} "
                    f"{applied['field']} is now {applied['proposed_value']}. "
                    f"Backup {applied['backup']}."
                )
            continue
        if len(question) > 1000:
            log.error("question longer than 1000 characters")
            print("Keep the question under 1000 characters.")
            continue
        _print_answer(answer(question))


if __name__ == "__main__":
    main()
