"""The real-trade book built from DTCC public swap reports: selection, mapping, and loading.

The reports are large and their terms of use are unchecked, so they are not in the
repository. These tests feed the selection functions rows shaped like the real
report (same column names) and check what is kept, what is invented, and that the
app loads the result through RISKSPHERE_TRADES.
"""

import csv

import pytest

import books
import dtcc

ASOF = "2026-11-19"


def _swap(id_, level="16,500.5", notional="10,000,000", ccy="USD", underlier="S&P 500 INDEX",
          action="NEWT", event="TRAD", expiry="2027-06-30"):
    return {"Dissemination Identifier": id_, "Action type": action, "Event type": event, "Expiration Date": expiry,
            "UPI Underlier Name": underlier, "Notional amount-Leg 1": notional, "Notional currency-Leg 1": ccy,
            "Price": level}


def _fwd(id_, rate="1.1178", eur="5,000,000", usd="5,589,000", fisn="NA/Fwd EUR USD", expiry="2027-03-31",
         action="NEWT", legs=("EUR", "USD")):
    return {"Dissemination Identifier": id_, "Action type": action, "Event type": "TRAD", "Expiration Date": expiry,
            "UPI FISN": fisn, "Notional currency-Leg 1": legs[0], "Notional amount-Leg 1": eur if legs[0] == "EUR" else usd,
            "Notional currency-Leg 2": legs[1], "Notional amount-Leg 2": usd if legs[0] == "EUR" else eur,
            "Exchange rate": rate}


def test_index_swap_keeps_the_real_size_level_and_dates():
    [t] = dtcc.pick_index_swaps([_swap("1")], ASOF, 5)
    assert t["symbol"] == "^SP500TR"
    assert t["entry_price"] == 16500.5
    assert t["quantity"] == pytest.approx(10_000_000 / 16500.5)
    assert t["maturity"] == "2027-06-30" and t["id"] == "1"


@pytest.mark.parametrize("row", [
    _swap("1", underlier="APPLE INC"),          # not the index
    _swap("1", action="MODI"),                  # an amendment, not a new trade
    _swap("1", action="TERM"),                  # a termination
    _swap("1", event="NOVA"),                   # not a trade event
    _swap("1", expiry="2026-11-01"),            # already expired
    _swap("1", ccy="EUR"),                      # not a USD notional
])
def test_index_swap_filters(row):
    assert dtcc.pick_index_swaps([row], ASOF, 5) == []


@pytest.mark.parametrize("level", ["0", "", "abc", "250000"])
def test_index_swap_with_a_nonsense_level_is_dropped(level):
    assert dtcc.pick_index_swaps([_swap("1", level=level)], ASOF, 5) == []


def test_forward_reads_the_eur_leg_whichever_side_it_is_on():
    a = dtcc.pick_eurusd_forwards([_fwd("1")], ASOF, 5)
    b = dtcc.pick_eurusd_forwards([_fwd("2", legs=("USD", "EUR"))], ASOF, 5)
    assert a[0]["quantity"] == b[0]["quantity"] == 5_000_000
    assert a[0]["entry_price"] == 1.1178 and a[0]["symbol"] == "EURUSD=X"


@pytest.mark.parametrize("row", [
    _fwd("1", fisn="NA/O Van Call EUR USD"),  # an option, not a linear forward
    _fwd("1", fisn="NA/Fwd NDF EUR USD"),      # non-deliverable
    _fwd("1", rate="145.2"),                   # not a EUR/USD rate
    _fwd("1", action="MODI"),
    _fwd("1", expiry="2026-01-01"),
])
def test_forward_filters(row):
    assert dtcc.pick_eurusd_forwards([row], ASOF, 5) == []


def test_selection_is_deterministic_and_limited():
    rows = [_swap(str(i)) for i in (9, 3, 7, 1)]
    assert [t["id"] for t in dtcc.pick_index_swaps(rows, ASOF, 2)] == ["1", "3"]


def test_book_rows_say_what_is_invented_and_where_each_trade_came_from():
    picked = dtcc.pick_index_swaps([_swap("1"), _swap("2")], ASOF, 5) + dtcc.pick_eurusd_forwards([_fwd("3")], ASOF, 5)
    rows = dtcc.to_book(picked, "2026_11_19")
    assert [r["counterparty_id"] for r in rows] == ["meridian", "apex", "harbor"]
    assert [r["quantity"] > 0 for r in rows] == [True, False, True]  # direction alternates
    assert all(r["direction_invented"] == "true" for r in rows)
    assert rows[0]["source_dissemination_id"] == "1"
    assert rows[2]["source_report"] == "CFTC_CUMULATIVE_FOREX_2026_11_19"
    assert set(rows[0]) == set(dtcc.COLUMNS)


def test_the_sample_book_is_the_default():
    assert books.trades_file() == "trades.csv"


def test_the_app_loads_a_dtcc_book_through_the_environment(tmp_path, monkeypatch, mock_market):
    rows = dtcc.to_book(dtcc.pick_index_swaps([_swap("1", level="16,000", notional="16,000,000")], ASOF, 5)
                        + dtcc.pick_eurusd_forwards([_fwd("2"), _fwd("3")], ASOF, 5), "2026_11_19")
    path = tmp_path / "trades_dtcc.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=dtcc.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    mock_market["^SP500TR"] = {"symbol": "^SP500TR", "price": 15000.0, "asof": "2026-11-20", "currency": "USD", "csv": "x"}
    monkeypatch.setenv("RISKSPHERE_TRADES", str(path))
    book = {p["counterparty_id"]: p for p in books.load_book()}
    meridian = book["meridian"]
    [trade] = [t for t in meridian["trades"] if t["trade_id"] == "D1"]
    assert trade["symbol"] == "^SP500TR" and trade["quantity"] == 1000.0
    # 1,000 index units long, entered at 16,000, marked at 15,000: the app's mark is a loss of 1,000,000
    assert trade["market_value"] == -1_000_000
    assert meridian["gross_mtm"] == -1_000_000  # Meridian holds only D1 in this book
    assert meridian["credit_exposure"] == 0       # a loss is never an exposure
