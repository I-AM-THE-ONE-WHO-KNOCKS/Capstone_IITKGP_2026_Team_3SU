"""The sample agreement sentences must agree with the data the calculators read.

data/clauses.json is prose that restates csa_terms.csv and the haircut table in
books.py. Nothing else keeps the two in step, and the old haircut note said 1%
and 8% for years while the calculator used other figures. These tests parse the
numbers out of the prose and compare them with the stored terms.
"""

import csv
import json
import re

import pytest

import books

CLAUSES = json.loads((books.DATA / "clauses.json").read_text())
USD = re.compile(r"USD ([\d,]+)")


def _terms():
    with (books.DATA / "csa_terms.csv").open() as f:
        return {r["counterparty_id"]: r for r in csv.DictReader(f)}


def _names():
    with (books.DATA / "counterparties.csv").open() as f:
        return {r["counterparty_id"]: r["name"] for r in csv.DictReader(f)}


def _usd(text):
    return [int(m.replace(",", "")) for m in USD.findall(text)]


def _amount_after(label, text):
    """First USD amount after `label` within the same sentence, or None."""
    m = re.search(label + r"[^.]*?USD ([\d,]+)", text)
    return int(m.group(1).replace(",", "")) if m else None


def csa_problems(clause, terms, names):
    """Everything wrong with one counterparty clause. An empty list means it agrees."""
    cid, text = clause["counterparty_id"], clause["text"]
    if cid not in terms:
        return [f"{clause['id']}: counterparty_id {cid!r} is not in csa_terms.csv"]
    row, problems = terms[cid], []
    for label, field in (("Threshold", "threshold"), ("Minimum Transfer Amount", "mta")):
        found = _amount_after(label, text)
        if found is not None and found != int(row[field]):
            problems.append(f"{clause['id']}: {label} is {found}, csa_terms.csv says {row[field]}")
    allowed = {int(row[k]) for k in ("threshold", "mta", "rounding", "independent_amount")}
    for amount in _usd(text):
        if amount not in allowed:
            problems.append(f"{clause['id']}: USD {amount} is not one of this counterparty's terms")
    m = re.search(r"nearest USD ([\d,]+)", text)
    if m and int(m.group(1).replace(",", "")) != int(row["rounding"]):
        problems.append(f"{clause['id']}: rounding is {m.group(1)}, csa_terms.csv says {row['rounding']}")
    for other, name in names.items():
        if other != cid and name.split()[0].lower() in text.lower():
            problems.append(f"{clause['id']}: mentions {name}, which is a different counterparty")
    return problems


def _band(label, text):
    """The three maturity cells for `label`, in percent, or None if the sentence is missing."""
    m = re.search(
        re.escape(label) + r" are ([\d.]+) percent under one year, ([\d.]+) percent for one to five years,"
        r" and ([\d.]+) percent over five years",
        text,
    )
    return tuple(float(x) for x in m.groups()) if m else None


def _single(label, text):
    m = re.search(re.escape(label) + r" is ([\d.]+) percent", text)
    return float(m.group(1)) if m else None


def haircut_problems(text, table=None):
    """Everything in the haircut note that differs from the calculator's table."""
    table = table or books.HAIRCUTS
    problems = []
    bands = {
        "Government bonds rated AA- or better": ("government", "AAA-AA-"),
        "Corporate bonds rated AA- or better": ("corporate", "AAA-AA-"),
        "Corporate bonds rated A+ to BBB-": ("corporate", "A+-BBB-"),
        "Government bonds rated A+ to BBB-": ("government", "A+-BBB-"),
    }
    for label, (asset, rating) in bands.items():
        cells = table[asset][rating]
        expected = (cells["<=1y"], cells["1-5y"], cells[">5y"])
        got = _band(label, text)
        if got is None:
            if label in text:
                problems.append(f"{label}: sentence is not in the expected form")
        elif got != expected:
            problems.append(f"{label}: note says {got}, table says {expected}")
    for label, key in (
        ("Cash haircut", "cash"),
        ("Main index equity", "equity_main_index"),
        ("Other equity", "equity_other"),
        ("Gold", "gold"),
    ):
        got = _single(label, text)
        if got is None:
            problems.append(f"{label}: missing from the note")
        elif got != table[key]:
            problems.append(f"{label}: note says {got}, table says {table[key]}")
    table_values = {0.0, table["gold"], table["equity_main_index"], table["equity_other"]}
    for asset in ("government", "corporate"):
        for cells in table[asset].values():
            table_values.update(cells.values())
    for pct in re.findall(r"([\d.]+) percent", text):
        if float(pct) not in table_values:
            problems.append(f"{pct} percent is not a value anywhere in the table")
    return problems


