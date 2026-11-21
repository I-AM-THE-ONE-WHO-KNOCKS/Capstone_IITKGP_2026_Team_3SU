"""Build a trade book from real, public swap reports (DTCC, CFTC Part 43 dissemination).

What is real: each trade's notional, price or forward rate, effective date and
expiry, taken from one day's public reports. What is invented: the counterparty
(the reports are anonymous, so trades are dealt round-robin to Meridian, Apex and
Harbor Lane) and the direction (the reports do not say who bought, so it
alternates long and short). Collateral, agreement terms and limits stay the
sample values. Every row keeps the report's Dissemination Identifier so it can be
traced back.

Real counterparts to the sample instruments:
- SPY swaps    -> total return swaps on "S&P 500 INDEX", marked on Yahoo ^SP500TR.
                  The reported levels (about 16,500 on 2026-11-19) are the total
                  return index, not the price index ^GSPC (about 7,000), so marking
                  on ^GSPC would invent a loss. quantity = notional / level, entry = level.
- EURUSD=X     -> deliverable EUR/USD forwards ("NA/Fwd EUR USD").
                  quantity = EUR notional, entry = forward rate (USD per EUR).
- LQD and SHY  -> no single-name ETF swaps appear in the equity reports, so the
                  bond trades have no real counterpart in this book.

DTCC's terms of use have not been checked, so the downloads and the built file
live in data/dtcc/, which is gitignored. Run scripts/build_dtcc_book.py.
"""

import csv
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DTCC_DIR = ROOT / "data" / "dtcc"
RAW_DIR = DTCC_DIR / "raw"
BOOK_FILE = DTCC_DIR / "trades_dtcc.csv"
URL = "https://kgc0418-tdw-data-0.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_{asset}_{date}.zip"
COUNTERPARTIES = ["meridian", "apex", "harbor"]
COLUMNS = ["trade_id", "counterparty_id", "symbol", "quantity", "entry_price", "currency", "maturity",
           "source_report", "source_dissemination_id", "direction_invented"]


def _number(text):
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return None


def _new_trade(row, asof):
    """A new, live trade: reported as new, a trade event, and not yet expired."""
    return (row.get("Action type") == "NEWT" and row.get("Event type") == "TRAD"
            and (row.get("Expiration Date") or "") > asof)


def pick_index_swaps(rows, asof, n):
    """Real S&P 500 index swaps with a USD notional and a plausible index level, oldest id first."""
    out = []
    for row in rows:
        if row.get("UPI Underlier Name") != "S&P 500 INDEX" or not _new_trade(row, asof):
            continue
        notional, level = _number(row.get("Notional amount-Leg 1")), _number(row.get("Price"))
        if row.get("Notional currency-Leg 1") != "USD" or not notional or not level or not 1000 < level < 20000:
            continue
        out.append({"symbol": "^SP500TR", "quantity": notional / level, "entry_price": level,
                    "maturity": row["Expiration Date"], "id": row["Dissemination Identifier"], "report": "EQUITIES"})
    return sorted(out, key=lambda t: t["id"])[:n]


def pick_eurusd_forwards(rows, asof, n):
    """Real deliverable EUR/USD forwards with a EUR leg and a rate between 0.8 and 1.6 USD per EUR."""
    out = []
    for row in rows:
        if row.get("UPI FISN") != "NA/Fwd EUR USD" or not _new_trade(row, asof):
            continue
        legs = {row.get("Notional currency-Leg 1"): _number(row.get("Notional amount-Leg 1")),
                row.get("Notional currency-Leg 2"): _number(row.get("Notional amount-Leg 2"))}
        eur, rate = legs.get("EUR"), _number(row.get("Exchange rate"))
        if not eur or not rate or not 0.8 < rate < 1.6:
            continue
        out.append({"symbol": "EURUSD=X", "quantity": eur, "entry_price": rate,
                    "maturity": row["Expiration Date"], "id": row["Dissemination Identifier"], "report": "FOREX"})
    return sorted(out, key=lambda t: t["id"])[:n]


def to_book(picked, date):
    """Deal the picked trades to the three counterparties and alternate long and short."""
    rows = []
    for i, t in enumerate(picked):
        sign = 1 if i % 2 == 0 else -1
        rows.append({
            "trade_id": f"D{i + 1}",
            "counterparty_id": COUNTERPARTIES[i % len(COUNTERPARTIES)],
            "symbol": t["symbol"],
            "quantity": round(sign * t["quantity"], 4),
            "entry_price": round(t["entry_price"], 6),
            "currency": "USD",
            "maturity": t["maturity"],
            "source_report": f"CFTC_CUMULATIVE_{t['report']}_{date}",
            "source_dissemination_id": t["id"],
            "direction_invented": "true",
        })
    return rows


def read_report(path):
    """Stream the CSV inside one downloaded zip without unpacking it to disk."""
    archive = zipfile.ZipFile(path)
    with archive.open(archive.namelist()[0]) as handle:
        yield from csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8", errors="replace"))


def download(asset, date, raw_dir=RAW_DIR):
    """Fetch one day's cumulative report unless it is already on disk. date is YYYY_MM_DD."""
    import urllib.request

    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"CFTC_CUMULATIVE_{asset}_{date}.zip"
    if not path.exists():
        request = urllib.request.Request(URL.format(asset=asset, date=date), headers={"User-Agent": "RiskSphere-dtcc"})
        with urllib.request.urlopen(request, timeout=300) as response:
            path.write_bytes(response.read())
    return path


def build(date, per_kind=3, out=BOOK_FILE):
    """Download the equity and FX reports for date, pick the trades, and write the book."""
    asof = date.replace("_", "-")
    swaps = pick_index_swaps(read_report(download("EQUITIES", date)), asof, per_kind)
    forwards = pick_eurusd_forwards(read_report(download("FOREX", date)), asof, per_kind)
    picked = [t for pair in zip(swaps, forwards) for t in pair] + swaps[len(forwards):] + forwards[len(swaps):]
    rows = to_book(picked, date)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows
