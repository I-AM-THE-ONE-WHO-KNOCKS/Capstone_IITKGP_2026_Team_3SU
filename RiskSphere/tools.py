"""The calculations the planner is allowed to run.

Each public function returns a dict and does not call the chat gateway.
current_exposure and calculate_margin_call read the marked book.
get_market_data talks to Yahoo for one ticker. regulatory_haircut is a
table lookup. check_pfe_limit runs the future-exposure simulation.

run(name, args) is what the MCP server calls. The console does not call it
directly. Unknown names raise. The marked book is cached for the question
and cleared by reset().
"""

import math

import json

import agent
import books
import log
import market
import pfe

_BOOK = {"rows": None, "error": None}


def reset():
    """Drop the cached book so the next question downloads prices again."""
    _BOOK["rows"] = None
    _BOOK["error"] = None


def _book():
    """Return the marked book, loading it once per question.

    A failure is remembered and re-raised, so a second tool in the same
    question does not download Yahoo again after the first failure.
    """
    if _BOOK["error"] is not None:
        raise _BOOK["error"]
    if _BOOK["rows"] is None:
        try:
            _BOOK["rows"] = books.load_book()
        except Exception as exc:
            _BOOK["error"] = exc
            raise
    return _BOOK["rows"]


def _party(counterparty):
    """Resolve a name or id to one marked counterparty dict.

    Raises ValueError when the name is not Meridian, Apex, or Harbor Lane.
    """
    book = _book()
    cid = books.find_party(book, counterparty) or str(counterparty or "").strip().lower()
    party = books.by_id(book).get(cid)
    if party is None:
        raise ValueError("Name Meridian, Apex, or Harbor Lane.")
    return party


def current_exposure(counterparty):
    """Today's exposure for one name, compared with the stored limit.

    counterparty is an id or a display name. The result includes the gross
    mark, collateral after haircut, exposure, limit, headroom, utilization,
    status, and the per-trade marks. measure is current_exposure_not_pfe
    so a reader does not treat this as a simulated future number.
    Status uses the unrounded utilization. Does not call the model.
    """
    party = _party(counterparty)
    return {
        "tool": "current_exposure",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "measure": party["measure"],
        "gross_mtm_usd": party["gross_mtm"],
        "collateral_after_haircut_usd": party["collateral_value"],
        "current_exposure_usd": party["credit_exposure"],
        "credit_limit_usd": party["credit_limit"],
        "headroom_usd": party["headroom"],
        "utilization_pct": party["utilization_pct"],
        "status": party["status"],
        "note": "This is today's exposure. Potential future exposure is stage 2.",
        "trades": [
            {
                "trade_id": t["trade_id"],
                "symbol": t["symbol"],
                "yahoo_price": t["price"],
                "asof": t["asof"],
                "csv": t["csv"],
                "entry": t["entry_price"],
                "quantity": t["quantity"],
                "mtm_usd": t["market_value"],
            }
            for t in party["trades"]
        ],
        "collateral": party["collateral_lines"],
    }


def calculate_margin_call(counterparty):
    """Variation-margin call for one name, from the stored agreement.

    Credit support is max(0, mark + independent amount - threshold).
    Raw delivery is that amount minus collateral after the haircut.
    A delivery at least the minimum transfer is rounded up. A return is
    rounded down. action is CALL_COUNTERPARTY, RETURN_COLLATERAL, or
    NO_ACTION. requires_human_approval is true whenever the action is
    not NO_ACTION. This function does not send the call.
    """
    party = _party(counterparty)
    return {
        "tool": "calculate_margin_call",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "net_mtm_usd": party["gross_mtm"],
        "independent_amount_usd": party["independent_amount"],
        "threshold_usd": party["threshold"],
        "mta_usd": party["mta"],
        "rounding_usd": party["rounding"],
        "credit_support_amount_usd": party["credit_support_amount"],
        "collateral_after_haircut_usd": party["collateral_value"],
        "haircut_pct": party["collateral_lines"][0]["haircut_pct"] if party["collateral_lines"] else 0,
        "raw_delivery_usd": party["raw_delivery"],
        "delivery_amount_usd": party["delivery_amount"],
        "return_amount_usd": party["return_amount"],
        "action": party["action"],
        "requires_human_approval": party["requires_human_approval"],
        "counterparty_may_call_us_usd": party["counterparty_may_call_us"],
        "collateral": party["collateral_lines"],
    }


