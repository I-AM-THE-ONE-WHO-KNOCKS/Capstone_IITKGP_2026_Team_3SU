"""Build data/dtcc/trades_dtcc.csv from one day of real DTCC swap reports.

Usage: python3 scripts/build_dtcc_book.py [YYYY_MM_DD]     (default 2026_11_19)
Then:  RISKSPHERE_TRADES=dtcc/trades_dtcc.csv python3 app.py

Real: notional, price or rate, dates. Invented: counterparty and direction. See dtcc.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dtcc  # noqa: E402

date = sys.argv[1] if len(sys.argv) > 1 else "2026_11_19"
rows = dtcc.build(date)
print(f"{len(rows)} trades written to {dtcc.BOOK_FILE}")
for r in rows:
    print(f"  {r['trade_id']} {r['counterparty_id']:8} {r['symbol']:9} qty {r['quantity']:>16} entry {r['entry_price']:>10} "
          f"maturity {r['maturity']}  DTCC id {r['source_dissemination_id']}")
