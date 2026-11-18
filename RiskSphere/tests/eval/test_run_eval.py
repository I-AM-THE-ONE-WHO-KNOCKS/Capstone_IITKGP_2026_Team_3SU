"""The scoring harness must itself be correct."""

import pytest

import run_eval


def test_wilson_interval_known_values():
    lo, hi = run_eval.wilson(8, 10)
    assert lo == pytest.approx(0.490, abs=0.005) and hi == pytest.approx(0.943, abs=0.005)
    assert run_eval.wilson(0, 0) == (0.0, 0.0)
    assert run_eval.wilson(10, 10)[1] == 1.0


def _row(expected_stop, got_stop, ok, clar_exp=False, clar_got=False, tools=("current_exposure",)):
    return {"ok": ok, "kind": None if ok else "x", "detail": "", "ms": 1.0,
            "case": {"expected_stop": expected_stop, "needs_clarification": clar_exp, "expected_tools": list(tools),
                     "index_overlap": False, "category": "c", "source": "s", "requirement_ids": ["R01"], "id": "i", "query": "q"},
            "out": {"stop": got_stop, "clarified": clar_got, "tools": [] if clar_got or got_stop else list(tools)}}


def test_metrics_guardrail_precision_recall_and_false_refusal():
    rows = [
        _row("action", "action", True),       # TP
        _row("action", None, False),          # FN
        _row(None, "action", False),          # FP + false refusal
        _row(None, None, True),
    ]
    m = run_eval.metrics(rows)
    act = m["guardrail_per_check"]["action"]
    assert (act["recall"]["k"], act["recall"]["n"]) == (1, 2)
    assert (act["precision"]["k"], act["precision"]["n"]) == (1, 2)
    assert (m["false_refusal_rate"]["k"], m["false_refusal_rate"]["n"]) == (1, 2)
    assert m["unsafe_blocked_by_any_check"]["k"] == 1


def test_metrics_clarification():
    rows = [
        _row(None, None, True, clar_exp=True, clar_got=True),    # TP
        _row(None, None, False, clar_exp=False, clar_got=True),  # FP
        _row(None, None, False, clar_exp=True, clar_got=False),  # FN
    ]
    m = run_eval.metrics(rows)
    assert (m["clarification_precision"]["k"], m["clarification_precision"]["n"]) == (1, 2)
    assert (m["clarification_recall"]["k"], m["clarification_recall"]["n"]) == (1, 2)


def test_full_offline_report_builds_and_renders():
    report = run_eval.build("offline", ["dev"], False)
    assert report["n_scored"] > 0 and report["metrics"]["end_to_end"]["n"] == report["n_scored"]
    assert "## Headline" in run_eval.markdown(report)


def test_conversation_report_builds_and_scores():
    rep = run_eval.build_conversations("offline", ["dev"])
    assert rep["n_conversations"] > 0 and rep["conversation_pass"]["n"] == rep["n_conversations"]
    assert rep["turn_pass"]["n"] == rep["n_turns"]
    # a conversation can only pass if all its turns pass
    assert rep["conversation_pass"]["k"] <= rep["n_conversations"]
    assert "## Headline" in run_eval.markdown_conversations(rep)
