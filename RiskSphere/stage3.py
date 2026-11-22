"""Stage 3 figures. Python only.

Scenario profit and loss reprices the equity and FX trades under a stored
shock. The initial-margin number is a parametric proxy with a horizon and
a percentile, not ISDA SIMM. Collateral coverage uses eligible assets
after the haircut. Expected loss is synthetic PD times LGD times an
exposure that is labelled as today's exposure or the simulated future
exposure. None of these is a regulatory capital number.
"""

import math
import statistics

import market
import pfe


def _ann_vol(closes):
    window = closes[-252:]
    rets = [
        math.log(window[i] / window[i - 1])
        for i in range(1, len(window))
        if window[i] > 0 and window[i - 1] > 0
    ]
    if len(rets) < 2:
        raise RuntimeError("not enough closes for a volatility")
    mean = sum(rets) / len(rets)
    var = sum((item - mean) ** 2 for item in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def _one_year_return(closes):
    window = closes[-252:]
    if len(window) < 2 or window[0] <= 0:
        return None
    return window[-1] / window[0] - 1


def scenario_pnl(party, scenario, shocks, label):
    """Reprice one name under a stored price shock.

    scenario is the id, shocks maps a symbol to a relative move such as
    -0.20, and label is the sentence stored with that shock. Profit and
    loss is the shocked mark minus today's mark, for symbols named in the
    shock only. Other trades stay at today's mark and contribute zero.
    A missing Yahoo price raises. The model does not choose the shock size.
    """
    lines = []
    base = 0
    shocked_total = 0
    pnl = 0
    shocked_symbols = set(shocks)
    market_notes = []
    for symbol in sorted(shocked_symbols):
        closes = market.fetch_closes(symbol)
        market_notes.append({
            "symbol": symbol,
            "one_year_return": None if _one_year_return(closes) is None else round(_one_year_return(closes), 4),
            "ann_vol": round(_ann_vol(closes), 4),
        })
    for trade in party["trades"]:
        symbol = trade["symbol"]
        mark = trade["market_value"]
        base += mark
        shock = shocks.get(symbol)
        if shock is None:
            shocked_total += mark
            lines.append({
                "trade_id": trade["trade_id"],
                "symbol": symbol,
                "in_scenario": False,
                "base_mtm_usd": mark,
                "shocked_mtm_usd": mark,
                "pnl_usd": 0,
            })
            continue
        shocked_price = trade["price"] * (1 + shock)
        shocked_mark = int(round(trade["quantity"] * (shocked_price - trade["entry_price"])))
        trade_pnl = shocked_mark - mark
        shocked_total += shocked_mark
        pnl += trade_pnl
        lines.append({
            "trade_id": trade["trade_id"],
            "symbol": symbol,
            "in_scenario": True,
            "shock": shock,
            "base_mtm_usd": mark,
            "shocked_mtm_usd": shocked_mark,
            "pnl_usd": trade_pnl,
        })
    return {
        "tool": "scenario_pnl",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "scenario": scenario,
        "scenario_label": label,
        "formula": "scenario pnl = shocked mark minus today's mark, equity and FX shocks only",
        "base_mtm_usd": base,
        "shocked_mtm_usd": shocked_total,
        "scenario_pnl_usd": pnl,
        "trades": lines,
        "market": market_notes,
        "note": "Price shock on the stored symbols. Not a rates model and not a full crisis replay.",
    }


def initial_margin_proxy(party, horizon_days, percentile):
    """Parametric loss quantile for one name. Not ISDA SIMM.

    Dollar sensitivity of each trade is quantity times the Yahoo price.
    Variance uses the daily historical volatility and correlation of those
    symbols, scaled by horizon_days. The proxy is the normal quantile of
    that variance at `percentile`. Horizon and percentile are returned on
    the result so the label cannot be dropped.
    """
    trades = party["trades"]
    if not trades:
        return {
            "tool": "initial_margin_proxy",
            "counterparty_id": party["counterparty_id"],
            "counterparty_name": party["name"],
            "initial_margin_usd": 0,
            "horizon_days": horizon_days,
            "percentile": percentile,
            "formula": "no trades",
            "label": "illustrative initial margin proxy, not ISDA SIMM",
        }
    symbols = sorted({trade["symbol"] for trade in trades})
    _days, aligned = pfe._align(symbols)
    ordered, sd, corr = pfe._vols_and_corr(aligned)
    index = {symbol: i for i, symbol in enumerate(ordered)}
    delta = [0.0] * len(ordered)
    for trade in trades:
        delta[index[trade["symbol"]]] += trade["quantity"] * trade["price"]
    variance = 0.0
    for i in range(len(ordered)):
        for j in range(len(ordered)):
            variance += delta[i] * delta[j] * sd[ordered[i]] * sd[ordered[j]] * corr[i][j]
    variance = max(variance, 0.0) * horizon_days
    z = statistics.NormalDist().inv_cdf(percentile)
    amount = int(round(z * math.sqrt(variance)))
    return {
        "tool": "initial_margin_proxy",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "initial_margin_usd": amount,
        "horizon_days": horizon_days,
        "percentile": percentile,
        "formula": "z times sqrt of correlated dollar-variance times horizon",
        "label": "illustrative initial margin proxy, not ISDA SIMM",
        "symbols": ordered,
    }


def collateral_coverage(party):
    """Eligible collateral against the credit-support requirement.

    An asset with no haircut mapping is listed and counts as zero.
    Shortfall is max(requirement - eligible, 0). Surplus is the other way.
    Requirement is the credit support amount already on the book.
    """
    eligible = 0
    ineligible = []
    lines = []
    for line in party["collateral_lines"]:
        lines.append({
            "asset": line["asset"],
            "amount": line["amount"],
            "haircut_pct": line["haircut_pct"],
            "counted": line["counted"],
            "eligible": line.get("eligible", True),
        })
        if line.get("eligible", True):
            eligible += line["counted"]
        else:
            ineligible.append(line["asset"])
    required = party["credit_support_amount"]
    shortfall = max(required - eligible, 0)
    surplus = max(eligible - required, 0)
    return {
        "tool": "collateral_coverage",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "requirement_usd": required,
        "eligible_collateral_usd": eligible,
        "shortfall_usd": shortfall,
        "surplus_usd": surplus,
        "ineligible_assets": ineligible,
        "lines": lines,
        "formula": "shortfall = max(credit support - eligible value after haircut, 0)",
    }


def expected_loss(party, ead_usd, ead_label):
    """PD x LGD x EAD from the synthetic credit row and a labelled exposure.

    ead_usd is either today's exposure or the simulated future peak.
    ead_label must say which. The result is not regulatory EAD and not
    SA-CCR. PD and LGD are the stored synthetic inputs.
    """
    if party.get("pd") is None or party.get("lgd") is None:
        raise RuntimeError("PD and LGD are missing. The model does not invent them.")
    el = party["pd"] * party["lgd"] * ead_usd
    return {
        "tool": "expected_loss",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "pd": party["pd"],
        "lgd": party["lgd"],
        "pd_horizon": party.get("pd_horizon") or "1y",
        "pd_source": party.get("pd_source") or "synthetic",
        "ead_usd": int(round(ead_usd)),
        "ead_label": ead_label,
        "expected_loss_usd": int(round(el)),
        "formula": "expected loss = PD x LGD x EAD",
        "note": "Synthetic PD and LGD. This EAD is not regulatory EAD and not SA-CCR.",
    }
