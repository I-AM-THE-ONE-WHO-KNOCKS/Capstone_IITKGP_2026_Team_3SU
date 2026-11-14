"""Sample book and the arithmetic for today's exposure and variation margin.

Nothing here calls a model. A positive market value means we are in the
money: the counterparty owes us on that trade.

Credit support is max(0, mark + independent amount - threshold). Collateral
is subtracted after the haircut. A delivery is rounded up. A return is
rounded down. The limit test in this module is today's exposure. Potential
future exposure is a separate simulation.
"""

import csv
import json
import math
from pathlib import Path

import market

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

# BCBS-IOSCO d499 Appendix B, in percent. Spot-check against the PDF is stage 3.
HAIRCUTS = {
    "cash": 0.0,
    "government": {
        "AAA-AA-": {"<=1y": 0.5, "1-5y": 2.0, ">5y": 4.0},
        "A+-BBB-": {"<=1y": 1.0, "1-5y": 3.0, ">5y": 6.0},
    },
    "corporate": {
        "AAA-AA-": {"<=1y": 1.0, "1-5y": 4.0, ">5y": 8.0},
        "A+-BBB-": {"<=1y": 2.0, "1-5y": 6.0, ">5y": 12.0},
    },
    "equity_main_index": 15.0,
    "equity_other": 25.0,
    "gold": 15.0,
}
ASSET_CLASSES = list(HAIRCUTS)
MATURITIES = ["<=1y", "1-5y", ">5y"]
RATINGS = ["AAA-AA-", "A+-BBB-"]

# How a row in collateral.csv maps onto that table.
POSTED = {
    "cash": ("cash", "1-5y", "AAA-AA-"),
    "government_bond": ("government", "1-5y", "AAA-AA-"),
    "corporate_bond": ("corporate", ">5y", "A+-BBB-"),
    "equity": ("equity_main_index", "1-5y", "AAA-AA-"),
}


def haircut_pct(asset_class, maturity_bucket="1-5y", rating_bucket="AAA-AA-"):
    """Look up one cell of the standardised haircut schedule, in percent.

    asset_class is cash, government, corporate, equity_main_index,
    equity_other, or gold. Bonds use maturity_bucket (<=1y, 1-5y, >5y)
    and rating_bucket (AAA-AA- or A+-BBB-). An unknown maturity or rating
    falls back to 1-5y and AAA-AA-. Cash, equity, and gold ignore those
    two arguments. Raises ValueError for an unknown asset class.
    Returns a float, for example 12.0.
    """
    if asset_class not in HAIRCUTS:
        raise ValueError(f"asset_class must be one of {ASSET_CLASSES}")
    maturity = maturity_bucket if maturity_bucket in MATURITIES else "1-5y"
    rating = rating_bucket if rating_bucket in RATINGS else "AAA-AA-"
    table = HAIRCUTS[asset_class]
    if isinstance(table, dict):
        return float(table[rating][maturity])
    return float(table)


