"""Run one routing case through the real agent code and say what, if anything, went wrong.

Planners:
  offline  guardrails + keyword planner (no network)         -> agent.heuristic_plan
  live     guardrails + the hosted Qwen planner (needs keys) -> agent.plan_with_model
"""

import agent

# failure kinds, most specific first
GUARD_MISSED = "guard_missed"          # should have been stopped, was not
GUARD_WRONG = "guard_wrong_check"      # stopped by a different check
FALSE_REFUSAL = "false_refusal"        # a valid question was stopped
CLAR_WRONG = "clarification_wrong"     # asked / did not ask when it should have
CLAR_ARG = "clarification_arg"         # asked about the wrong argument
TOOLS_WRONG = "wrong_tools"
ARGS_WRONG = "wrong_args"
ERROR = "error"


def plan_for(question, state, planner):
    if planner == "live":
        return agent.plan_with_model(question, state)
    return agent.heuristic_plan(question, state)


def evaluate(case, planner="offline"):
    """Return (ok, kind, detail, outcome). outcome is a small dict for confusion matrices."""
    state = {"last_counterparty": case["last_counterparty"], "pending_tool": None}
    want_stop = case["expected_stop"]
    try:
        rep = agent.prefilter(case["query"], state)
        got_stop = rep["stop"]
        out = {"stop": got_stop, "tools": [], "clarified": False}
        if got_stop != want_stop:
            if want_stop is None:
                return False, FALSE_REFUSAL, f"stopped by {got_stop}", out
            if got_stop is None:
                return False, GUARD_MISSED, f"expected {want_stop}, passed all checks", out
            return False, GUARD_WRONG, f"expected {want_stop}, got {got_stop}", out
        if want_stop is not None:
            return True, None, "", out

        plan = plan_for(case["query"], state, planner)
        clarified = bool(plan.get("needs_clarification"))
        tools = sorted(s["tool"] for s in plan.get("steps", []))
        out.update(tools=tools, clarified=clarified)
        if clarified != case["needs_clarification"]:
            return False, CLAR_WRONG, f"clarified={clarified} (missing={plan.get('missing_arg')})", out
        if clarified:
            if plan.get("missing_arg") != case["expected_missing_arg"]:
                return False, CLAR_ARG, f"asked for {plan.get('missing_arg')}", out
            return True, None, "", out
        if tools != sorted(case["expected_tools"]):
            return False, TOOLS_WRONG, f"got {tools}", out
        steps = plan["steps"]
        for exp in case["expected_args"]:
            if not any(all(s["args"].get(k) == v for k, v in exp.items()) for s in steps):
                return False, ARGS_WRONG, f"want {exp}, got {[s['args'] for s in steps]}", out
        return True, None, "", out
    except Exception as exc:  # a crash is a failure, not an abort of the whole run
        return False, ERROR, f"{type(exc).__name__}: {exc}", {"stop": None, "tools": [], "clarified": False}


# ----------------------------------------------------------------------------- conversations
import os  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

TURN_OUTCOME = "turn_outcome"
TURN_STOP = "turn_stop"
TURN_ARG = "turn_missing_arg"
TURN_TOOLS = "turn_tools"
TURN_ARGS = "turn_args"
STATE_LAST = "state_last_counterparty"
STATE_PENDING = "state_pending"


def _fake_call(name, arguments=None):
    """Stands in for the MCP server: echoes the counterparty so app._remember behaves as in production."""
    out = {"tool": name}
    if (arguments or {}).get("counterparty"):
        out["counterparty_id"] = arguments["counterparty"]
    return out


def _outcome_of(payload):
    flags = payload["flags"]
    if "kill_switch" in flags:
        return {"outcome": "refuse", "stop": "kill"}
    for f in flags:
        if f.startswith("clarification:"):
            return {"outcome": "clarify", "missing_arg": f.split(":", 1)[1]}
        if f.startswith("refused:"):
            return {"outcome": "refuse", "stop": f.split(":", 1)[1]}
    return {"outcome": "answer", "tools": sorted(r["tool"] for r in payload["results"]),
            "args": [r["args"] for r in payload["results"]]}


def _check_turn(expect, state_after, actual, state):
    if actual["outcome"] != expect["outcome"]:
        return TURN_OUTCOME, f"expected {expect['outcome']}, got {actual['outcome']}" + (
            f" ({actual.get('stop') or actual.get('missing_arg') or actual.get('tools')})")
    if expect["outcome"] == "refuse" and actual["stop"] != expect["stop"]:
        return TURN_STOP, f"expected stop {expect['stop']}, got {actual['stop']}"
    if expect["outcome"] == "clarify" and actual["missing_arg"] != expect["missing_arg"]:
        return TURN_ARG, f"expected to ask for {expect['missing_arg']}, asked for {actual['missing_arg']}"
    if expect["outcome"] == "answer":
        if actual["tools"] != sorted(expect["tools"]):
            return TURN_TOOLS, f"expected {sorted(expect['tools'])}, got {actual['tools']}"
        for exp in expect.get("args", []):
            if not any(all(a.get(k) == v for k, v in exp.items()) for a in actual["args"]):
                return TURN_ARGS, f"want {exp}, got {actual['args']}"
    last = state.get("last_counterparty") or ""
    if last != state_after["last_counterparty"]:
        return STATE_LAST, f"memory is {last!r}, expected {state_after['last_counterparty']!r}"
    pending = (state.get("pending_tool") or {}).get("missing_arg")
    if pending != state_after["pending"]:
        return STATE_PENDING, f"pending clarification is {pending!r}, expected {state_after['pending']!r}"
    return None, ""


def evaluate_conversation(conv, planner="offline"):
    """Play a conversation through app.answer, with the calculation server mocked.

    State is kept in a temporary file, so the real session file is never touched.
    Returns {ok, turns: [{ok, kind, detail, user, state_ok}], first_failure}.
    A `new_session` turn simulates quitting and starting again: nothing is cleared
    by this harness, so a state file that survives a restart shows up as a failure.
    """
    import app
    import mcp_client

    turns = []
    with tempfile.TemporaryDirectory() as td, \
            mock.patch.object(agent, "STATE_PATH", Path(td) / "session_state.json"), \
            mock.patch.object(agent, "TRACE_PATH", Path(td) / "traces.jsonl"), \
            mock.patch.object(mcp_client, "reset", lambda: None), \
            mock.patch.object(mcp_client, "call_tool", _fake_call), \
            mock.patch.object(app, "_tool_lines", lambda row: []), \
            mock.patch.object(agent, "compose", lambda *a, **k: ("", "")):
        for turn in conv["turns"]:
            env = {"PFE_KILL_SWITCH": "0"}
            if planner == "offline":
                env["PFE_LLM_OFF"] = "1"
            env.update(turn.get("env") or {})
            try:
                with mock.patch.dict(os.environ, env):
                    payload = app.answer(turn["user"])
                kind, detail = _check_turn(turn["expect"], turn["state_after"], _outcome_of(payload), agent.load_state())
            except Exception as exc:
                kind, detail = ERROR, f"{type(exc).__name__}: {exc}"
            # state_ok: True/False when the memory was compared, None when the turn already failed earlier
            state_ok = True if kind is None else (False if kind in (STATE_LAST, STATE_PENDING) else None)
            turns.append({"user": turn["user"], "ok": kind is None, "kind": kind, "detail": detail, "state_ok": state_ok})
    failed = [i for i, t in enumerate(turns, 1) if not t["ok"]]
    return {"ok": not failed, "turns": turns, "first_failure": failed[0] if failed else None}
