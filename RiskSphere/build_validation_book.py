"""Write the six-name book and set each limit from the simulated peak.

Entry prices are Yahoo closes on the trade date. Posted collateral is
sized from the mark ten business days earlier, the same idea as a book
that was last margined then. After the trades are written, we simulate
each name and set the limit to the collateralised peak times that name's
factor, rounded to the nearest hundred thousand.

This is the desk book, written into data/. Needs Yahoo. If a download
fails, the script stops and does not write a partial book. The chat model
is not called.
"""

import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path

import market

OUT = Path(__file__).resolve().parent / "data"
LAG = 10

# id, name, book, threshold, mta, rounding, independent, limit factor,
# collateral (asset in our book, haircut percent), trades
# trade: symbol, direction, notional, days since trade, days to maturity
SPECS = [
    ("CP-001", "Apex Capital Derivatives", "Equity and FX", 0, 250000, 10000, 0, 1.50,
     ("cash", 0.0),
     [("SPY", 1, 25_000_000, 240, 300), ("EURUSD=X", -1, 15_000_000, 170, 380)]),
    ("CP-002", "Meridian Global Trading", "Equity and FX", 1_000_000, 500_000, 50_000, 0, 0.85,
     ("corporate_bond", 12.0),
     [("QQQ", 1, 30_000_000, 290, 420), ("USDJPY=X", 1, 20_000_000, 150, 400)]),
    ("CP-003", "Vanguard Risk Arbitrage", "Rates and gold", 0, 100_000, 10_000, 0, 1.10,
     ("government_bond", 2.0),
     [("TLT", -1, 20_000_000, 270, 330), ("GLD", 1, 10_000_000, 200, 360)]),
    ("CP-004", "Zenith Hedge Fund LLC", "Equity", 500_000, 250_000, 25_000, 1_000_000, 0.70,
     ("equity", 15.0),
     [("EEM", 1, 40_000_000, 220, 480), ("SPY", -1, 10_000_000, 120, 200)]),
    ("CP-005", "Kaveri Commodities Pte", "Gold and FX", 2_000_000, 500_000, 100_000, 0, 1.40,
     ("cash", 0.0),
     [("GLD", 1, 35_000_000, 250, 350), ("USDINR=X", 1, 25_000_000, 160, 390)]),
    ("CP-006", "Marina Sovereign Fund", "Equity and FX", 0, 100_000, 10_000, 0, 1.15,
     ("government_bond", 2.0),
     [("QQQ", -1, 15_000_000, 140, 300), ("EURUSD=X", 1, 30_000_000, 90, 450)]),
]

CREDIT = {
    "CP-001": (0.010, 0.45),
    "CP-002": (0.012, 0.40),
    "CP-003": (0.008, 0.35),
    "CP-004": (0.020, 0.55),
    "CP-005": (0.015, 0.50),
    "CP-006": (0.006, 0.30),
}


def _day(offset):
    """A calendar date offset days from today, as YYYY-MM-DD."""
    return (date.today() + timedelta(days=offset)).isoformat()


def _close_on_or_before(rows, day):
    """Last close whose date is on or before day. rows are oldest first."""
    chosen = None
    for stamp, close in rows:
        if stamp <= day:
            chosen = close
        else:
            break
    if chosen is None:
        raise RuntimeError(f"no close on or before {day}")
    return chosen


def _write(name, header, rows):
    path = OUT / name
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"wrote {path}")


def main():
    """Download the symbols once, then write the six tables."""
    symbols = sorted({trade[0] for spec in SPECS for trade in spec[9]})
    history = {symbol: market.history(symbol) for symbol in symbols}
    OUT.mkdir(parents=True, exist_ok=True)

    names, trades, collateral, terms, limits, credit = [], [], [], [], [], []
    for spec in SPECS:
        cid, name, book, threshold, mta, rounding, independent, factor, coll, legs = spec
        names.append((cid, name, book))
        terms.append((cid, threshold, mta, rounding, independent, "USD"))
        pd, lgd = CREDIT[cid]
        credit.append((cid, pd, lgd, "1y", "synthetic"))
        lagged = 0.0
        gross_notional = 0.0
        for symbol, direction, notional, since, life in legs:
            rows = history[symbol]
            entry_day = _day(-since)
            entry = _close_on_or_before(rows, entry_day)
            quantity = direction * notional / entry
            trades.append((
                f"{cid}-{symbol}", cid, symbol, f"{quantity:.6f}", f"{entry:.6f}", "USD", _day(life),
            ))
            lag_day = rows[max(0, len(rows) - 1 - LAG)][0]
            lagged_px = _close_on_or_before(rows, lag_day)
            lagged += quantity * (lagged_px - entry)
            gross_notional += abs(notional)
        requirement = max(0.0, lagged + independent - threshold) * 1.05
        haircut = coll[1]
        face = requirement / (1 - haircut / 100) if requirement else 0.0
        collateral.append((cid, coll[0], int(round(face)), "USD"))
        limits.append((cid, 1_000_000))

    _write("counterparties.csv", ["counterparty_id", "name", "book"], names)
    _write("trades.csv", ["trade_id", "counterparty_id", "symbol", "quantity", "entry_price", "currency", "maturity"], trades)
    _write("collateral.csv", ["counterparty_id", "asset", "amount", "currency"], collateral)
    _write("csa_terms.csv", ["counterparty_id", "threshold", "mta", "rounding", "independent_amount", "base_currency"], terms)
    _write("limits.csv", ["counterparty_id", "pfe_limit"], limits)
    _write("credit.csv", ["counterparty_id", "pd", "lgd", "pd_horizon", "source"], credit)
    print(f"names {len(names)}")
    _set_limits_from_peak()


def _set_limits_from_peak():
    """Replace the placeholder limit with the simulated collateralised peak times the factor."""
    os.environ["PFE_LLM_OFF"] = "1"
    import agent
    import books
    import pfe

    cfg = agent.load_config()
    factors = {spec[0]: spec[7] for spec in SPECS}
    book = books.load_book(OUT)
    rows = []
    basis = []
    for party in book:
        result = pfe.check_limit(party, cfg)
        peak = int(result["peak_pfe_collateralized_usd"])
        factor = factors[party["counterparty_id"]]
        limit = int(round(max(peak, 1) * factor, -5))
        if limit < 100_000:
            limit = 100_000
        rows.append((party["counterparty_id"], limit))
        basis.append({
            "counterparty_id": party["counterparty_id"],
            "name": party["name"],
            "peak_pfe_collateralized_usd": peak,
            "factor": factor,
            "credit_limit_usd": limit,
        })
        print(f"limit {party['name']} peak {peak} factor {factor} limit {limit}")
    _write("limits.csv", ["counterparty_id", "pfe_limit"], rows)
    (OUT / "limit_basis.json").write_text(json.dumps(basis, indent=2))


if __name__ == "__main__":
    main()
