"""Simulated potential future exposure.

Correlated geometric Brownian motion, flat historical volatility, and
variation-margin collateral lagged by a margin period of risk. The chat
model does not draw the paths.

A number for the book is quoted only after the closed-form check and the
invariants pass on a throw-away series. The peak is the maximum, over time,
of the per-step quantile, not the quantile of each path's own peak.
Collateral at a step uses the mark from margin-period steps earlier.
Posted cash on the book is not an input.
"""

import math
import random
import statistics
from datetime import datetime, timezone

import log
import market

_READY = {"ok": None, "lines": []}


def _mean(xs):
    """Arithmetic mean. xs must be non-empty."""
    return sum(xs) / len(xs)


def _cov(a, b):
    """Sample covariance of two equal-length series, divisor n - 1."""
    ma, mb = _mean(a), _mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) - 1)


def _cholesky(corr):
    """Lower-triangular Cholesky factor of a correlation matrix.

    A diagonal that would go non-positive is floored so a single-asset
    matrix still factors. Returns the lower triangle as a list of rows.
    """
    n = len(corr)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            total = sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                lower[i][j] = math.sqrt(max(corr[i][i] - total, 1e-12))
            else:
                lower[i][j] = (corr[i][j] - total) / lower[j][j]
    return lower


def _quantile(values, q):
    """Linear-interpolated quantile. q is between 0 and 1, for example 0.95.

    An empty list returns 0. The result is not rounded.
    """
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def _align(symbols):
    """Line up Yahoo histories on dates that every symbol has.

    Returns (sorted dates, {symbol: [close, ...]}). Raises when fewer than
    40 overlapping days remain, which is too little for a covariance.
    """
    series = {symbol: market.history(symbol) for symbol in symbols}
    common = None
    for rows in series.values():
        days = {day for day, _close in rows}
        common = days if common is None else common & days
    days = sorted(common)
    if len(days) < 40:
        raise RuntimeError("not enough overlapping Yahoo history for a covariance")
    out = {}
    for symbol, rows in series.items():
        by_day = {day: close for day, close in rows}
        out[symbol] = [by_day[day] for day in days]
    return days, out


def _vols_and_corr(aligned, window=504):
    """Daily volatility and correlation from the last `window` log returns.

    aligned maps a symbol to a list of closes. Returns (symbols, {symbol: daily sd}, corr).
    The diagonal of corr is bumped by 1e-10 so the matrix stays positive definite.
    A one-asset simulation does not use this factor on the path itself.
    """
    symbols = list(aligned)
    rets = {}
    for symbol, closes in aligned.items():
        paired = [
            math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes))
            if closes[i] > 0 and closes[i - 1] > 0
        ]
        rets[symbol] = paired[-window:]
    length = min(len(rets[symbol]) for symbol in symbols)
    rets = {symbol: rows[-length:] for symbol, rows in rets.items()}
    sd = {symbol: math.sqrt(_cov(rets[symbol], rets[symbol])) for symbol in symbols}
    corr = []
    for left in symbols:
        row = []
        for right in symbols:
            if sd[left] == 0 or sd[right] == 0:
                row.append(1.0 if left == right else 0.0)
            else:
                row.append(_cov(rets[left], rets[right]) / (sd[left] * sd[right]))
        corr.append(row)
    for i in range(len(symbols)):
        corr[i][i] += 1e-10
    return symbols, sd, corr


