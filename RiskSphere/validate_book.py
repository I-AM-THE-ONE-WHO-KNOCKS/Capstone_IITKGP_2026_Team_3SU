"""Mark the six-name book and check the arithmetic.

The book is data/. Each stored limit is the simulated collateralised peak
times that name's factor, rounded, as written in limit_basis.json. Yahoo
is required, because the mark uses the latest close. The chat model is
not called.

A failure exits non-zero. The checks are exposure not negative, status
following 85 and 100, a delivery or return on the rounding step, the
stored limit matching the recorded peak, and the simulation engine still
passing on its throw-away book.
"""

import json
import os
from pathlib import Path

import agent
import books
import pfe

HERE = Path(__file__).resolve().parent / "data"
FAILURES = []


def check(name, ok, detail=""):
    """Print one line. A false result is remembered."""
    print(("ok  " if ok else "FAIL") + " " + name + (f" - {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main():
    """Load the validation book and the engine checks."""
    os.environ["PFE_LLM_OFF"] = "1"
    if not (HERE / "counterparties.csv").exists() or not (HERE / "limit_basis.json").exists():
        raise SystemExit("missing book; run python3 build_validation_book.py")
    basis = {row["counterparty_id"]: row for row in json.loads((HERE / "limit_basis.json").read_text())}
    book = books.load_book(HERE)
    expected_ids = {"CP-001", "CP-002", "CP-003", "CP-004", "CP-005", "CP-006"}
    check("book has the six names", {row["counterparty_id"] for row in book} == expected_ids, str(len(book)))
    for row in book:
        check(f"{row['counterparty_id']} exposure is not negative", row["credit_exposure"] >= 0)
        check(f"{row['counterparty_id']} limit is positive", row["credit_limit"] > 0)
        util = (100.0 * row["credit_exposure"] / row["credit_limit"]) if row["credit_limit"] else 0
        expect = "BREACH" if util > 100 else "WARNING" if util >= 85 else "OK"
        check(f"{row['counterparty_id']} status matches {util:.1f}%", row["status"] == expect, row["status"])
        if row["action"] == "CALL_COUNTERPARTY":
            check(
                f"{row['counterparty_id']} delivery clears the minimum transfer",
                row["delivery_amount"] >= row["mta"] and row["delivery_amount"] % row["rounding"] == 0,
                str(row["delivery_amount"]),
            )
        elif row["action"] == "RETURN_COLLATERAL":
            check(
                f"{row['counterparty_id']} return clears the minimum transfer",
                row["return_amount"] >= row["mta"] and row["return_amount"] % row["rounding"] == 0,
                str(row["return_amount"]),
            )
        else:
            check(f"{row['counterparty_id']} no action has no amount", row["delivery_amount"] == 0 and row["return_amount"] == 0)
        recorded = basis.get(row["counterparty_id"], {})
        peak = recorded.get("peak_pfe_collateralized_usd", 0)
        factor = recorded.get("factor", 0)
        expect_limit = int(round(max(peak, 1) * factor, -5))
        if expect_limit < 100_000:
            expect_limit = 100_000
        check(
            f"{row['counterparty_id']} limit is the rounded peak",
            row["credit_limit"] == expect_limit,
            f"stored {row['credit_limit']} peak {peak} factor {factor}",
        )
        check(f"{row['counterparty_id']} credit inputs are in range", 0 <= row["pd"] <= 1 and 0 <= row["lgd"] <= 1)
        print(
            f"     {row['name']}: exposure {row['credit_exposure']} "
            f"limit {row['credit_limit']} {row['status']} action {row['action']}"
        )
    ok, lines = pfe.validate(agent.load_config())
    check("pfe engine checks pass", ok, " | ".join(lines[-2:]))
    print(f"\n{len(FAILURES)} failed" if FAILURES else "\nvalidation book passed")
    raise SystemExit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
