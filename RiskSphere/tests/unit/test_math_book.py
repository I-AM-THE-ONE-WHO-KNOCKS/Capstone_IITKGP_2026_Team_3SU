"""Layer 1b: exposure / margin arithmetic against hand values, boundaries and the oracle.

Synthetic books are written to a temp data dir, so these tests do not depend on
the sample CSVs or on Yahoo.
"""

import csv
import random

import pytest

import books
import oracle
import tools


def write_book(tmp_path, monkeypatch, *, trades, collateral, terms, limit, price):
    """One counterparty 'x'. trades: [(qty, entry)], all on symbol SYM at `price`."""
    def w(name, header, rows):
        with (tmp_path / name).open("w", newline="") as f:
            csv.writer(f).writerows([header] + rows)

    w("counterparties.csv", ["counterparty_id", "name", "book"], [["x", "Xeno Capital", "test"]])
    w("limits.csv", ["counterparty_id", "pfe_limit"], [["x", limit]])
    w("csa_terms.csv", ["counterparty_id", "threshold", "mta", "rounding", "independent_amount", "base_currency"],
      [["x", terms["threshold"], terms["mta"], terms["rounding"], terms["independent"], "USD"]])
    w("trades.csv", ["trade_id", "counterparty_id", "symbol", "quantity", "entry_price", "currency", "maturity"],
      [[f"T{i}", "x", "SYM", q, e, "USD", "2027-06-18"] for i, (q, e) in enumerate(trades)])
    w("collateral.csv", ["counterparty_id", "asset", "amount", "currency"],
      [["x", a, amt, "USD"] for a, amt in collateral])
    monkeypatch.setattr(books, "DATA", tmp_path)
    monkeypatch.setattr("market.fetch_quotes", lambda symbols=None: {
        "SYM": {"price": price, "asof": "2026-11-13", "currency": "USD", "csv": "x.csv"}})
    tools.reset()
    return books.by_id(books.load_book())["x"]


TERMS = dict(threshold=0, mta=50_000, rounding=10_000, independent=0)


# --- Golden values for the sample book, computed by hand -------------------------------

def test_sample_book_golden_values(mock_market):
    """SPY 754.37, EURUSD 1.08, SHY 82, LQD 108 (conftest quotes). Hand arithmetic:
    Meridian 40000*(754.37-700) + -5e6*(1.08-1.18) = 2,174,800 + 500,000 = 2,674,800."""
    tools.reset()
    book = books.by_id(books.load_book())
    m, a, h = book["meridian"], book["apex"], book["harbor"]

    assert m["gross_mtm"] == 2_674_800
    assert m["collateral_value"] == 1_000_000
    assert m["credit_exposure"] == 1_674_800
    assert m["status"] == "OK"                      # 33.5 % of 5,000,000
    assert m["raw_delivery"] == 674_800             # (2,674,800 - 1,000,000) - 1,000,000
    assert m["delivery_amount"] == 680_000          # rounded UP to 10,000
    assert m["action"] == "CALL_COUNTERPARTY"

    assert a["gross_mtm"] == 1_840_000              # 80000*18 + 100000*4
    assert a["collateral_value"] == 352_000         # 400,000 * (1 - 12 %)
    assert a["credit_exposure"] == 1_488_000
    assert a["status"] == "OK"                      # 74.4 % of 2,000,000
    assert a["delivery_amount"] == 990_000          # (1,840,000-500,000)-352,000 = 988,000 -> 990,000

    assert h["gross_mtm"] == 1_234_960
    assert h["credit_exposure"] == 1_034_960
    assert h["status"] == "BREACH"
    assert h["headroom"] == -34_960
    assert h["delivery_amount"] == 1_040_000        # raw 1,034,960 -> 1,040,000


# --- Status boundaries (README step 4) --------------------------------------------------

@pytest.mark.parametrize("exposure, expected", [
    (849_999, "OK"),
    (850_000, "WARNING"),        # exactly 85 % is WARNING
    (999_999, "WARNING"),
    (1_000_000, "WARNING"),      # exactly 100 % is NOT a breach
    (1_000_001, "BREACH"),       # 100.0001 % displays as 100.0 but is a breach
])
def test_status_boundaries(tmp_path, monkeypatch, exposure, expected):
    p = write_book(tmp_path, monkeypatch, trades=[(1, 0)], collateral=[("cash", 0)],
                   terms=TERMS, limit=1_000_000, price=exposure)
    assert p["credit_exposure"] == exposure
    assert p["status"] == expected
    assert oracle.status(exposure, 1_000_000) == expected


def test_exposure_floored_at_zero(tmp_path, monkeypatch):
    p = write_book(tmp_path, monkeypatch, trades=[(1, 0)], collateral=[("cash", 5_000_000)],
                   terms=TERMS, limit=1_000_000, price=100)
    assert p["credit_exposure"] == 0 and p["status"] == "OK"


def test_negative_mark_counterparty_owes_nothing(tmp_path, monkeypatch):
    p = write_book(tmp_path, monkeypatch, trades=[(1, 500_000)], collateral=[("cash", 0)],
                   terms=TERMS, limit=1_000_000, price=0)
    assert p["gross_mtm"] == -500_000
    assert p["credit_exposure"] == 0


# --- Collateral haircut (README step 3) --------------------------------------------------

