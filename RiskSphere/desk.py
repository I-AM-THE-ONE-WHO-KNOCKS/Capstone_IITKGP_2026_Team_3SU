"""Desk files a person can open. Python writes them. Nothing is sent.

The term sheet is extracted from the downloaded agreements. A field is
VERIFIED only when the quote is in the document and states exactly one
amount. REVIEW and NOT_FOUND stay on the sheet for a person. The model is
not required for this pass: a pattern finds the sentence, and the gate
checks it. A person can ask the model to try one document; the same gate
still applies, and a failed quote is not written as verified.

The daily pack, the margin notice, the stress workbook, the dispute sheet,
and the fan chart all use the six-name book. Each file is marked as a draft.
"""

import json
import re
from datetime import date
from pathlib import Path

import books
import filings
import office

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "desk"

FIELDS = {
    "threshold": {"kw": r"threshold", "amount": True},
    "minimum_transfer_amount": {"kw": r"minimum transfer amount", "amount": True},
    "rounding": {"kw": r"rounding", "amount": True},
    "independent_amount": {"kw": r"independent amount", "amount": True},
    "base_currency": {"kw": r"base currency", "amount": False},
    "valuation_agent": {"kw": r"valuation agent", "amount": False},
    "notification_time": {"kw": r"notification time", "amount": False},
    "eligible_collateral": {"kw": r"eligible collateral", "amount": False},
}
_CUR = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP"}
_AMT1 = re.compile(
    r"(?P<cur>USD|US\$|EUR|GBP|JPY|\$|€|£)\s?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?P<mult>\s?(?:million|m)\b)?",
    re.I,
)
_AMT2 = re.compile(r"(?P<num>\d{1,3}(?:,\d{3})+|\d+)\s?(?P<cur>USD|EUR|GBP|JPY)\b", re.I)
_ZERO = re.compile(r"\b(zero|none|not applicable|nil)\b", re.I)
_TYPO = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u2013": "-", "\u2014": "-", "\u00a0": " "})


