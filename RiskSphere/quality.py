"""Data check for the sample book.

Reads the stored tables and reports a bad row. It does not invent a missing
price, probability of default, or haircut, and it does not call Yahoo or
the chat model. A later calculation calls assert_ok() and stops when this
check fails.
"""

import csv
import re
from datetime import datetime
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REQUIRED = {
    "counterparties.csv": ["counterparty_id", "name", "book"],
    "trades.csv": ["trade_id", "counterparty_id", "symbol", "quantity", "entry_price", "currency", "maturity"],
    "collateral.csv": ["counterparty_id", "asset", "amount", "currency"],
    "csa_terms.csv": ["counterparty_id", "threshold", "mta", "rounding", "independent_amount", "base_currency"],
    "limits.csv": ["counterparty_id", "pfe_limit"],
    "credit.csv": ["counterparty_id", "pd", "lgd", "pd_horizon", "source"],
}


class QualityError(RuntimeError):
    """The book failed a check. No figure should be computed from it."""


def _rows(name):
    path = DATA / name
    if not path.exists():
        return None
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _num(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def check():
    """Return {"passed", "problems", "rows_checked"}.

    problems is a list of {where, problem}. passed is false when that list
    is not empty. rows_checked counts data rows, not the header.
    """
    problems = []
    rows_checked = 0

    def bad(where, problem):
        problems.append({"where": where, "problem": problem})

    loaded = {}
    for name, columns in REQUIRED.items():
        rows = _rows(name)
        if rows is None:
            bad(name, "file is missing")
            loaded[name] = []
            continue
        if rows and any(column not in rows[0] for column in columns):
            missing = [column for column in columns if column not in rows[0]]
            bad(name, "missing columns " + ", ".join(missing))
        loaded[name] = rows
        rows_checked += len(rows)

    ids = [row.get("counterparty_id", "").strip() for row in loaded["counterparties.csv"]]
    if len(ids) != len(set(ids)) or "" in ids:
        bad("counterparties.csv", "counterparty ids must be present and unique")
    known = set(ids)

    trade_ids = []
    for index, row in enumerate(loaded["trades.csv"], start=2):
        where = f"trades.csv row {index}"
        trade_id = row.get("trade_id", "").strip()
        trade_ids.append(trade_id)
        if not trade_id:
            bad(where, "trade id is blank")
        if row.get("counterparty_id", "").strip() not in known:
            bad(where, "counterparty id is not on the book")
        if not row.get("symbol", "").strip():
            bad(where, "symbol is blank")
        quantity = _num(row.get("quantity"))
        if quantity is None or quantity == 0:
            bad(where, "quantity must be a non-zero number. A short is negative.")
        entry = _num(row.get("entry_price"))
        if entry is None or entry <= 0:
            bad(where, "entry price must be a number above zero")
        if row.get("currency", "").strip() != "USD":
            bad(where, "currency must be USD on this book")
        maturity = row.get("maturity", "").strip()
        if not DATE_RE.match(maturity):
            bad(where, "maturity must be YYYY-MM-DD")
        else:
            try:
                datetime.strptime(maturity, "%Y-%m-%d")
            except ValueError:
                bad(where, "maturity is not a real date")
    if any(not item for item in trade_ids) or len(trade_ids) != len(set(trade_ids)):
        bad("trades.csv", "trade ids must be present and unique")

    for index, row in enumerate(loaded["collateral.csv"], start=2):
        where = f"collateral.csv row {index}"
        if row.get("counterparty_id", "").strip() not in known:
            bad(where, "counterparty id is not on the book")
        if not row.get("asset", "").strip():
            bad(where, "asset is blank")
        amount = _num(row.get("amount"))
        if amount is None or amount < 0:
            bad(where, "amount must be zero or positive")
        if row.get("currency", "").strip() != "USD":
            bad(where, "currency must be USD on this book")

    for name, fields in (
        ("csa_terms.csv", ("threshold", "mta", "rounding", "independent_amount")),
        ("limits.csv", ("pfe_limit",)),
    ):
        seen = []
        for index, row in enumerate(loaded[name], start=2):
            where = f"{name} row {index}"
            cid = row.get("counterparty_id", "").strip()
            seen.append(cid)
            if cid not in known:
                bad(where, "counterparty id is not on the book")
            for field in fields:
                number = _num(row.get(field))
                if number is None or number < 0:
                    bad(where, f"{field} must be zero or positive")
        if set(seen) != known:
            bad(name, "every counterparty needs a row, and only those names")

    for index, row in enumerate(loaded["credit.csv"], start=2):
        where = f"credit.csv row {index}"
        if row.get("counterparty_id", "").strip() not in known:
            bad(where, "counterparty id is not on the book")
        pd = _num(row.get("pd"))
        lgd = _num(row.get("lgd"))
        if pd is None or not 0 <= pd <= 1:
            bad(where, "pd must be between 0 and 1. It is not filled in by the model.")
        if lgd is None or not 0 <= lgd <= 1:
            bad(where, "lgd must be between 0 and 1. It is not filled in by the model.")
        if row.get("pd_horizon", "").strip() != "1y":
            bad(where, "pd horizon on this book is 1y")
        if row.get("source", "").strip() != "synthetic":
            bad(where, "source must say synthetic. A real PD is not inferred from the name.")
    credit_ids = [row.get("counterparty_id", "").strip() for row in loaded["credit.csv"]]
    if set(credit_ids) != known:
        bad("credit.csv", "every counterparty needs a synthetic PD and LGD")

    return {"passed": not problems, "problems": problems, "rows_checked": rows_checked}


def assert_ok():
    """Raise QualityError when the book has a bad row. Returns the report otherwise."""
    report = check()
    if not report["passed"]:
        detail = "; ".join(f"{row['where']}: {row['problem']}" for row in report["problems"])
        raise QualityError("Book failed the data check. " + detail)
    return report
