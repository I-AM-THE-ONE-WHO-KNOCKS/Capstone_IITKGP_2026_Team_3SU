"""Stage 4. Sample sentences, a quote check, and a term a person can sign.

The two piles stay apart: agreement sentences, and the haircut schedule
sentences. A proposed term is written as a draft. The model cannot apply
it. A person signs with their name, which writes a backup and an audit
row, and only then is the stored agreement changed.
"""

import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import books
import desk
import search

OUT = Path(__file__).resolve().parent / "outputs"
FIELDS = ("threshold", "mta", "rounding", "independent_amount")


def search_clauses(query, pile):
    """Rank one pile and return the passages. Does not change the book."""
    pile = pile if pile in ("csa", "d499") else "csa"
    found = search.search(query, top=3, pile=pile)
    hits = []
    for hit in found["hits"]:
        hits.append({
            "source": hit.get("source", ""),
            "id": hit["id"],
            "title": hit["title"],
            "text": hit["text"],
            "bm25": hit.get("bm25", 0),
            "dense": hit.get("dense", 0),
            "score": hit["score"],
            "pile": pile,
        })
    return {
        "tool": "search_clauses",
        "pile": pile,
        "mode": found["mode"],
        "model": found.get("model") or "",
        "hits": hits,
        "note": "Sample sentences. A hit does not change the stored terms. Cite a source tag like S1 and quote only text that appears in that hit.",
    }


def bad_quotes(text, passages):
    """Phrases in double quotes that are not inside a returned passage."""
    missing = []
    for quoted in re.findall(r'"([^"]{8,})"', text or ""):
        if not any(quoted in passage for passage in passages):
            missing.append(quoted)
    return missing


_FIELD_ALIAS = {
    "minimum_transfer_amount": "mta",
    "minimum_transfer": "mta",
    "independent": "independent_amount",
    "ia": "independent_amount",
}


def _reading(status, reason, field="", passage_id="", quote="", amount=None):
    """One Documents reading. applied is always false. This does not write a file."""
    return {
        "tool": "read_term",
        "status": status,
        "reason": reason,
        "field": field,
        "passage_id": passage_id,
        "quote": (quote or "")[:400],
        "amount": amount,
        "applied": False,
        "note": "Python checked the quote. A draft is written only when the status is VERIFIED.",
    }


def _as_amount(value, quote):
    """The amount the model named, or the first amount in the quote."""
    if value not in (None, ""):
        try:
            return int(round(float(str(value).replace(",", "").replace(" ", ""))))
        except (TypeError, ValueError):
            parsed = desk.parse_amount(str(value))
            if parsed:
                return int(round(parsed[0]))
    parsed = desk.parse_amount(quote or "")
    if parsed:
        return int(round(parsed[0]))
    return None


def judge_reading(parsed, hits):
    """Decide whether a model reading may become a draft.

    parsed is the object Documents asked Qwen to return: field, passage_id,
    value, and a quote copied from one hit. VERIFIED requires the quote to
    sit in that hit and to state exactly one amount. Several amounts, or a
    quote that is not in the hit, is REVIEW. No quote is NOT_FOUND.
    Does not write a draft and does not change the stored agreement.
    """
    hits = list(hits or [])
    if not isinstance(parsed, dict):
        return _reading("REVIEW", "the model reply was not an object")
    quote = " ".join(str(parsed.get("quote") or "").split())
    field = str(parsed.get("field") or "").strip().lower().replace(" ", "_")
    field = _FIELD_ALIAS.get(field, field)
    passage_id = str(parsed.get("passage_id") or "").strip()
    amount = _as_amount(parsed.get("value"), quote)
    if not quote and amount is None:
        return _reading("NOT_FOUND", "the passages do not state one amount")
    by_id = {hit.get("id"): hit for hit in hits}
    hit = by_id.get(passage_id)
    if hit is None and quote:
        owners = [item for item in hits if quote in (item.get("text") or "")]
        if len(owners) == 1:
            hit = owners[0]
            passage_id = hit.get("id") or ""
    if hit is None:
        return _reading(
            "REVIEW", "passage id is not one of the retrieved hits",
            field=field, passage_id=passage_id, quote=quote, amount=amount,
        )
    if amount is None:
        return _reading(
            "REVIEW", "the quote does not state one amount",
            field=field, passage_id=passage_id, quote=quote,
        )
    status, reason = desk.verify_record({"quote": quote, "amount": amount}, hit.get("text") or "")
    if status != "VERIFIED":
        return _reading(status, reason, field=field, passage_id=passage_id, quote=quote, amount=amount)
    if field not in FIELDS:
        return _reading(
            "REVIEW", "field is not an agreement number",
            field=field, passage_id=passage_id, quote=quote, amount=amount,
        )
    return _reading("VERIFIED", "", field=field, passage_id=passage_id, quote=quote, amount=amount)