def _alnum(text):
    """Letters and digits only, so a curly quote does not fail a real quote."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").translate(_TYPO).lower())


def parse_amount(text):
    """First amount in a snippet, or zero when the snippet says none. Otherwise None."""
    found = []
    for pattern in (_AMT1, _AMT2):
        match = pattern.search(text)
        if match:
            currency = match.group("cur").upper()
            amount = float(match.group("num").replace(",", ""))
            if match.groupdict().get("mult"):
                amount *= 1_000_000
            found.append((match.start(), amount, _CUR.get(currency, currency)))
    zero = _ZERO.search(text)
    if zero:
        found.append((zero.start(), 0.0, None))
    if not found:
        return None
    _, amount, currency = min(found, key=lambda item: item[0])
    return amount, currency


def _chunks(text, size=900, overlap=150):
    """Overlapping windows so a term that sits on a boundary is still visible."""
    if not text:
        return []
    step = max(size - overlap, 1)
    return [text[start:start + size] for start in range(0, len(text), step)]


def field_passages(text, field, top=2):
    """Windows that mention the field, most mentions first."""
    pattern = re.compile(FIELDS[field]["kw"], re.I)
    scored = [(len(pattern.findall(window)), window) for window in _chunks(text)]
    scored = sorted((item for item in scored if item[0]), key=lambda item: -item[0])
    return [window for _, window in scored[:top]]


def regex_extract(field, passages):
    """Pull one field from the passages. Returns None when the pattern does not see it."""
    spec = FIELDS[field]
    pattern = re.compile(spec["kw"], re.I)
    for passage in passages:
        for match in pattern.finditer(passage):
            snippet = re.split(r"\n\s*\n", passage[match.start():match.start() + 260])[0]
            if spec["amount"]:
                parsed = parse_amount(snippet)
                if parsed:
                    amount, currency = parsed
                    label = f"{currency or ''} {amount:,.0f}".strip()
                    return {"value": label, "amount": amount, "currency": currency, "quote": snippet.strip()}
            elif field == "notification_time":
                clock = re.search(r"\d{1,2}[:.]\d{2}\s?(?:a\.?m\.?|p\.?m\.?)[^.]{0,40}", snippet, re.I)
                if clock:
                    return {"value": clock.group(0).strip(), "amount": None, "currency": None, "quote": snippet.strip()}
            else:
                return {"value": snippet[:120].strip(), "amount": None, "currency": None, "quote": snippet.strip()}
    return None


def verify_record(record, document):
    """VERIFIED only when the quote is in the document and states one amount.

    Several amounts in one quote means a person has to pick the party.
    A quote that is not in the file is REVIEW, never VERIFIED.
    """
    if not record or not record.get("quote"):
        return "NOT_FOUND", "term not stated in the retrieved passages"
    quote = record.get("quote", "")
    fragments = [part for part in re.split(r"\.\.\.|\[\.\.\.\]|\u2026", quote) if len(_alnum(part)) >= 12]
    folded = _alnum(document)
    if not fragments or not all(_alnum(part) in folded for part in fragments):
        return "REVIEW", "quote not found verbatim in the document"
    amount = record.get("amount")
    if amount is not None:
        amount = float(amount)
        if int(amount) == 0:
            number_ok = bool(_ZERO.search(quote) or re.search(r"\b0\b", quote))
        else:
            forms = {f"{int(amount):,}", str(int(amount))}
            if amount >= 1_000_000 and amount % 1_000_000 == 0:
                forms.add(f"{amount / 1_000_000:g} million")
            number_ok = any(form in quote for form in forms)
        if not number_ok:
            return "REVIEW", "extracted number is not in the quote"
        amounts = set()
        for pattern in (_AMT1, _AMT2):
            for match in pattern.finditer(quote):
                value = float(match.group("num").replace(",", ""))
                if match.groupdict().get("mult"):
                    value *= 1_000_000
                amounts.add(value)
        if _ZERO.search(quote):
            amounts.add(0.0)
        if len(amounts) > 1:
            return "REVIEW", "quote states several amounts; a person must confirm the figure"
    return "VERIFIED", ""


def extract_document(doc_id, folder=None):
    """One agreement, eight fields, each with a status from the quote gate."""
    folder = Path(folder or filings.CSA_DIR)
    path = folder / f"{doc_id}.txt"
    text = path.read_text(errors="ignore")
    meta_path = path.with_suffix(".json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    rows = []
    for field in FIELDS:
        passages = field_passages(text, field)
        record = regex_extract(field, passages) if passages else None
        status, reason = verify_record(record, text)
        record = record or {}
        rows.append({
            "doc_id": doc_id,
            "field": field,
            "status": status,
            "reason": reason,
            "value": record.get("value") or "",
            "amount": record.get("amount"),
            "currency": record.get("currency") or "",
            "quote": (record.get("quote") or "")[:400],
            "source_url": meta.get("url", ""),
            "method": "python",
        })
    return rows


def write_term_sheet(limit=None):
    """Workbook of extracted terms. Green is verified. Yellow needs a person. Grey was not found."""
    OUT.mkdir(parents=True, exist_ok=True)
    paths = sorted(filings.CSA_DIR.glob("*.txt")) if filings.CSA_DIR.exists() else []
    if limit:
        paths = paths[:limit]
    records = []
    for path in paths:
        records.extend(extract_document(path.stem))
    fields = list(FIELDS)
    header = ["doc_id", "source_url"] + fields
    summary = [header]
    fills = {}
    by_doc = []
    seen = []
    for record in records:
        if record["doc_id"] not in seen:
            seen.append(record["doc_id"])
    for doc_id in seen:
        group = [record for record in records if record["doc_id"] == doc_id]
        row = [doc_id, group[0]["source_url"]]
        for field in fields:
            item = next(record for record in group if record["field"] == field)
            if item["amount"] is not None:
                shown = f"{item['currency']} {item['amount']:,.0f}".strip()
            else:
                shown = str(item["value"])[:60]
            row.append(shown)
        summary.append(row)
        excel_row = len(summary)
        for index, field in enumerate(fields):
            item = next(record for record in group if record["field"] == field)
            fills[(excel_row, 3 + index)] = item["status"]
        by_doc.append(doc_id)
    evidence = [["doc_id", "field", "status", "reason", "method", "value", "amount", "currency", "quote", "source_url"]]
    evidence_fills = {}
    for record in records:
        evidence.append([
            record["doc_id"], record["field"], record["status"], record["reason"], record["method"],
            record["value"], record["amount"] if record["amount"] is not None else "",
            record["currency"], record["quote"], record["source_url"],
        ])
        evidence_fills[(len(evidence), 3)] = record["status"]
    if not records:
        summary = [["note"], ["No credit-support filings on disk yet. Run python3 filings.py, then this command again."]]
        fills = {}
        evidence = [["note"], ["The quote gate is ready. It does not invent a term."]]
        evidence_fills = {}
    path = office.write_xlsx(OUT / "csa_terms_extracted.xlsx", [
        ("CSA_Terms", summary, fills),
        ("Evidence", evidence, evidence_fills),
    ])
    verified = sum(1 for record in records if record["status"] == "VERIFIED")
    print(f"term sheet {path} documents {len(by_doc)} verified fields {verified}")
    return path


def _book_rows():
    """Marked book. Yahoo is required. A failed price stops the pack."""
    return books.load_book()


def write_daily_pack():
    """One workbook: exposure, margin, limit, and the simulated peak for each name."""
    import tools
    rows = [["name", "id", "exposure_usd", "limit_usd", "utilization_pct", "status",
             "margin_action", "delivery_usd", "return_usd", "peak_pfe_usd", "pfe_status"]]
    for party in _book_rows():
        cid = party["counterparty_id"]
        exposure = tools.current_exposure(cid)
        margin = tools.calculate_margin_call(cid)
        future = tools.check_pfe_limit(cid)
        rows.append([
            party["name"], cid, exposure["current_exposure_usd"], exposure["credit_limit_usd"],
            exposure["utilization_pct"], exposure["status"], margin["action"],
            margin["delivery_amount_usd"], margin["return_amount_usd"],
            future["peak_pfe_collateralized_usd"], future["status"],
        ])
    path = office.write_xlsx(
        OUT / f"daily_pack_{date.today():%Y%m%d}.xlsx",
        [("Daily", rows, {})],
    )
    print(f"daily pack {path}")
    return path


def write_notices():
    """One Word notice per name that has a delivery or a return. Each file says it is a draft."""
    import tools
    written = []
    for party in _book_rows():
        margin = tools.calculate_margin_call(party["counterparty_id"])
        if margin["action"] == "NO_ACTION":
            continue
        amount = margin["delivery_amount_usd"] or margin["return_amount_usd"]
        direction = "deliver" if margin["action"] == "CALL_COUNTERPARTY" else "return"
        paragraphs = [
            "DRAFT - NOT SENT",
            f"Margin notice for {party['name']} ({party['counterparty_id']})",
            f"Date: {date.today().isoformat()}",
            f"Our calculation is a {direction} of USD {amount:,}.",
            f"Threshold USD {margin['threshold_usd']:,}. Minimum transfer USD {margin['mta_usd']:,}. Rounding USD {margin['rounding_usd']:,}.",
            "This notice is a draft for a person to review. It has not been sent.",
        ]
        path = office.write_docx(OUT / f"margin_notice_{party['counterparty_id']}_{date.today():%Y%m%d}.docx", paragraphs)
        written.append(path)
        print(f"notice {path}")
    if not written:
        print("no margin notice: every name is no_action")
    return written


def write_stress():
    """Workbook of the three stored shocks for every name."""
    import tools
    rows = [["name", "scenario", "pnl_usd", "shocked_exposure_usd"]]
    names = {party["counterparty_id"]: party["name"] for party in _book_rows()}
    for cid, name in names.items():
        for scenario in ("equity_down_20", "equity_down_40", "usd_spike"):
            result = tools.scenario_pnl(cid, scenario)
            rows.append([name, scenario, result["scenario_pnl_usd"], result["shocked_mtm_usd"]])
    path = office.write_xlsx(OUT / f"stress_{date.today():%Y%m%d}.xlsx", [("Stress", rows, {})])
    print(f"stress {path}")
    return path


def dispute_status(our_mtm, their_mtm, rounding):
    """AGREE when the gap is within the larger of the rounding step and 1% of our mark."""
    gap = abs(float(our_mtm) - float(their_mtm))
    tolerance = max(float(rounding), abs(float(our_mtm)) * 0.01)
    if gap <= tolerance:
        return "AGREE", gap, tolerance
    return "DISPUTE", gap, tolerance


def write_dispute_sheet():
    """Our mark on every name. Their mark is blank until a person types one. No dispute is filed."""
    rows = [["name", "id", "our_mtm_usd", "their_mtm_usd", "gap_usd", "tolerance_usd", "status", "note"]]
    for party in _book_rows():
        rows.append([
            party["name"], party["counterparty_id"], party["gross_mtm"], "", "", "",
            "NEEDS_THEIR_MARK", "Draft. Their figure is not on the book.",
        ])
    path = office.write_xlsx(OUT / f"dispute_{date.today():%Y%m%d}.xlsx", [("Dispute", rows, {})])
    print(f"dispute {path}")
    return path


def write_fan(counterparty_id=None):
    """SVG fan of the collateralised 50, 95, and 99 percent points. One file per name, or one name."""
    import tools
    written = []
    parties = _book_rows()
    if counterparty_id:
        parties = [party for party in parties if party["counterparty_id"] == counterparty_id]
    for party in parties:
        future = tools.check_pfe_limit(party["counterparty_id"])
        fan = future["fan"]
        if not fan:
            continue
        width, height = 640, 320
        values = [point["p99"] for point in fan] + [point["p50"] for point in fan]
        top = max(values) or 1
        def x_of(index):
            return 50 + int(index * (width - 80) / max(len(fan) - 1, 1))
        def y_of(value):
            return 30 + int((1 - value / top) * (height - 70))
        def poly(key):
            return " ".join(f"{x_of(i)},{y_of(point[key])}" for i, point in enumerate(fan))
        labels = "".join(
            f'<text x="{x_of(i)}" y="{height - 12}" font-size="11" text-anchor="middle">{point["step"]}</text>'
            for i, point in enumerate(fan)
        )
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="Helvetica, Arial, sans-serif">
<rect width="100%" height="100%" fill="#ffffff"/>
<text x="20" y="22" font-size="14" font-weight="700">{party["name"]} collateralised exposure fan</text>
<polyline fill="none" stroke="#94a3b8" stroke-width="2" points="{poly("p50")}"/>
<polyline fill="none" stroke="#0f766e" stroke-width="2" points="{poly("p95")}"/>
<polyline fill="none" stroke="#b45309" stroke-width="2" points="{poly("p99")}"/>
<text x="60" y="{height - 36}" font-size="11" fill="#94a3b8">p50</text>
<text x="110" y="{height - 36}" font-size="11" fill="#0f766e">p95</text>
<text x="160" y="{height - 36}" font-size="11" fill="#b45309">p99</text>
{labels}
</svg>'''
        path = OUT / f"fan_{party['counterparty_id']}.svg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)
        written.append(path)
        print(f"fan {path}")
    return written


def write_all():
    """Term sheet from whatever filings are on disk, then the five book files."""
    write_term_sheet()
    write_daily_pack()
    write_notices()
    write_stress()
    write_dispute_sheet()
    write_fan()


if __name__ == "__main__":
    write_all()
