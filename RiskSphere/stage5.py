"""Stage 5. Alerts and draft files. Nothing is sent.

A draft is marked DRAFT_PENDING_APPROVAL. The console does not move
collateral and does not deliver a notice.
"""

import csv
import hashlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path

import books
import stage3

OUT = Path(__file__).resolve().parent / "outputs"
DATA = Path(__file__).resolve().parent / "data"
FILES = (
    "counterparties.csv",
    "trades.csv",
    "collateral.csv",
    "csa_terms.csv",
    "limits.csv",
    "credit.csv",
)


def _run_id():
    return datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _hashes():
    found = {}
    for name in FILES:
        path = DATA / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        found[name] = digest
    return found


def _age_days(asof):
    day = datetime.strptime(asof, "%Y-%m-%d").date()
    return (date.today() - day).days


def list_alerts(book, stale_after_days):
    """Breach, collateral shortfall, and a price older than the stale window.

    book is the marked book. stale_after_days is an integer. Returns a
    result dict. Does not send anything.
    """
    alerts = []
    for party in book:
        if party["status"] in ("BREACH", "WARNING"):
            alerts.append({
                "code": party["status"],
                "counterparty_id": party["counterparty_id"],
                "detail": (
                    f"{party['name']} utilization {party['utilization_pct']}% "
                    f"on today's exposure"
                ),
            })
        cover = stage3.collateral_coverage(party)
        if cover["shortfall_usd"] > 0:
            alerts.append({
                "code": "SHORTFALL",
                "counterparty_id": party["counterparty_id"],
                "detail": f"{party['name']} collateral shortfall {cover['shortfall_usd']}",
            })
        for trade in party["trades"]:
            age = _age_days(trade["asof"])
            if age > stale_after_days:
                alerts.append({
                    "code": "STALE_PRICE",
                    "counterparty_id": party["counterparty_id"],
                    "detail": f"{trade['symbol']} close is {age} days old, as of {trade['asof']}",
                })
    return {
        "tool": "list_alerts",
        "alerts": alerts,
        "alert_count": len(alerts),
        "stale_after_days": stale_after_days,
    }


def _write(name, text):
    OUT.mkdir(exist_ok=True)
    path = OUT / name
    path.write_text(text)
    return str(path)


def draft_report(party, cfg, include_pfe, pfe_result):
    """One text report for one name. Status is a draft.

    include_pfe is true only when a future-exposure result was computed
    for this same call. The file records the run id, input hashes, formula
    names, seed, path count, and warnings.
    """
    cover = stage3.collateral_coverage(party)
    el = stage3.expected_loss(
        party,
        party["credit_exposure"],
        "current exposure, not regulatory EAD",
    )
    alerts = list_alerts([party], int(cfg.get("stale_after_days", 5)))
    run_id = _run_id()
    warnings = []
    if party["status"] == "BREACH":
        warnings.append("today's exposure is over the stored limit")
    if cover["shortfall_usd"] > 0:
        warnings.append("collateral shortfall is above zero")
    if cover["ineligible_assets"]:
        warnings.append("ineligible collateral was excluded: " + ", ".join(cover["ineligible_assets"]))
    if not include_pfe:
        warnings.append("this report did not resimulate future exposure")
    lines = [
        "DRAFT_PENDING_APPROVAL",
        f"run_id: {run_id}",
        f"name: {party['name']}",
        "formulas: mtm = quantity x (price - entry); exposure = max(gross - eligible collateral, 0); "
        "credit support = max(0, gross + independent amount - threshold); "
        "shortfall = max(credit support - eligible collateral, 0); "
        "expected loss = PD x LGD x EAD",
        f"pfe_seed: {cfg.get('pfe_seed')}",
        f"pfe_paths: {cfg.get('pfe_paths')}",
        f"pfe_included: {str(bool(include_pfe)).lower()}",
        "input_hashes: " + json.dumps(_hashes()),
        f"gross_mtm_usd: {party['gross_mtm']}",
        f"current_exposure_usd: {party['credit_exposure']}",
        f"limit_usd: {party['credit_limit']}",
        f"utilization_pct: {party['utilization_pct']}",
        f"status: {party['status']}",
        f"margin_action: {party['action']}",
        f"delivery_usd: {party['delivery_amount']}",
        f"return_usd: {party['return_amount']}",
        f"shortfall_usd: {cover['shortfall_usd']}",
        f"surplus_usd: {cover['surplus_usd']}",
        f"expected_loss_usd: {el['expected_loss_usd']}",
        f"ead_label: {el['ead_label']}",
        f"alert_count: {alerts['alert_count']}",
    ]
    if pfe_result:
        lines.append(f"peak_pfe_collateralized_usd: {pfe_result['peak_pfe_collateralized_usd']}")
        lines.append(f"pfe_status: {pfe_result['status']}")
    if warnings:
        lines.append("warnings: " + "; ".join(warnings))
    lines.append("Nothing in this file was sent or booked.")
    path = _write(f"report_{run_id}.txt", "\n".join(lines) + "\n")
    return {
        "tool": "draft_report",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "run_id": run_id,
        "status": "DRAFT_PENDING_APPROVAL",
        "path": path,
        "current_exposure_usd": party["credit_exposure"],
        "exposure_status": party["status"],
        "shortfall_usd": cover["shortfall_usd"],
        "expected_loss_usd": el["expected_loss_usd"],
        "alert_count": alerts["alert_count"],
        "pfe_seed": cfg.get("pfe_seed"),
        "pfe_paths": cfg.get("pfe_paths"),
        "include_pfe": bool(include_pfe),
        "warnings": warnings,
        "note": "Draft report. Nothing was sent.",
    }