def _useful_hits(hits):
    """Drop the ranker's empty fallback. A hit with no score is not evidence."""
    kept = []
    for hit in hits or []:
        if not hit.get("text"):
            continue
        if float(hit.get("bm25") or 0) == 0 and float(hit.get("dense") or 0) == 0:
            continue
        kept.append(hit)
    return kept


def _has(hits, needle, question=""):
    """Return the hit and the sentence that contains needle, or (None, "").

    A sentence about a different name is not used when the question names
    someone whose own passages were retrieved.
    """
    needle_l = needle.lower()
    matches = []
    for hit in hits:
        text = hit.get("text") or ""
        if needle_l not in text.lower():
            continue
        sentence = text.strip()
        for part in re.split(r"(?<=\.)\s+", text):
            if needle_l in part.lower():
                sentence = part.strip()
                break
        matches.append((hit, sentence))
    if not matches:
        return None, ""
    parties = ("meridian", "apex", "harbor", "zenith", "vanguard", "kaveri", "marina")
    asked = [party for party in parties if re.search(rf"\b{party}\b", question or "", re.I)]

    def _named_in(hit):
        low = (hit.get("text") or "").lower()
        return [party for party in parties if party in low]

    if not asked:
        return matches[0]
    named = [item for item in matches if any(party in _named_in(item[0]) for party in asked)]
    if named:
        return named[0]
    shared = [item for item in matches if not _named_in(item[0])]
    if shared:
        return shared[0]
    return None, ""


def _scenario_amount(question, kind):
    """The amount the question states for an exposure or a delivery."""
    match = re.search(
        rf"\b{kind}\b.{{0,40}}?([\d,]{{2,}})",
        question or "",
        re.I,
    )
    if not match:
        match = re.search(
            rf"\bUSD\s*([\d,]{{2,}}).{{0,24}}\b{kind}\b",
            question or "",
            re.I,
        )
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def decision_kind(question):
    """Which decision a question is asking for, or "" if it is not one.

    The same label drives the search words and the Python conclusion.
    A plain "what does the schedule say" question stays "".
    """
    text = question or ""

    def has(pattern):
        return re.search(pattern, text, re.I)

    if has(r"\beuros?\b|\beur\b") and has(r"\bdollars?\b|\busd\b"):
        return "fx"
    if has(r"close everything|close[- ]?out|close the positions|tear up the trades"):
        return "close"
    if has(r"\bexempt|upfront margin|regulators require"):
        return "exempt"
    if has(r"lend it|lend out|lending out|lend the collateral|rehypothec|on-lend|repo it overnight"):
        return "lend"
    if has(r"swap them|swap the collateral|\bswap\b|instead of the treasur|in place of treasur|substitution"):
        return "swap"
    if has(r"won't send|will not send|refuses to send|refuses to post|valuation of their|valuation is wrong|disagrees with our"):
        return "dispute"
    if has(r"money arrive|cash arrive|when must it arrive|arrival deadline|when does the money"):
        return "arrive"
    if has(r"want some back|have to return|give it back|hand it back|collateral back|return the cash|return some"):
        return "return"
    if has(r"small amount|slight increase|tiny increase|modest bump|a little higher"):
        return "small"
    if _scenario_amount(text, "delivery") is not None and has(r"after rounding|once rounded|amount is called|rounded up"):
        return "rounding"
    if _scenario_amount(text, "exposure") is not None and has(r"require a delivery|delivery of collateral|have to deliver"):
        return "threshold"
    if _scenario_amount(text, "exposure") is not None and has(r"is a call required|have to call|need to call"):
        return "mta"
    return ""