def simulate(trades, threshold, mta, independent, mpor, n_paths, seed, max_steps, quantile):
    """Run the paths and return today's mark, the peaks, and a small fan.

    Each trade needs symbol, quantity, entry, and steps (how far that trade
    lives). If every trade also has closes, those series are used and Yahoo
    is not called. Otherwise the histories are downloaded and aligned.

    The spot follows a geometric random walk with flat historical volatility.
    One asset uses the daily vol directly. Several assets share shocks
    through the Cholesky factor. Mark at a step is quantity times
    (simulated price minus entry), summed across trades that are still alive,
    and floored at zero for the exposure.

    Variation margin at step t is the saved mark from step t - mpor, minus
    the threshold, floored at zero, and only posted when it meets the
    minimum transfer. Independent amount is added. The collateralised
    exposure is the simulated mark minus that margin, floored at zero.

    The reported peak is the max over steps of the quantile, for both the
    collateralised and the uncollateralised profiles. profile_unc keeps the
    uncollateralised quantile at every step so the closed-form check can
    use it. The fan stores p50, p95, and p99 of the collateralised exposure
    at steps 0, 21, 63, 126, and the horizon.

    threshold, mta, and independent are in currency units. mpor is in steps.
    n_paths and seed control the Monte Carlo. max_steps caps the horizon.
    quantile is the PFE percentile, usually 0.95.
    """
    symbols = sorted({row["symbol"] for row in trades})
    if all("closes" in row for row in trades):
        aligned = {}
        for row in trades:
            aligned.setdefault(row["symbol"], row["closes"])
        symbols = list(aligned)
    else:
        _days, aligned = _align(symbols)
    symbols, sd, corr = _vols_and_corr(aligned)
    lower = _cholesky(corr)
    index = {symbol: i for i, symbol in enumerate(symbols)}
    spot = [aligned[symbol][-1] for symbol in symbols]
    horizon = max(1, min(max_steps, max(row["steps"] for row in trades)))
    rng = random.Random(seed)
    log_s = [[0.0] * len(symbols) for _ in range(n_paths)]
    legs = [
        (index[row["symbol"]], row["quantity"], row["entry"], row["steps"])
        for row in trades
    ]
    peak_unc = 0.0
    peak_col = 0.0
    peak_day = 0
    fan = []
    profile_unc = []
    current = 0.0
    saved = []
    one = len(symbols) == 1
    daily0 = sd[symbols[0]] if one else 0.0
    drift0 = -0.5 * daily0 * daily0

    for step in range(horizon + 1):
        net = [0.0] * n_paths
        if step == 0:
            value = 0.0
            for i, qty, entry, _life in legs:
                value += qty * (spot[i] - entry)
            for path in range(n_paths):
                net[path] = value
            current = value
        elif one:
            for path in range(n_paths):
                log_s[path][0] += rng.gauss(0.0, 1.0) * daily0 + drift0
                price = spot[0] * math.exp(log_s[path][0])
                value = 0.0
                for _i, qty, entry, life in legs:
                    if step <= life:
                        value += qty * (price - entry)
                net[path] = value
        else:
            for path in range(n_paths):
                shock = [rng.gauss(0.0, 1.0) for _ in symbols]
                for i in range(len(symbols)):
                    mixed = sum(lower[i][j] * shock[j] for j in range(i + 1))
                    daily = sd[symbols[i]]
                    log_s[path][i] += mixed * daily - 0.5 * daily * daily
                value = 0.0
                for i, qty, entry, life in legs:
                    if step <= life:
                        price = spot[i] * math.exp(log_s[path][i])
                        value += qty * (price - entry)
                net[path] = value
        saved.append(net)
        # Collateral is variation margin on the value from margin-period-of-risk steps ago.
        lagged = saved[max(step - mpor, 0)]
        exposure_unc = [max(value, 0.0) for value in net]
        exposure_col = [0.0] * n_paths
        for path, value in enumerate(net):
            credit_support = max(lagged[path] - threshold, 0.0)
            posted = credit_support if credit_support >= mta else 0.0
            posted += independent
            exposure_col[path] = max(value - posted, 0.0)
        q_unc = _quantile(exposure_unc, quantile)
        q_col = _quantile(exposure_col, quantile)
        profile_unc.append(q_unc)
        if q_col >= peak_col:
            peak_col = q_col
            peak_day = step
        peak_unc = max(peak_unc, q_unc)
        if step in (0, 21, 63, 126) or step == horizon:
            fan.append({
                "step": step,
                "p50": int(round(_quantile(exposure_col, 0.50))),
                "p95": int(round(q_col)),
                "p99": int(round(_quantile(exposure_col, 0.99))),
            })
    return {
        "current_mtm": current,
        "peak_uncollateralized": peak_unc,
        "peak_collateralized": peak_col,
        "peak_step": peak_day,
        "fan": fan,
        "profile_unc": profile_unc,
        "horizon_steps": horizon,
    }