@pytest.mark.parametrize("clause", [c for c in CLAUSES if c["counterparty_id"]], ids=lambda c: c["id"])
def test_agreement_clause_matches_csa_terms(clause):
    assert csa_problems(clause, _terms(), _names()) == []


def test_haircut_note_matches_calculator_table():
    notes = [c for c in CLAUSES if c["id"] == "haircut-note"]
    assert len(notes) == 1
    assert haircut_problems(notes[0]["text"]) == []


def test_every_counterparty_has_its_threshold_and_minimum_transfer_stated():
    """A stored term with no sentence behind it cannot be searched or quoted."""
    for cid, row in _terms().items():
        text = " ".join(c["text"] for c in CLAUSES if c["counterparty_id"] == cid)
        assert _amount_after("Threshold", text) == int(row["threshold"]), cid
        assert _amount_after("Minimum Transfer Amount", text) == int(row["mta"]), cid


def test_clause_ids_unique_and_fields_present():
    ids = [c["id"] for c in CLAUSES]
    assert len(ids) == len(set(ids))
    for c in CLAUSES:
        assert {"id", "counterparty_id", "title", "text"} <= set(c)
        assert c["text"].strip() and c["title"].strip()


# The checks above only matter if they fail on a wrong clause. These plant the
# old defects and require the checker to notice each one.

def test_checker_catches_wrong_threshold():
    bad = {"id": "x", "counterparty_id": "meridian", "title": "t",
           "text": "The Threshold for Meridian Capital is USD 900,000."}
    assert any("Threshold" in p for p in csa_problems(bad, _terms(), _names()))


def test_checker_catches_wrong_minimum_transfer():
    bad = {"id": "x", "counterparty_id": "apex", "title": "t",
           "text": "The Minimum Transfer Amount is USD 250,000."}
    assert csa_problems(bad, _terms(), _names())


def test_checker_catches_stray_amount_and_other_counterparty():
    stray = {"id": "x", "counterparty_id": "harbor", "title": "t", "text": "A fee of USD 12,345 applies."}
    assert csa_problems(stray, _terms(), _names())
    other = {"id": "y", "counterparty_id": "harbor", "title": "t", "text": "Same as Meridian."}
    assert any("different counterparty" in p for p in csa_problems(other, _terms(), _names()))


def test_checker_catches_the_old_haircut_note():
    old = ("Cash haircut is 0 percent. Government bond haircut is 1 percent. "
           "Corporate bond haircut is 8 percent. Equity haircut is 15 percent.")
    assert haircut_problems(old)


def test_checker_catches_a_single_changed_cell():
    note = next(c["text"] for c in CLAUSES if c["id"] == "haircut-note")
    changed = note.replace("12 percent over five years", "10 percent over five years")
    assert changed != note and haircut_problems(changed)


# ----------------------------------------------------------------------------- the 12 CFR passages

CFR_FILE = books.DATA / "docs" / "haircut_schedule_12cfr45.md"
CFR_ROWS = {  # passage id -> the start of each row of the saved table it restates
    "cfr45-government": ["Eligible government and related debt"],
    "cfr45-gse": ["Eligible GSE debt not identified"],
    "cfr45-other-debt": ["Other eligible publicly traded debt"],
    "cfr45-equity-gold": ["Equities included in the S&P 500", "Equities included in the S&P 1500", "Gold"],
}


def _percents(text):
    return [float(x) for x in re.findall(r"([\d.]+) percent", text)]


def cfr_problems(passage_id, text):
    """Percentages in a 12 CFR passage, in order, against the saved copy of Table B."""
    lines = CFR_FILE.read_text().splitlines()
    expected = []
    for start in CFR_ROWS[passage_id]:
        expected += [p for line in lines if line.startswith(start) for p in _percents(line)]
    got = _percents(text)
    return [] if got == expected else [f"{passage_id}: passage says {got}, saved table says {expected}"]


@pytest.mark.parametrize("passage_id", sorted(CFR_ROWS))
def test_cfr_passage_matches_the_saved_table(passage_id):
    text = next(c["text"] for c in CLAUSES if c["id"] == passage_id)
    assert cfr_problems(passage_id, text) == []


def test_every_cfr_passage_in_clauses_json_is_checked():
    in_json = {c["id"] for c in CLAUSES if c["id"].startswith("cfr45-")}
    assert in_json == set(CFR_ROWS)


def test_checker_catches_a_changed_cfr_figure():
    text = next(c["text"] for c in CLAUSES if c["id"] == "cfr45-other-debt")
    assert cfr_problems("cfr45-other-debt", text.replace("8.0 percent over five", "6.0 percent over five"))


def test_checker_catches_a_wrong_rounding_clause():
    bad = {"id": "x", "counterparty_id": "apex", "title": "t",
           "text": "A delivery amount is rounded up to the nearest USD 5,000."}
    assert any("rounding" in p for p in csa_problems(bad, _terms(), _names()))