def get_market_data(ticker):
    """Last Yahoo close and a one-year volatility for one symbol.

    ticker is required, for example SPY or EURUSD=X. Volatility is the
    sample standard deviation of the last 252 daily log returns, times
    sqrt(252), in percent. Raises if the ticker is blank or Yahoo fails.
    Does not mark the book and does not call the model.
    """
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        raise ValueError("ticker is required")
    quote = market.fetch_quotes([symbol])[symbol]
    closes = market.fetch_closes(symbol)[-252:]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i] and closes[i - 1] > 0]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    vol = round(math.sqrt(var) * math.sqrt(252) * 100, 2)
    return {
        "tool": "get_market_data",
        "ticker": symbol,
        "spot": quote["price"],
        "asof": quote["asof"],
        "currency": quote["currency"],
        "ann_vol_1y_pct": vol,
        "closes_used": len(closes),
        "data_source": "yahoo",
        "csv": quote["csv"],
    }


def check_pfe_limit(counterparty):
    """Simulated future exposure for one name, against the stored limit.

    Runs the engine checks first. If those fail, the book number is not
    quoted. The paths use variation margin, not the posted cash balance.
    Returns the peak, the uncollateralised peak, today's simulated mark,
    utilization, status, and the fan. Status OK here does not clear a
    breach on today's exposure.
    """
    party = _party(counterparty)
    log.trace(
        f"pfe start {party['counterparty_id']} paths will be read from config"
    )
    result = pfe.check_limit(party, agent.load_config())
    log.trace(
        f"pfe {party['counterparty_id']} peak={result['peak_pfe_collateralized_usd']} "
        f"status={result['status']}"
    )
    log.debug("pfe json " + json.dumps(result))
    return result


def regulatory_haircut(asset_class, maturity_bucket="1-5y", rating_bucket="AAA-AA-"):
    """One cell of the standardised haircut schedule.

    Does not need a counterparty and does not call Yahoo. Unknown maturity
    or rating falls back to 1-5y and AAA-AA-. Returns the percent and a
    source note. The table has not yet been spot-checked against the PDF.
    """
    pct = books.haircut_pct(asset_class, maturity_bucket, rating_bucket)
    return {
        "tool": "regulatory_haircut",
        "asset_class": asset_class,
        "maturity_bucket": maturity_bucket if maturity_bucket in books.MATURITIES else "1-5y",
        "rating_bucket": rating_bucket if rating_bucket in books.RATINGS else "AAA-AA-",
        "haircut_pct": pct,
        "source": "BCBS-IOSCO standardised haircut schedule, Appendix B of d499. Not yet spot-checked against the PDF.",
    }


HANDLERS = {
    "current_exposure": current_exposure,
    "calculate_margin_call": calculate_margin_call,
    "get_market_data": get_market_data,
    "regulatory_haircut": regulatory_haircut,
    "check_pfe_limit": check_pfe_limit,
}


def run(name, args):
    """Dispatch one planned step.

    name is a key of HANDLERS. args is the dict from the plan.
    Market data reads ticker. A haircut reads asset_class, maturity_bucket,
    and rating_bucket. The other tools read counterparty.
    Logs the call at trace and the JSON result at debug. Raises ValueError
    for an unknown name, and whatever the tool raises on a bad input.
    """
    log.trace(f"tool in {name} {args}")
    fn = HANDLERS.get(name)
    if fn is None:
        log.error(f"unknown tool {name}")
        raise ValueError(f"unknown tool {name}")
    if name == "get_market_data":
        result = fn(args.get("ticker"))
    elif name == "regulatory_haircut":
        result = fn(
            args.get("asset_class"),
            args.get("maturity_bucket") or "1-5y",
            args.get("rating_bucket") or "AAA-AA-",
        )
    else:
        result = fn(args.get("counterparty"))
    log.debug("tool out " + name + " " + json.dumps(result))
    return result