def _business_steps(maturity, asof, cap):
    """Convert a calendar maturity into business-day steps, then cap it.

    Uses 252/365. The result is at least 1 and at most cap.
    maturity and asof are YYYY-MM-DD.
    """
    end = datetime.strptime(maturity, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start = datetime.strptime(asof, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    days = max((end - start).days, 1)
    return max(1, min(cap, int(days * 252 / 365)))


def _book_trades(party, cap):
    """Trades of one marked counterparty, in the shape simulate() expects.

    Quantity and entry come from the book. steps is the capped maturity.
    Closes are not attached, so simulate() downloads Yahoo history.
    """
    asof = party["trades"][0]["asof"]
    return [
        {
            "symbol": trade["symbol"],
            "quantity": trade["quantity"],
            "entry": trade["entry_price"],
            "steps": _business_steps(trade["maturity"], asof, cap),
        }
        for trade in party["trades"]
    ]


def _synthetic_closes(n, sigma, seed):
    """A throw-away price path for the engine check. Not a market series.

    n closes, starting at 100, daily shock of size sigma. seed makes the
    series repeatable.
    """
    rng = random.Random(seed)
    price = 100.0
    closes = []
    for _ in range(n):
        price *= math.exp(rng.gauss(0.0, sigma))
        closes.append(price)
    return closes


def validate(cfg):
    """Closed form and the invariants, cached for the life of the process.

    Builds a fake one-asset history and compares the uncollateralised
    quantile with the lognormal formula at days 21, 63, 126, and 250, long
    and short, within 8%. Also checks that today's mark matches
    notional times (spot/strike - 1), that collateral does not raise the
    peak, that a huge threshold turns collateral off, that offsetting trades
    net, that a one-day margin period cuts the collateralised peak below
    35% of the uncollateralised peak, and that a different seed stays
    within 8%.

    cfg supplies pfe_validate_paths, pfe_quantile, and pfe_seed.
    Returns (ok, lines). A later call in the same process returns the
    cached result and does not resimulate. A book number must not be
    quoted when ok is false.
    """
    if _READY["ok"] is not None:
        return _READY["ok"], _READY["lines"]
    paths = int(cfg.get("pfe_validate_paths", 2500))
    steps = 250
    sigma = 0.012
    closes = _synthetic_closes(780, sigma, 5)
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    window = rets[-504:]
    sd = math.sqrt(sum((x - _mean(window)) ** 2 for x in window) / (len(window) - 1))
    spot, strike = closes[-1], closes[-100]
    notional = 10_000_000.0
    quantity = notional / strike
    z = statistics.NormalDist().inv_cdf(cfg.get("pfe_quantile", 0.95))

    def analytic(k, direction):
        growth = math.exp(-0.5 * sd * sd * k + direction * sd * math.sqrt(k) * z)
        return max(direction * notional * (spot / strike * growth - 1), 0.0)

    def trade(direction, life):
        return [{
            "symbol": "VAL",
            "quantity": direction * quantity,
            "entry": strike,
            "steps": life,
            "closes": closes,
        }]

    seed = int(cfg.get("pfe_seed", 7))
    q = cfg.get("pfe_quantile", 0.95)
    log.trace(f"pfe checks start paths={paths} steps={steps}")
    long = simulate(trade(1, steps), 0, 0, 0, 10, paths, seed, steps, q)
    short = simulate(trade(-1, steps), 0, 0, 0, 10, paths, seed, steps, q)
    big = simulate(trade(1, steps), 1e15, 0, 0, 10, paths, seed, steps, q)
    net = simulate(
        trade(1, steps) + [{
            "symbol": "VAL",
            "quantity": -quantity,
            "entry": strike,
            "steps": steps,
            "closes": closes,
        }],
        0, 0, 0, 10, paths, seed, steps, q,
    )
    fast = simulate(trade(1, steps), 0, 0, 0, 1, paths, seed, steps, q)
    lines = []

    def check(name, expected, actual, ok):
        lines.append(f"{name}: expected {expected:.2f}, actual {actual:.2f}, passed {ok}")
        return ok

    passed = True
    mtm = notional * (spot / strike - 1)
    passed = check(
        "current mark equals notional times (spot/strike - 1)",
        mtm,
        long["current_mtm"],
        abs(long["current_mtm"] - mtm) <= 1e-6 * notional,
    ) and passed
    for day in (21, 63, 126, 250):
        an = analytic(day, 1)
        mc = long["profile_unc"][day]
        passed = check(
            f"closed form long day {day}",
            an,
            mc,
            abs(mc - an) <= 0.08 * max(an, 0.02 * notional),
        ) and passed
        an_s = analytic(day, -1)
        mc_s = short["profile_unc"][day]
        passed = check(
            f"closed form short day {day}",
            an_s,
            mc_s,
            abs(mc_s - an_s) <= 0.08 * max(an_s, 0.02 * notional),
        ) and passed
    passed = check(
        "collateral does not raise PFE",
        long["peak_uncollateralized"],
        long["peak_collateralized"],
        long["peak_collateralized"] <= long["peak_uncollateralized"] + 1.0,
    ) and passed
    passed = check(
        "huge threshold turns collateral off",
        big["peak_uncollateralized"],
        big["peak_collateralized"],
        abs(big["peak_uncollateralized"] - big["peak_collateralized"]) < 1.0,
    ) and passed
    passed = check(
        "offsetting trades net",
        0.0,
        net["peak_uncollateralized"],
        net["peak_uncollateralized"] < 1.0,
    ) and passed
    passed = check(
        "one-day margin period cuts collateralised PFE",
        0.35 * fast["peak_uncollateralized"],
        fast["peak_collateralized"],
        fast["peak_collateralized"] < 0.35 * fast["peak_uncollateralized"],
    ) and passed
    again = simulate(trade(1, steps), 0, 0, 0, 10, paths, seed + 1, steps, q)
    base_peak = long["peak_uncollateralized"]
    passed = check(
        "different seed within 8%",
        base_peak,
        again["peak_uncollateralized"],
        abs(again["peak_uncollateralized"] - base_peak) < 0.08 * max(base_peak, 1.0),
    ) and passed
    _READY["ok"] = passed
    _READY["lines"] = lines
    for line in lines:
        log.debug("pfe check " + line)
    if passed:
        log.trace("pfe checks passed")
    else:
        log.error("pfe checks failed")
    return passed, lines


def check_limit(party, cfg):
    """Future exposure for one marked counterparty, after the engine checks.

    party is a row from the marked book. cfg supplies path count, seed,
    horizon cap, quantile, and the margin period. Raises RuntimeError when
    validate() fails, and in that case no book peak is returned.

    Utilization is the collateralised peak divided by the stored limit.
    Status is BREACH over 100%, WARNING from 85%, otherwise OK, using the
    unrounded ratio. The fan is collateralised. Posted cash is not used
    inside the paths. Returns the tool dict, including the check lines.
    """
    ok, lines = validate(cfg)
    if not ok:
        raise RuntimeError("PFE engine checks failed. " + " | ".join(lines))
    trades = _book_trades(party, int(cfg.get("pfe_max_steps", 126)))
    result = simulate(
        trades,
        party["threshold"],
        party["mta"],
        party["independent_amount"],
        int(cfg.get("mpor_days", 10)),
        int(cfg.get("pfe_paths", 2000)),
        int(cfg.get("pfe_seed", 7)),
        int(cfg.get("pfe_max_steps", 126)),
        cfg.get("pfe_quantile", 0.95),
    )
    peak = int(round(result["peak_collateralized"]))
    limit = party["credit_limit"]
    raw = (100.0 * peak / limit) if limit else 0.0
    if raw > 100:
        status = "BREACH"
    elif raw >= 85:
        status = "WARNING"
    else:
        status = "OK"
    return {
        "tool": "check_pfe_limit",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "measure": "pfe",
        "current_mtm_usd": int(round(result["current_mtm"])),
        "peak_pfe_collateralized_usd": peak,
        "peak_pfe_uncollateralized_usd": int(round(result["peak_uncollateralized"])),
        "peak_step": result["peak_step"],
        "horizon_steps": result["horizon_steps"],
        "credit_limit_usd": limit,
        "utilization_pct": round(raw, 1),
        "status": status,
        "pfe_quantile": cfg.get("pfe_quantile", 0.95),
        "n_paths": int(cfg.get("pfe_paths", 2000)),
        "mpor_days": int(cfg.get("mpor_days", 10)),
        "fan": result["fan"],
        "model": "Monte Carlo, correlated GBM, flat historical vol, VM collateral",
        "checks": lines,
    }