def _read_csv(name):
    """Read one book table as a list of dicts. name is the file under data/."""
    with (DATA / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def _money(value):
    """Round a stored amount to the nearest dollar, as an int."""
    return int(round(float(value)))


def _round_up(amount, step):
    """Round a delivery up to the agreement's rounding step.

    A tiny epsilon keeps a value that is already on the step from bumping
    up. step <= 0 returns the amount as an int with no rounding.
    """
    if step <= 0:
        return int(amount)
    return int(math.ceil(amount / step - 1e-9)) * step


def _round_down(amount, step):
    """Round a return down to the agreement's rounding step.

    Same epsilon idea as _round_up, in the other direction, so an exact
    multiple is not reduced. step <= 0 returns the amount as an int.
    """
    if step <= 0:
        return int(amount)
    return int(math.floor(amount / step + 1e-9)) * step


def _status(utilization):
    """BREACH above 100, WARNING from 85 inclusive, otherwise OK.

    utilization is the unrounded percent. Rounding before this test would
    hide a small breach that displays as 100.0.
    """
    if utilization > 100:
        return "BREACH"
    if utilization >= 85:
        return "WARNING"
    return "OK"


def load_book():
    """Mark every counterparty and return one dict per name.

    Reads the sample names, limits, agreement terms, trades, and posted
    collateral, then downloads a Yahoo quote for each symbol. Mark is
    quantity times (last close minus entry). Collateral value is face
    times (1 minus the haircut), using the POSTED map onto the schedule.
    Today's exposure is max(gross mark - collateral value, 0). The margin
    action is CALL_COUNTERPARTY, RETURN_COLLATERAL, or NO_ACTION.

    Raises if Yahoo fails or a posted asset has no haircut mapping.
    Does not call the chat model. Does not compute future exposure.
    """
    names = {row["counterparty_id"]: row for row in _read_csv("counterparties.csv")}
    limits = {row["counterparty_id"]: _money(row["pfe_limit"]) for row in _read_csv("limits.csv")}
    terms = {row["counterparty_id"]: row for row in _read_csv("csa_terms.csv")}
    trades = _read_csv("trades.csv")
    collateral = _read_csv("collateral.csv")
    quotes = market.fetch_quotes(sorted({row["symbol"] for row in trades}))

    book = []
    for cid, party in names.items():
        marked = []
        for row in trades:
            if row["counterparty_id"] != cid:
                continue
            quote = quotes[row["symbol"]]
            qty = float(row["quantity"])
            entry = float(row["entry_price"])
            mtm = int(round(qty * (quote["price"] - entry)))
            marked.append({
                "trade_id": row["trade_id"],
                "symbol": row["symbol"],
                "quantity": qty,
                "entry_price": entry,
                "price": quote["price"],
                "asof": quote["asof"],
                "csv": quote["csv"],
                "maturity": row["maturity"],
                "market_value": mtm,
            })
        gross = sum(row["market_value"] for row in marked)
        term = terms[cid]
        threshold = _money(term["threshold"])
        mta = _money(term["mta"])
        rounding = _money(term["rounding"])
        independent = _money(term.get("independent_amount") or 0)

        collateral_lines = []
        collateral_value = 0
        for row in collateral:
            if row["counterparty_id"] != cid:
                continue
            mapped = POSTED.get(row["asset"])
            if mapped is None:
                raise ValueError(f"no haircut mapping for {row['asset']}")
            asset_class, maturity, rating = mapped
            pct = haircut_pct(asset_class, maturity, rating)
            amount = _money(row["amount"])
            counted = int(round(amount * (1 - pct / 100)))
            collateral_value += counted
            collateral_lines.append({
                "asset": row["asset"],
                "asset_class": asset_class,
                "maturity_bucket": maturity,
                "rating_bucket": rating,
                "amount": amount,
                "haircut_pct": pct,
                "counted": counted,
            })

        credit_support = max(0, gross + independent - threshold)
        raw = credit_support - collateral_value
        delivery = 0
        returned = 0
        if raw > 0 and raw >= mta:
            delivery = _round_up(raw, rounding)
            action = "CALL_COUNTERPARTY"
        elif raw < 0 and -raw >= mta:
            returned = _round_down(-raw, rounding)
            action = "RETURN_COLLATERAL"
        else:
            action = "NO_ACTION"
        exposure = max(gross - collateral_value, 0)
        limit = limits[cid]
        raw_utilization = (100.0 * exposure / limit) if limit else 0.0
        utilization = round(raw_utilization, 1)
        call_on_us = max(0, -gross)
        if call_on_us < mta:
            call_on_us = 0
        else:
            call_on_us = _round_up(call_on_us, rounding)

        book.append({
            "counterparty_id": cid,
            "name": party["name"],
            "book": party["book"],
            "gross_mtm": gross,
            "collateral_value": collateral_value,
            "collateral_lines": collateral_lines,
            "independent_amount": independent,
            "threshold": threshold,
            "mta": mta,
            "rounding": rounding,
            "credit_support_amount": credit_support,
            "raw_delivery": raw,
            "delivery_amount": delivery,
            "return_amount": returned,
            "action": action,
            "requires_human_approval": action != "NO_ACTION",
            "counterparty_may_call_us": call_on_us,
            "credit_exposure": exposure,
            "credit_limit": limit,
            "utilization_pct": utilization,
            "status": _status(raw_utilization),
            "headroom": limit - exposure,
            "measure": "current_exposure_not_pfe",
            "trades": marked,
        })
    return book


def by_id(book):
    """Index a load_book() result by counterparty id."""
    return {row["counterparty_id"]: row for row in book}


def names_only():
    """Names and ids for the planner. Does not call Yahoo or mark the book."""
    return _read_csv("counterparties.csv")


def find_party(book, text):
    """Match a counterparty mentioned in text. Returns an id, or ''.

    book may be the marked book or the names-only rows. A match is the id,
    the full name, or the first word of the name, compared in lower case.
    "Harbor Lane" and "harbor" both return the Harbor id.
    """
    lowered = str(text or "").lower()
    rows = book if book and "name" in book[0] else [
        {"counterparty_id": row["counterparty_id"], "name": row["name"]} for row in names_only()
    ]
    for row in rows:
        if row["counterparty_id"] in lowered or row["name"].lower() in lowered:
            return row["counterparty_id"]
        first = row["name"].split()[0].lower()
        if first and first in lowered:
            return row["counterparty_id"]
    return ""


def clause_rows():
    """Sample agreement sentences for the ranker. Not a filing, and not priced."""
    return json.loads((DATA / "clauses.json").read_text())


if __name__ == "__main__":
    for row in load_book():
        print(
            row["name"],
            "exposure", row["credit_exposure"],
            "limit", row["credit_limit"],
            "status", row["status"],
            "delivery", row["delivery_amount"],
            "return", row["return_amount"],
            "action", row["action"],
        )