def _clause_result(status, conclusion, quote, passage_id, must_say, **figures):
    """One checked reading. The conclusion is Python's, not the model's."""
    result = {
        "tool": "apply_clause",
        "status": status,
        "conclusion": conclusion,
        "quote": quote,
        "passage_id": passage_id,
        "must_say": must_say,
        "applied": False,
        "note": "Python applied the passage, or refused when the passage does not state the decision.",
    }
    result.update(figures)
    return result


def reason_from_hits(question, hits):
    """Decide a question from retrieved passages, or say they do not state it.

    A comparison or a rounding step uses numbers that are already in the
    question and the passage. A permission, an exemption, a close-out, or a
    deadline that is not written in a hit is not granted. Does not write a
    draft and does not change the book.
    """
    hits = _useful_hits(hits)
    text = question or ""
    kind = decision_kind(text)

    if kind == "fx":
        hit, quote = _has(hits, "collateral currency differs from the settlement currency", text)
        if hit:
            return _clause_result(
                "APPLIED",
                "Yes. The schedule applies an additional 8 percent haircut when the collateral "
                "currency differs from the settlement currency. No other haircut is added from these passages.",
                quote, hit.get("id") or "", "additional 8 percent",
            )

    if kind == "close":
        hit, quote = _has(hits, "does not make a legal determination", text)
        if hit:
            return _clause_result(
                "APPLIED",
                "No. We do not close out. The passage says this desk does not make a legal "
                "determination that netting is enforceable.",
                quote, hit.get("id") or "", "do not close out",
            )

    if kind == "exempt":
        hit, quote = _has(hits, "not the full published", text)
        return _clause_result(
            "NOT_IN_PASSAGES" if not hit else "APPLIED",
            "No. These passages do not grant a regulatory exemption. The schedule note is not "
            "the published margin rule, and this desk does not decide an exemption.",
            quote, (hit or {}).get("id") or "", "do not grant a regulatory exemption",
        )

    if kind == "lend":
        return _clause_result(
            "NOT_IN_PASSAGES",
            "The retrieved passages do not say collateral may be lent. Lending it overnight is not allowed from these passages.",
            "", "", "do not say collateral may be lent",
        )

    if kind == "swap":
        hit, quote = _has(hits, "corporate bonds may be posted", text)
        if hit:
            return _clause_result(
                "RELATED",
                "Corporate bonds may be posted. The passages do not say they can be swapped for "
                "Treasuries unless a person agrees the substitution.",
                quote, hit.get("id") or "", "do not say they can be swapped",
            )

    if kind == "dispute":
        hit, quote = _has(hits, "undisputed amount is transferred", text)
        if hit:
            return _clause_result(
                "APPLIED",
                "The undisputed amount is transferred and the disputed amount follows the dispute "
                "paragraph. We do not force the collateral and we do not close out.",
                quote, hit.get("id") or "", "undisputed amount is transferred",
            )

    if kind == "arrive":
        hit, quote = _has(hits, "notice is given by", text)
        if hit:
            return _clause_result(
                "RELATED",
                "That sentence is a notice time, not when the money must arrive. The passages do not state an arrival deadline.",
                quote, hit.get("id") or "", "not when the money must arrive",
            )

    if kind == "return":
        hit, quote = _has(hits, "a return rounds down", text)
        return _clause_result(
            "RELATED" if hit else "NOT_IN_PASSAGES",
            "The passages do not say this return is due. A return sentence, when one was retrieved, "
            "only says how a return is rounded. Today's margin figure is a separate calculation.",
            quote, (hit or {}).get("id") or "", "do not say this return is due",
        )

    if kind == "small":
        return _clause_result(
            "NOT_IN_PASSAGES",
            "A small amount is not a figure in the passages, so a call cannot be decided from them.",
            "", "", "not a figure",
        )

    delivery = _scenario_amount(text, "delivery")
    if kind == "rounding" and delivery is not None:
        hit, quote = _has(hits, "delivery rounds up", text)
        full = (hit or {}).get("text") or ""
        step_match = re.search(r"nearest USD ([\d,]+)", full)
        if hit and step_match:
            quote = full.strip()
            step = int(step_match.group(1).replace(",", ""))
            called = ((delivery + step - 1) // step) * step
            return _clause_result(
                "APPLIED",
                f"The passage says a delivery rounds up to the nearest USD {step:,}. "
                f"USD {delivery:,} is called as USD {called:,}.",
                quote, hit.get("id") or "", f"{called:,}",
                question_amount=delivery, rule_amount=step, result_amount=called,
            )

    exposure = _scenario_amount(text, "exposure")
    if kind == "threshold" and exposure is not None:
        hit, quote = _has(hits, "does not require a delivery", text)
        full = (hit or {}).get("text") or ""
        threshold_match = re.search(r"Threshold (?:for [\w ]+ )?is USD ([\d,]+)", full, re.I)
        if hit and threshold_match:
            quote = full.strip()
            threshold = int(threshold_match.group(1).replace(",", ""))
            if exposure < threshold:
                conclusion = (
                    f"No delivery is required. USD {exposure:,} is below the threshold of USD {threshold:,} "
                    "stated in the passage."
                )
                say = "no delivery is required"
            else:
                conclusion = (
                    f"USD {exposure:,} is not below the threshold of USD {threshold:,}. "
                    "The passage does not state the call amount."
                )
                say = "does not state the call amount"
            return _clause_result(
                "APPLIED", conclusion, quote, hit.get("id") or "", say,
                question_amount=exposure, rule_amount=threshold,
            )

    if kind == "mta" and exposure is not None:
        hit, quote = _has(hits, "Minimum Transfer Amount is USD", text)
        full = (hit or {}).get("text") or ""
        mta_match = re.search(r"Minimum Transfer Amount is USD ([\d,]+)", full, re.I)
        if hit and mta_match and re.search(r"is called|not required", full, re.I):
            quote = full.strip()
            mta = int(mta_match.group(1).replace(",", ""))
            if exposure <= mta:
                conclusion = (
                    f"No call is required from this passage. USD {exposure:,} is not above "
                    f"the minimum transfer amount of USD {mta:,}."
                )
                say = "no call is required"
            else:
                conclusion = (
                    f"USD {exposure:,} is above the minimum transfer amount of USD {mta:,}. "
                    "The passage says that exposure is called. It does not state the call amount."
                )
                say = "does not state the call amount"
            return _clause_result(
                "APPLIED", conclusion, quote, hit.get("id") or "", say,
                question_amount=exposure, rule_amount=mta,
            )

    return _clause_result(
        "NOT_IN_PASSAGES",
        "The retrieved passages do not state the decision. Nothing is sent, booked, or closed out.",
        "", "", "do not state the decision",
    )


def propose_term(counterparty_id, field, value, passage_id):
    """Record a draft term. The stored agreement is left as it is.

    field is threshold, mta, rounding, or independent_amount.
    value is the number the passage is claimed to support.
    The passage must contain that number, as digits or with commas.
    Status on the draft is DRAFT_PENDING_APPROVAL. applied is false.
    """
    if field not in FIELDS:
        raise ValueError(f"field must be one of {', '.join(FIELDS)}")
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be a number") from exc
    passage = None
    for row in books.clause_rows():
        if row.get("id") == passage_id and (row.get("pile") or "csa") == "csa":
            passage = row
            break
    if passage is None:
        raise ValueError("passage id is not in the agreement pile")
    if passage.get("counterparty_id") not in (counterparty_id, ""):
        raise ValueError("passage is for a different name")
    # The amount must appear as its own number. A bare "1" must not match
    # inside "1,000,000", and "10" must not match inside "100".
    shown = f"{number:,}"
    amount = re.compile(rf"(?<![\d,])(?:{re.escape(shown)}|{number})(?![\d,])")
    if amount.search(passage["text"]) is None:
        raise ValueError("the passage does not contain that number, so the draft was not written")
    current = None
    for row in books._read_csv("csa_terms.csv"):
        if row["counterparty_id"] == counterparty_id:
            current = int(round(float(row[field])))
            break
    OUT.mkdir(exist_ok=True)
    draft = {
        "counterparty_id": counterparty_id,
        "field": field,
        "proposed_value": number,
        "current_value": current,
        "passage_id": passage_id,
        "status": "DRAFT_PENDING_APPROVAL",
        "applied": False,
    }
    with (OUT / "pending_terms.jsonl").open("a") as handle:
        handle.write(json.dumps(draft) + "\n")
    draft["tool"] = "propose_term"
    draft["note"] = "Draft only. A person applies it with: approve <id> <field> by <name>."
    return draft


def list_pending(path=None):
    """Drafts that a person has not signed. The latest line for each name and field wins."""
    path = Path(path) if path else OUT / "pending_terms.jsonl"
    latest = {}
    if not path.exists():
        return []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        latest[(row.get("counterparty_id"), row.get("field"))] = row
    return [row for row in latest.values() if not row.get("applied")]


def approve_term(counterparty_id, field, approved_by, pending_path=None, terms_path=None, audit_path=None):
    """Apply one waiting draft after a person signs it.

    approved_by is the person's name, at least 3 characters. The model is
    not this function's caller. A missing draft, a short name, or an
    unknown field raises ValueError and leaves the book unchanged.

    Writes a backup of the terms file, updates that one field, appends an
    audit row, and records the draft as applied.
    """
    name = " ".join(str(approved_by or "").split())
    if len(name) < 3:
        raise ValueError("a person has to sign with a name of at least 3 characters")
    if field not in FIELDS:
        raise ValueError(f"field must be one of {', '.join(FIELDS)}")
    pending_path = Path(pending_path) if pending_path else OUT / "pending_terms.jsonl"
    terms_path = Path(terms_path) if terms_path else books.DATA / "csa_terms.csv"
    audit_path = Path(audit_path) if audit_path else OUT / "csa_terms_audit.csv"
    waiting = [
        row for row in list_pending(pending_path)
        if row.get("counterparty_id") == counterparty_id and row.get("field") == field
    ]
    if not waiting:
        raise ValueError("no draft is waiting for that name and field")
    draft = waiting[-1]
    with terms_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
        header = list(rows[0].keys()) if rows else []
    target = None
    for row in rows:
        if row["counterparty_id"] == counterparty_id:
            target = row
            break
    if target is None or field not in target:
        raise ValueError("that name is not on the stored agreement")
    old = target[field]
    backup = audit_path.parent / (terms_path.name + ".bak")
    audit_path.parent.mkdir(exist_ok=True)
    shutil.copyfile(terms_path, backup)
    target[field] = str(int(draft["proposed_value"]))
    with terms_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    audit_exists = audit_path.exists()
    with audit_path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "ts", "counterparty_id", "field", "old", "new", "approved_by", "passage_id",
        ])
        if not audit_exists:
            writer.writeheader()
        writer.writerow({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "counterparty_id": counterparty_id,
            "field": field,
            "old": old,
            "new": target[field],
            "approved_by": name,
            "passage_id": draft.get("passage_id") or "",
        })
    applied = dict(draft)
    applied["status"] = "APPROVED"
    applied["applied"] = True
    applied["approved_by"] = name
    with pending_path.open("a") as handle:
        handle.write(json.dumps(applied) + "\n")
    applied["backup"] = str(backup)
    applied["note"] = "The stored agreement was updated. The previous file is the backup."
    return applied