@pytest.mark.parametrize("asset, amount, counted", [
    ("cash", 1_000_000, 1_000_000),
    ("corporate_bond", 400_000, 352_000),
    ("government_bond", 1_000_000, 980_000),
    ("equity", 1_000_000, 850_000),
])
def test_collateral_counts_after_haircut(tmp_path, monkeypatch, asset, amount, counted):
    p = write_book(tmp_path, monkeypatch, trades=[(1, 0)], collateral=[(asset, amount)],
                   terms=TERMS, limit=10_000_000, price=2_000_000)
    assert p["collateral_value"] == counted


def test_unmapped_collateral_asset_is_an_error(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        write_book(tmp_path, monkeypatch, trades=[(1, 0)], collateral=[("bitcoin", 1)],
                   terms=TERMS, limit=1, price=1)


# --- Margin call (README steps 6-8) -------------------------------------------------------
# threshold 0, no collateral => raw delivery equals the mark.

@pytest.mark.parametrize("mark, action, delivery", [
    (49_999, "NO_ACTION", 0),         # raw just under MTA
    (50_000, "CALL_COUNTERPARTY", 50_000),   # raw == MTA is enough
    (50_001, "CALL_COUNTERPARTY", 60_000),   # rounded UP to the step
    (60_000, "CALL_COUNTERPARTY", 60_000),   # already on the step: not bumped
    (0, "NO_ACTION", 0),
])
def test_delivery_rounding_and_mta(tmp_path, monkeypatch, mark, action, delivery):
    p = write_book(tmp_path, monkeypatch, trades=[(1, 0)], collateral=[("cash", 0)],
                   terms=TERMS, limit=10_000_000, price=mark)
    assert (p["action"], p["delivery_amount"], p["return_amount"]) == (action, delivery, 0)


@pytest.mark.parametrize("posted, action, returned", [
    (49_999, "NO_ACTION", 0),            # over-collateralised by less than MTA
    (50_000, "RETURN_COLLATERAL", 50_000),
    (59_999, "RETURN_COLLATERAL", 50_000),   # rounded DOWN
    (60_000, "RETURN_COLLATERAL", 60_000),
])
def test_return_rounding_and_mta(tmp_path, monkeypatch, posted, action, returned):
    p = write_book(tmp_path, monkeypatch, trades=[(1, 0)], collateral=[("cash", posted)],
                   terms=TERMS, limit=10_000_000, price=0)
    assert (p["action"], p["return_amount"], p["delivery_amount"]) == (action, returned, 0)
    assert p["requires_human_approval"] == (action != "NO_ACTION")


def test_threshold_and_independent_amount(tmp_path, monkeypatch):
    t = dict(threshold=1_000_000, mta=50_000, rounding=10_000, independent=200_000)
    p = write_book(tmp_path, monkeypatch, trades=[(1, 0)], collateral=[("cash", 0)],
                   terms=t, limit=10_000_000, price=1_500_000)
    assert p["credit_support_amount"] == 700_000     # 1.5m + 0.2m - 1.0m
    assert p["delivery_amount"] == 700_000


def test_below_threshold_means_no_credit_support(tmp_path, monkeypatch):
    t = dict(threshold=2_000_000, mta=50_000, rounding=10_000, independent=0)
    p = write_book(tmp_path, monkeypatch, trades=[(1, 0)], collateral=[("cash", 0)],
                   terms=t, limit=10_000_000, price=1_000_000)
    assert p["credit_support_amount"] == 0
    assert p["action"] == "NO_ACTION"


# --- Differential test against the independent oracle ---------------------------------------

def _random_case(rng):
    terms = dict(threshold=rng.choice([0, 100_000, 500_000, 1_000_000]),
                 mta=rng.choice([10_000, 50_000, 250_000]),
                 rounding=rng.choice([1_000, 10_000, 25_000]),
                 independent=rng.choice([0, 0, 100_000]))
    trades = [(rng.choice([-1, 1]) * rng.randint(1, 50) * 1000, rng.randint(50, 150)) for _ in range(rng.randint(1, 3))]
    collateral = [(rng.choice(list(oracle.POSTED)), rng.randint(0, 40) * 25_000) for _ in range(rng.randint(0, 3))]
    return trades, collateral, terms, rng.choice([500_000, 1_000_000, 5_000_000]), rng.randint(40, 200)


@pytest.mark.parametrize("seed", range(60))
def test_code_matches_oracle_on_random_books(tmp_path, monkeypatch, seed):
    trades, collateral, terms, limit, price = _random_case(random.Random(seed))
    p = write_book(tmp_path, monkeypatch, trades=trades, collateral=collateral, terms=terms, limit=limit, price=price)
    expected = oracle.party([(q, e, price) for q, e in trades], collateral, terms, limit)
    actual = {k: p[k] for k in expected}
    assert actual == expected

    # invariants that must hold for any book
    assert p["credit_exposure"] >= 0
    assert not (p["delivery_amount"] and p["return_amount"])
    if p["delivery_amount"]:
        assert p["delivery_amount"] % terms["rounding"] == 0 and p["delivery_amount"] >= p["raw_delivery"]
    if p["return_amount"]:
        assert p["return_amount"] % terms["rounding"] == 0 and p["return_amount"] <= -p["raw_delivery"]


@pytest.mark.parametrize("seed", range(20))
def test_more_collateral_never_raises_exposure(tmp_path, monkeypatch, seed):
    trades, collateral, terms, limit, price = _random_case(random.Random(1000 + seed))
    base = write_book(tmp_path, monkeypatch, trades=trades, collateral=collateral, terms=terms, limit=limit, price=price)
    more = write_book(tmp_path, monkeypatch, trades=trades, collateral=collateral + [("cash", 100_000)],
                      terms=terms, limit=limit, price=price)
    assert more["credit_exposure"] <= base["credit_exposure"]