def draft_margin_notice(party):
    """A margin notice marked as a draft. It is not delivered."""
    run_id = _run_id()
    text = "\n".join([
        "DRAFT_PENDING_APPROVAL",
        f"run_id: {run_id}",
        f"to: {party['name']}",
        "This is a draft notice. It has not been sent.",
        f"action: {party['action']}",
        f"net_mark_usd: {party['gross_mtm']}",
        f"threshold_usd: {party['threshold']}",
        f"credit_support_usd: {party['credit_support_amount']}",
        f"collateral_after_haircut_usd: {party['collateral_value']}",
        f"delivery_usd: {party['delivery_amount']}",
        f"return_usd: {party['return_amount']}",
        "A person still has to review this before any collateral moves.",
    ]) + "\n"
    path = _write(f"notice_{run_id}.txt", text)
    return {
        "tool": "draft_margin_notice",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "run_id": run_id,
        "status": "DRAFT_PENDING_APPROVAL",
        "action": party["action"],
        "delivery_amount_usd": party["delivery_amount"],
        "return_amount_usd": party["return_amount"],
        "path": path,
        "note": "Draft only. The notice was not sent.",
    }


def draft_stress_book(parties, scenarios):
    """Write a draft stress table. scenarios is the stored shock map."""
    run_id = _run_id()
    path = OUT / f"stress_{run_id}.csv"
    OUT.mkdir(exist_ok=True)
    rows = []
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "status", "run_id", "counterparty_id", "scenario", "scenario_pnl_usd",
            "base_mtm_usd", "shocked_mtm_usd",
        ])
        for party in parties:
            for name, spec in scenarios.items():
                result = stage3.scenario_pnl(party, name, spec["shocks"], spec["label"])
                writer.writerow([
                    "DRAFT_PENDING_APPROVAL",
                    run_id,
                    party["counterparty_id"],
                    name,
                    result["scenario_pnl_usd"],
                    result["base_mtm_usd"],
                    result["shocked_mtm_usd"],
                ])
                rows.append({
                    "counterparty_id": party["counterparty_id"],
                    "scenario": name,
                    "scenario_pnl_usd": result["scenario_pnl_usd"],
                })
    return {
        "tool": "draft_stress_book",
        "status": "DRAFT_PENDING_APPROVAL",
        "run_id": run_id,
        "path": str(path),
        "rows": rows,
        "note": "Draft stress book. Equity and FX shocks from the stored list. Nothing was sent.",
    }


def draft_dispute(party, their_mtm):
    """Compare our mark with a figure the question already stated.

    their_mtm is that stated figure. The gap is our mark minus theirs.
    Within the rounding step the row is AGREE. Within the minimum transfer
    it is PARTIAL_AGREE. Otherwise it is DISPUTE. The row is still a draft.
    """
    ours = party["gross_mtm"]
    theirs = int(round(float(their_mtm)))
    gap = ours - theirs
    size = abs(gap)
    if size <= party["rounding"]:
        verdict = "AGREE"
        disputed = 0
    elif size <= party["mta"]:
        verdict = "PARTIAL_AGREE"
        disputed = size
    else:
        verdict = "DISPUTE"
        disputed = size
    run_id = _run_id()
    path = OUT / f"dispute_{run_id}.csv"
    OUT.mkdir(exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "status", "run_id", "counterparty_id", "our_mtm_usd", "their_mtm_usd",
            "gap_usd", "verdict", "disputed_usd",
        ])
        writer.writerow([
            "DRAFT_PENDING_APPROVAL", run_id, party["counterparty_id"],
            ours, theirs, gap, verdict, disputed,
        ])
    return {
        "tool": "draft_dispute",
        "counterparty_id": party["counterparty_id"],
        "counterparty_name": party["name"],
        "status": "DRAFT_PENDING_APPROVAL",
        "run_id": run_id,
        "our_mtm_usd": ours,
        "their_mtm_usd": theirs,
        "gap_usd": gap,
        "verdict": verdict,
        "disputed_usd": disputed,
        "path": str(path),
        "note": "Draft dispute row. Nothing was agreed or sent.",
    }
