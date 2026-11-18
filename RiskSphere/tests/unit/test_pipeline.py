"""Layer 3: end-to-end app.answer() with mocked LLM / MCP call."""

import json

import pytest

import agent
import app

MARGIN_RESULT = {
    "tool": "calculate_margin_call", "counterparty_id": "meridian", "action": "NO_ACTION",
    "delivery_amount_usd": 0, "return_amount_usd": 0, "threshold_usd": 1000000,
    "mta_usd": 250000, "rounding_usd": 10000, "net_mtm_usd": 2000000,
    "independent_amount_usd": 0, "credit_support_amount_usd": 1000000,
    "collateral_after_haircut_usd": 1000000, "raw_delivery_usd": 0,
}


def test_two_turn_clarification(monkeypatch, mcp):
    monkeypatch.setattr("mcp_client.call_tool", lambda name, arguments=None: MARGIN_RESULT)
    t1 = app.answer("Check margin call")
    assert "clarification:counterparty" in t1["flags"]
    assert agent.load_state().get("pending_tool") is not None

    t2 = app.answer("Meridian Capital")
    assert "clarification_followup" in t2["flags"]
    assert t2["results"][0]["ok"]
    assert agent.load_state().get("pending_tool") is None
    assert agent.load_state().get("last_counterparty") == "meridian"


def test_critic_fallback_drops_bad_wording(monkeypatch, mcp):
    plan_json = json.dumps({
        "intent": ["regulatory_haircut"], "refuse": False, "refusal_reason": "",
        "reason": "haircut table lookup",
        "steps": [{"tool": "regulatory_haircut",
                   "args": {"asset_class": "corporate", "maturity_bucket": ">5y", "rating_bucket": "A+-BBB-"}}],
    })
    bad = "The haircut is 99% and a margin call of $500,000 is due."
    monkeypatch.setenv("PFE_LLM_OFF", "0")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    seq = iter([plan_json, bad, bad])
    monkeypatch.setattr("agent.chat", lambda *a, **k: next(seq))
    res = app.answer("What haircut applies to corporate bonds rated A to BBB over 5 years?")
    assert "critic_fallback" in res["flags"]
    assert res["wording"] == ""
    assert res["results"][0]["output"]["haircut_pct"] == 12.0
