"""Daily closes from Yahoo Finance.

Downloads two years of daily closes for a symbol, writes them locally, and
marks from the last close. The sample book itself is not downloaded. If
Yahoo does not answer, the caller stops. A file left from an earlier run
is not used in place of a failed download. Results are cached in memory
for 60 seconds so one question does not download the same symbol twice.
"""

import csv
import json
import re
import time

import log
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MKT = Path(__file__).resolve().parent / "data" / "market"
_CACHE = {}
_CACHE_AT = 0
_TTL = 60


def _chart(symbol, range_):
    """GET the Yahoo chart JSON for one symbol and a range such as 2y.

    Uses the public chart endpoint, daily bars. Raises RuntimeError when
    the payload has no result. Does not fall back to a saved file.
    """
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + "?interval=1d&range=" + range_
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode())
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo returned no price for {symbol}")
    return result[0]


def _file(symbol):
    """Local path for a symbol. Characters outside letters and digits become underscores.

    EURUSD=X is stored as EURUSD_X.csv.
    """
    return MKT / (re.sub(r"[^A-Za-z0-9]+", "_", symbol) + ".csv")


def _download(symbol):
    """Download two years of daily closes and write date,close rows.

    Drops bars with a missing stamp or close. Fewer than 20 usable rows
    is an error. Returns {"rows": [(date, close), ...], "currency", "csv"}.
    The chart API serves two years; a longer range is not requested.
    """
    chart = _chart(symbol, "2y")
    meta = chart.get("meta") or {}
    stamps = chart.get("timestamp") or []
    quote = (chart.get("indicators") or {}).get("quote") or [{}]
    closes = (quote[0].get("close") or []) if quote else []
    rows = []
    for stamp, close in zip(stamps, closes):
        if stamp is None or close is None:
            continue
        day = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")
        rows.append((day, float(close)))
    if len(rows) < 20:
        log.error(f"yahoo history too short for {symbol}")
        raise RuntimeError(f"Yahoo returned too little history for {symbol}")
    log.trace(f"yahoo download {symbol} rows={len(rows)} last={rows[-1][0]} {rows[-1][1]}")
    log.debug(f"yahoo file {_file(symbol)}")
    MKT.mkdir(parents=True, exist_ok=True)
    path = _file(symbol)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "close"])
        writer.writerows(rows)
    return {
        "rows": rows,
        "currency": meta.get("currency") or "USD",
        "csv": str(path.relative_to(path.parents[2])),
    }


def _series(symbol):
    """Cached download for one symbol. The cache expires after 60 seconds."""
    global _CACHE_AT, _CACHE
    now = time.time()
    if _CACHE and now - _CACHE_AT < _TTL and symbol in _CACHE:
        return _CACHE[symbol]
    if now - _CACHE_AT >= _TTL:
        _CACHE.clear()
        _CACHE_AT = now
    _CACHE[symbol] = _download(symbol)
    return _CACHE[symbol]


def fetch_quotes(symbols):
    """Last close for each symbol.

    Returns {symbol: {price, asof, currency, csv, rows}}. symbols is a list
    of Yahoo tickers. A failure on any symbol raises and does not return
    a stale close.
    """
    quotes = {}
    for symbol in symbols:
        series = _series(symbol)
        day, close = series["rows"][-1]
        quotes[symbol] = {
            "price": close,
            "asof": day,
            "currency": series["currency"],
            "csv": series["csv"],
            "rows": len(series["rows"]),
        }
    return quotes


def fetch_closes(symbol):
    """Close prices only, oldest first, after a fresh or cached download."""
    return [close for _day, close in _series(symbol)["rows"]]


def history(symbol):
    """Dated closes as (date, close) pairs, oldest first.

    Used by the future-exposure simulation to line up symbols on common days.
    """
    return list(_series(symbol)["rows"])
