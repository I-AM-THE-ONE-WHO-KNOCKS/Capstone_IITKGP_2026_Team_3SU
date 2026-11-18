"""Layer 1: deterministic financial formulas and PFE engine invariants."""

import pytest

import books
import pfe
import tools


@pytest.fixture(autouse=True)
def _reset_tools():
    tools.reset()


@pytest.mark.parametrize("args, expected", [
    (("cash",), 0.0),
    (("corporate", ">5y", "A+-BBB-"), 12.0),
    (("government", "1-5y", "AAA-AA-"), 2.0),
    (("equity_main_index",), 15.0),
])
def test_regulatory_haircut_schedule(args, expected):
    assert books.haircut_pct(*args) == expected


def test_unknown_asset_class_rejected():
    with pytest.raises(ValueError):
        books.haircut_pct("crypto")


def test_harbor_breach(mock_market):
    harbor = tools.current_exposure("harbor")
    assert harbor["status"] == "BREACH"
    assert harbor["current_exposure_usd"] == 1034960
    assert harbor["headroom_usd"] == -34960


def test_meridian_margin_terms(mock_market):
    m = tools.calculate_margin_call("meridian")
    assert m["threshold_usd"] == 1000000
    assert m["mta_usd"] == 250000
    assert m["action"] in ("CALL_COUNTERPARTY", "RETURN_COLLATERAL", "NO_ACTION")


def test_pfe_engine_self_checks(cfg):
    ok, lines = pfe.validate(cfg)
    assert ok, f"PFE engine validation failed: {lines}"
