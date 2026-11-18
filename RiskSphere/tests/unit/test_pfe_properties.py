"""Layer 1c: PFE engine properties on synthetic prices (no Yahoo, small path counts)."""

import math
import statistics

import pytest

import books
import oracle
import pfe

CLOSES = pfe._synthetic_closes(780, 0.012, 5)
SPOT, STRIKE = CLOSES[-1], CLOSES[-100]
NOTIONAL = 10_000_000.0
QTY = NOTIONAL / STRIKE
Q = 0.95
PATHS = 1500


def trade(direction=1, life=126, qty=QTY):
    return {"symbol": "VAL", "quantity": direction * qty, "entry": STRIKE, "steps": life, "closes": CLOSES}


def sim(trades, threshold=0, mta=0, ia=0, mpor=10, seed=7, paths=PATHS, steps=126):
    return pfe.simulate(trades, threshold, mta, ia, mpor, paths, seed, steps, Q)


def test_same_seed_is_deterministic():
    a, b = sim([trade()]), sim([trade()])
    assert a["peak_collateralized"] == b["peak_collateralized"]
    assert a["peak_uncollateralized"] == b["peak_uncollateralized"]
    assert a["fan"] == b["fan"]


def test_different_seed_changes_paths_but_stays_close():
    a, b = sim([trade()], seed=7), sim([trade()], seed=8)
    assert a["peak_uncollateralized"] != b["peak_uncollateralized"]
    assert b["peak_uncollateralized"] == pytest.approx(a["peak_uncollateralized"], rel=0.12)


def test_today_mark_matches_notional_formula():
    r = sim([trade()])
    assert r["current_mtm"] == pytest.approx(NOTIONAL * (SPOT / STRIKE - 1), rel=1e-6)


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("k", [21, 63, 126])
def test_uncollateralised_peak_not_below_closed_form_at_step_k(direction, k):
    """The reported peak is the max over steps, so it is >= the closed form at any step."""
    rets = [math.log(CLOSES[i] / CLOSES[i - 1]) for i in range(1, len(CLOSES))][-504:]
    sd = statistics.pstdev(rets) * math.sqrt(len(rets) / (len(rets) - 1))
    z = statistics.NormalDist().inv_cdf(Q)
    expected = oracle.lognormal_pfe(NOTIONAL, SPOT, STRIKE, sd, k, direction, z)
    r = sim([trade(direction)], paths=4000)
    assert r["peak_uncollateralized"] >= expected * 0.92


def test_collateral_never_raises_the_peak():
    r = sim([trade()])
    assert r["peak_collateralized"] <= r["peak_uncollateralized"] + 1e-6


def test_huge_threshold_turns_collateral_off():
    r = sim([trade()], threshold=1e15)
    assert r["peak_collateralized"] == pytest.approx(r["peak_uncollateralized"], rel=1e-9)


def test_offsetting_trades_net_to_near_zero():
    r = sim([trade(1), trade(-1)])
    assert r["peak_uncollateralized"] < 1e-6 and r["current_mtm"] == pytest.approx(0, abs=1e-6)


def test_shorter_margin_period_lowers_collateralised_peak():
    slow, fast = sim([trade()], mpor=10), sim([trade()], mpor=1)
    assert fast["peak_collateralized"] <= slow["peak_collateralized"]
    assert fast["peak_collateralized"] < 0.35 * fast["peak_uncollateralized"]


def test_higher_threshold_never_lowers_the_collateralised_peak():
    low, high = sim([trade()], threshold=0), sim([trade()], threshold=2_000_000)
    assert high["peak_collateralized"] >= low["peak_collateralized"] - 1e-6


def test_check_limit_status_uses_unrounded_ratio(monkeypatch, cfg):
    monkeypatch.setattr(pfe, "validate", lambda c: (True, ["stub"]))
    monkeypatch.setattr(pfe, "simulate", lambda *a, **k: {
        "peak_collateralized": 1_000_000.4, "peak_uncollateralized": 2e6, "current_mtm": 0,
        "peak_step": 5, "horizon_steps": 10, "fan": []})
    party = {"counterparty_id": "x", "name": "X", "credit_limit": 1_000_000, "threshold": 0, "mta": 0,
             "independent_amount": 0, "trades": [{"symbol": "S", "quantity": 1, "entry_price": 1, "asof": "2026-11-13", "maturity": "2027-06-18"}]}
    # 1,000,000 rounds to exactly 100 % -> WARNING, not BREACH
    assert pfe.check_limit(party, cfg)["status"] == "WARNING"
    monkeypatch.setattr(pfe, "simulate", lambda *a, **k: {
        "peak_collateralized": 1_000_001, "peak_uncollateralized": 2e6, "current_mtm": 0,
        "peak_step": 5, "horizon_steps": 10, "fan": []})
    assert pfe.check_limit(party, cfg)["status"] == "BREACH"


def test_check_limit_refuses_when_engine_checks_fail(monkeypatch, cfg):
    monkeypatch.setattr(pfe, "validate", lambda c: (False, ["closed form failed"]))
    with pytest.raises(RuntimeError, match="PFE engine checks failed"):
        pfe.check_limit({"trades": []}, cfg)


# --- haircut table properties -----------------------------------------------------------

@pytest.mark.parametrize("cls", ["government", "corporate"])
@pytest.mark.parametrize("rating", books.RATINGS)
def test_haircut_increases_with_maturity(cls, rating):
    vals = [books.haircut_pct(cls, m, rating) for m in books.MATURITIES]
    assert vals == sorted(vals) and len(set(vals)) == 3


@pytest.mark.parametrize("cls", ["government", "corporate"])
@pytest.mark.parametrize("mat", books.MATURITIES)
def test_lower_rating_never_has_smaller_haircut(cls, mat):
    assert books.haircut_pct(cls, mat, "A+-BBB-") >= books.haircut_pct(cls, mat, "AAA-AA-")


def test_corporate_haircut_not_below_government():
    for mat in books.MATURITIES:
        for rat in books.RATINGS:
            assert books.haircut_pct("corporate", mat, rat) >= books.haircut_pct("government", mat, rat)


def test_haircuts_between_0_and_100_and_cash_is_zero():
    for cls in books.ASSET_CLASSES:
        for mat in books.MATURITIES:
            for rat in books.RATINGS:
                assert 0 <= books.haircut_pct(cls, mat, rat) < 100
    assert books.haircut_pct("cash", ">5y", "A+-BBB-") == 0


def test_haircut_matches_oracle_table():
    for (cls, mat, rat), pct in oracle.HAIRCUT.items():
        assert books.haircut_pct(cls, mat or "1-5y", rat or "AAA-AA-") == float(pct)
