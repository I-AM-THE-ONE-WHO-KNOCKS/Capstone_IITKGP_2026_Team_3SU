"""Independent reference implementation of the README's arithmetic.

Written from the README text (How I get a number, steps 1-8), not from
books.py. It uses exact integer / Fraction arithmetic so it does not share
the float tricks (epsilons, rounding order) of the code under test.
"""

from fractions import Fraction
import math

# README step 3 / BCBS-IOSCO d499 schedule, in percent, as exact fractions.
HAIRCUT = {
    ("cash", None, None): Fraction(0),
    ("equity_main_index", None, None): Fraction(15),
    ("equity_other", None, None): Fraction(25),
    ("gold", None, None): Fraction(15),
}
for _rating, _row in {
    "AAA-AA-": {"<=1y": "0.5", "1-5y": "2", ">5y": "4"},
    "A+-BBB-": {"<=1y": "1", "1-5y": "3", ">5y": "6"},
}.items():
    for _mat, _pct in _row.items():
        HAIRCUT[("government", _mat, _rating)] = Fraction(_pct)
for _rating, _row in {
    "AAA-AA-": {"<=1y": "1", "1-5y": "4", ">5y": "8"},
    "A+-BBB-": {"<=1y": "2", "1-5y": "6", ">5y": "12"},
}.items():
    for _mat, _pct in _row.items():
        HAIRCUT[("corporate", _mat, _rating)] = Fraction(_pct)

# How a collateral.csv asset maps to the schedule (README step 3).
POSTED = {
    "cash": ("cash", None, None),
    "government_bond": ("government", "1-5y", "AAA-AA-"),
    "corporate_bond": ("corporate", ">5y", "A+-BBB-"),
    "equity": ("equity_main_index", None, None),
}


def _round_half_even(fr):
    return int(round(fr))  # round() on a Fraction is exact, half to even


def ceil_to(amount, step):
    return -(-amount // step) * step if step > 0 else amount


def floor_to(amount, step):
    return (amount // step) * step if step > 0 else amount


def status(exposure, limit):
    """Exact integer comparison on the unrounded ratio. > 100 % BREACH, >= 85 % WARNING."""
    if limit == 0:
        return "OK"
    if exposure * 100 > limit * 100:
        return "BREACH"
    if exposure * 100 >= limit * 85:
        return "WARNING"
    return "OK"


def party(trades, collateral, terms, limit):
    """trades: [(qty, entry, price)], collateral: [(asset, amount)],
    terms: dict threshold, mta, rounding, independent. Returns the expected dict."""
    gross = sum(_round_half_even(Fraction(str(q)) * (Fraction(str(p)) - Fraction(str(e)))) for q, e, p in trades)
    counted = 0
    for asset, amount in collateral:
        cls, mat, rat = POSTED[asset]
        counted += _round_half_even(Fraction(amount) * (1 - HAIRCUT[(cls, mat, rat)] / 100))
    exposure = max(gross - counted, 0)
    support = max(0, gross + terms["independent"] - terms["threshold"])
    raw = support - counted
    delivery = returned = 0
    if raw > 0 and raw >= terms["mta"]:
        delivery, action = ceil_to(raw, terms["rounding"]), "CALL_COUNTERPARTY"
    elif raw < 0 and -raw >= terms["mta"]:
        returned, action = floor_to(-raw, terms["rounding"]), "RETURN_COLLATERAL"
    else:
        action = "NO_ACTION"
    return {
        "gross_mtm": gross,
        "collateral_value": counted,
        "credit_exposure": exposure,
        "headroom": limit - exposure,
        "status": status(exposure, limit),
        "credit_support_amount": support,
        "raw_delivery": raw,
        "delivery_amount": delivery,
        "return_amount": returned,
        "action": action,
        "requires_human_approval": action != "NO_ACTION",
    }


def lognormal_pfe(notional, spot, strike, sd, k, direction, z):
    """Closed-form uncollateralised PFE quantile for one linear trade (README stage 2)."""
    growth = math.exp(-0.5 * sd * sd * k + direction * sd * math.sqrt(k) * z)
    return max(direction * notional * (spot / strike * growth - 1), 0.0)
