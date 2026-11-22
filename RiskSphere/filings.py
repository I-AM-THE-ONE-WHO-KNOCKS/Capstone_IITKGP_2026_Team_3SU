"""Public credit-support filings, contracts, disclosures, and the BCBS text.

The counts we ask for are 500 credit-support exhibits, 300 derivative or
collateral contracts, and 150 disclosure excerpts, plus the BCBS-IOSCO margin
framework. A document is kept only when the text is long enough and actually
talks about collateral. A miss is skipped and remembered. The run can be
stopped and started again; files already saved are not downloaded twice.

SEC asks for a contact in the user agent. Set SEC_USER_AGENT before a long
run. HTML is stripped here. PDF text is taken with pdftotext.
"""

import gzip
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "filings"
CSA_DIR = DATA / "csa"
CON_DIR = DATA / "contracts"
DIS_DIR = DATA / "disclosures"
REG_DIR = DATA / "regulation"
REJECTED = DATA / "rejected_stems.txt"

CSA_TARGET = 500
CONTRACT_TARGET = 300
DISCLOSURE_TARGET = 150

SEC_SEARCH = "https://efts.sec.gov/LATEST/search-index"
PAUSE = 0.15

CSA_QUERIES = [
    '"credit support annex"',
    '"credit support annex" "eligible collateral" "valuation percentage"',
    '"minimum transfer amount" "threshold" "independent amount"',
]
CONTRACT_QUERIES = [
    '"ISDA Master Agreement" "Schedule" "collateral"',
    '"collateral agreement" "eligible collateral" "margin"',
    '"variation margin" "initial margin" "swap"',
    '"master netting agreement" "collateral"',
    '"credit support" "pledged collateral" "swap counterparty"',
]
DISCLOSURE_QUERIES = [
    '"additional collateral" "downgrade" "derivative"',
    '"credit rating" "collateral" "derivative" "triggers"',
    '"downgrade" "post additional collateral" "swap"',
]
CSA_MARKERS = [
    "credit support annex", "paragraph 13", "minimum transfer amount", "threshold",
    "eligible collateral", "valuation percentage", "independent amount",
    "delivery amount", "return amount", "eligible credit support",
]
COLLATERAL_TERMS = [
    "collateral", "margin", "credit support", "netting", "master agreement", "swap",
    "derivative", "threshold", "downgrade", "credit rating", "eligible", "haircut",
    "rehypothecat", "early termination", "event of default", "posted", "pledge",
]
TRIGGER_TERMS = [
    "downgrade", "credit rating", "ratings trigger", "rating trigger", "rating agenc",
    "below investment grade", "ratings-based", "rating-based",
]
BCBS_URLS = [
    "https://www.bis.org/publications/202004-standards-margin-requirements-non-centrally-cleared-derivatives.pdf",
    "https://www.bis.org/bcbs/publ/d499.pdf",
]


def _ua():
    """SEC user agent. A contact string belongs in the environment, not in code."""
    return os.environ.get("SEC_USER_AGENT", "RiskSphere research desk (set SEC_USER_AGENT to a contact email)")


def _get(url, params=None, timeout=60):
    """GET bytes. Raises on an HTTP error. Decodes gzip when the server sends it."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "User-Agent": _ua(),
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json,text/html,application/pdf,*/*",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw


def html_to_text(content):
    """Strip tags and scripts. Good enough for an exhibit, not a browser."""
    if isinstance(content, bytes):
        text = content.decode("utf-8", "ignore")
    else:
        text = content
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|h\d|li)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def pdf_to_text(content):
    """Extract text with pdftotext. Raises when the tool is missing or the PDF is empty."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(content)
        path = handle.name
    try:
        result = subprocess.run(
            ["pdftotext", "-q", "-layout", path, "-"],
            capture_output=True, check=False, timeout=60,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "ignore")[:200] or "pdftotext failed")
    return result.stdout.decode("utf-8", "ignore")


def csa_score(text):
    """How many credit-support markers appear. Five or more is a real annex."""
    lowered = text.lower()
    return sum(marker in lowered for marker in CSA_MARKERS)


def collateral_score(text):
    """How many collateral-contract markers appear. Below six is rejected."""
    lowered = text.lower()
    return sum(marker in lowered for marker in COLLATERAL_TERMS)


def _rejected():
    if not REJECTED.exists():
        return set()
    return set(REJECTED.read_text().split())


def _reject(stem):
    DATA.mkdir(parents=True, exist_ok=True)
    with REJECTED.open("a") as handle:
        handle.write(stem + "\n")


def _company_counts():
    counts = {}
    for folder in (CSA_DIR, CON_DIR, DIS_DIR):
        if not folder.exists():
            continue
        for meta_path in folder.glob("*.json"):
            try:
                url = json.loads(meta_path.read_text()).get("url", "")
            except json.JSONDecodeError:
                continue
            match = re.search(r"/data/(\d+)/", url)
            if match:
                cik = int(match.group(1))
                counts[cik] = counts.get(cik, 0) + 1
    return counts


def _search(query, offset=0, forms=None, year=None, size=100):
    params = {"q": query, "from": offset, "size": size}
    if forms:
        params["forms"] = forms
    if year:
        params["dateRange"] = "custom"
        params["startdt"] = f"{year}-01-01"
        params["enddt"] = f"{year}-12-31"
    payload = json.loads(_get(SEC_SEARCH, params))
    return payload.get("hits", {}).get("hits", [])


def _save(folder, stem, text, title, url):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.txt").write_text(text)
    (folder / f"{stem}.json").write_text(json.dumps({"title": title, "url": url}))


def _stem(adsh, fname):
    return re.sub(r"[^A-Za-z0-9]+", "_", f"{adsh}_{fname}")[:90]


def download_csas(n_docs=CSA_TARGET, pause=PAUSE, max_bytes=3_000_000):
    """Credit-support exhibits. Returns how many text files are in the CSA folder."""
    seen = {path.stem for path in CSA_DIR.glob("*.txt")} if CSA_DIR.exists() else set()
    for query in CSA_QUERIES:
        offset = 0
        while offset < 1000 and len(seen) < n_docs:
            try:
                hits = _search(query, offset)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                print(f"search failed ({query[:40]}, offset {offset}): {exc}")
                break
            if not hits:
                break
            offset += len(hits)
            time.sleep(pause)
            for hit in hits:
                if len(seen) >= n_docs:
                    break
                source = hit.get("_source", {})
                file_type = str(source.get("file_type", "")).upper()
                if file_type and not file_type.startswith("EX"):
                    continue
                try:
                    adsh, fname = hit["_id"].split(":", 1)
                    stem = _stem(adsh, fname)
                    if stem in seen:
                        continue
                    cik = int((source.get("ciks") or ["0"])[0])
                    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}"
                    raw = _get(url)
                    if len(raw) > max_bytes:
                        continue
                    text = pdf_to_text(raw) if fname.lower().endswith(".pdf") else html_to_text(raw)
                    if len(text) < 4000 or "credit support" not in text.lower() or csa_score(text) < 3:
                        continue
                    title = f"{(source.get('display_names') or ['?'])[0]} | {source.get('file_type', '')} | {source.get('file_date', '')}"
                    _save(CSA_DIR, stem, text, title, url)
                    seen.add(stem)
                    print(f"csa {len(seen)}/{n_docs} {stem}")
                    time.sleep(pause)
                except Exception as exc:
                    print("  skip:", str(exc)[:80])
    return len(list(CSA_DIR.glob("*.txt"))) if CSA_DIR.exists() else 0


def download_contracts(n_docs=CONTRACT_TARGET, per_company=3, pause=PAUSE, max_bytes=3_000_000):
    """Derivative and collateral contracts. A genuine annex is filed with the CSAs instead."""
    years = range(date.today().year, 2009, -1)
    already = len(list(CON_DIR.glob("*.txt"))) if CON_DIR.exists() else 0
    seen = set()
    for folder in (CSA_DIR, CON_DIR):
        if folder.exists():
            seen.update(path.stem for path in folder.glob("*.txt"))
    seen.update(_rejected())
    per_cik = _company_counts()
    added = 0
    for year in years:
        for query in CONTRACT_QUERIES:
            if already + added >= n_docs:
                break
            try:
                hits = _search(query, 0, year=year)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                print(f"contract search failed ({year}): {str(exc)[:60]}")
                continue
            time.sleep(pause)
            for hit in hits:
                if already + added >= n_docs:
                    break
                source = hit.get("_source", {})
                file_type = str(source.get("file_type", "")).upper()
                if file_type and not file_type.startswith("EX"):
                    continue
                try:
                    adsh, fname = hit["_id"].split(":", 1)
                    cik = int((source.get("ciks") or ["0"])[0])
                    stem = _stem(adsh, fname)
                    if per_cik.get(cik, 0) >= per_company or stem in seen:
                        continue
                    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}"
                    raw = _get(url)
                    if len(raw) > max_bytes:
                        _reject(stem)
                        seen.add(stem)
                        continue
                    text = pdf_to_text(raw) if fname.lower().endswith(".pdf") else html_to_text(raw)
                    score = csa_score(text)
                    if len(text) < (4000 if score >= 5 else 6000) or (score < 5 and collateral_score(text) < 6):
                        _reject(stem)
                        seen.add(stem)
                        continue
                    folder = CSA_DIR if score >= 5 else CON_DIR
                    title = f"{(source.get('display_names') or ['?'])[0]} | {source.get('file_type', '')} | {source.get('file_date', '')}"
                    _save(folder, stem, text, title, url)
                    seen.add(stem)
                    per_cik[cik] = per_cik.get(cik, 0) + 1
                    added += 1
                    print(f"contract {added}/{n_docs} {stem}")
                    time.sleep(pause)
                except Exception as exc:
                    print("  skip:", str(exc)[:70])
        if already + added >= n_docs:
            break
    stored = len(list(CON_DIR.glob("*.txt"))) if CON_DIR.exists() else 0
    print(f"contracts added {added}, stored {stored}")
    return stored


def _trigger_passages(text, max_chars=20000):
    """Paragraphs that mention collateral and a ratings trigger, plus their neighbours."""
    paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    hits = [
        index for index, paragraph in enumerate(paragraphs)
        if "collateral" in paragraph.lower() and any(term in paragraph.lower() for term in TRIGGER_TERMS)
    ]
    keep = sorted({neighbour for index in hits for neighbour in (index - 1, index, index + 1) if 0 <= neighbour < len(paragraphs)})
    chosen = []
    used = 0
    for index in keep:
        if used + len(paragraphs[index]) > max_chars:
            break
        chosen.append(paragraphs[index])
        used += len(paragraphs[index]) + 2
    return "\n\n".join(chosen), len(hits)


def download_disclosures(n_docs=DISCLOSURE_TARGET, per_company=2, pause=PAUSE, max_bytes=15_000_000):
    """Collateral and rating-trigger excerpts from Form 10-K filings. Not the whole filing."""
    years = range(date.today().year, 2012, -1)
    already = len(list(DIS_DIR.glob("*.txt"))) if DIS_DIR.exists() else 0
    seen = {path.stem for path in DIS_DIR.glob("*.txt")} if DIS_DIR.exists() else set()
    seen.update(_rejected())
    per_cik = _company_counts()
    added = 0
    for year in years:
        for query in DISCLOSURE_QUERIES:
            if already + added >= n_docs:
                break
            try:
                hits = _search(query, 0, forms="10-K", year=year)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                print(f"disclosure search failed ({year}): {str(exc)[:60]}")
                continue
            time.sleep(pause)
            for hit in hits:
                if already + added >= n_docs:
                    break
                source = hit.get("_source", {})
                try:
                    adsh, fname = hit["_id"].split(":", 1)
                    if not fname.lower().endswith((".htm", ".html")):
                        continue
                    cik = int((source.get("ciks") or ["0"])[0])
                    stem = _stem(adsh, fname)
                    if per_cik.get(cik, 0) >= per_company or stem in seen:
                        continue
                    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}"
                    raw = _get(url)
                    if len(raw) > max_bytes:
                        _reject(stem)
                        seen.add(stem)
                        continue
                    excerpt, n_hit = _trigger_passages(html_to_text(raw))
                    if n_hit < 2 or len(excerpt) < 1500:
                        _reject(stem)
                        seen.add(stem)
                        continue
                    name = (source.get("display_names") or ["?"])[0]
                    text = (
                        f"[Excerpt: collateral and credit-rating passages of the Form 10-K of {name}, "
                        f"filed {source.get('file_date', '')}]\n\n{excerpt}"
                    )
                    _save(DIS_DIR, stem, text, f"{name} | 10-K excerpt | {source.get('file_date', '')}", url)
                    seen.add(stem)
                    per_cik[cik] = per_cik.get(cik, 0) + 1
                    added += 1
                    print(f"disclosure {added}/{n_docs} {stem}")
                    time.sleep(pause)
                except Exception as exc:
                    print("  skip:", str(exc)[:70])
        if already + added >= n_docs:
            break
    stored = len(list(DIS_DIR.glob("*.txt"))) if DIS_DIR.exists() else 0
    print(f"disclosures added {added}, stored {stored}")
    return stored


def download_bcbs():
    """The BCBS margin framework (d499) and, if that URL fails, the SA-CCR note (d279).

    The file is kept only when the extracted text is long enough to be the paper.
    """
    REG_DIR.mkdir(parents=True, exist_ok=True)
    target = REG_DIR / "bcbs_iosco_margin.txt"
    if target.exists() and target.stat().st_size > 5000:
        print(f"regulation already stored, {target.stat().st_size} bytes")
        return target
    errors = []
    for url in BCBS_URLS:
        try:
            raw = _get(url, timeout=90)
            if not raw.startswith(b"%PDF"):
                raise ValueError("response is not a PDF")
            text = pdf_to_text(raw)
            if len(text) < 5000:
                raise ValueError("PDF text too short")
            target.write_text(text)
            (REG_DIR / "bcbs_iosco_margin.json").write_text(json.dumps({
                "title": "BCBS margin and counterparty-credit standards",
                "url": url,
            }))
            print(f"regulation {len(text)} chars from {url}")
            return target
        except Exception as exc:
            errors.append(f"{url}: {str(exc)[:80]}")
    print("regulation not downloaded: " + " | ".join(errors))
    return None


def counts():
    """How many text files sit in each folder."""
    return {
        "csa": len(list(CSA_DIR.glob("*.txt"))) if CSA_DIR.exists() else 0,
        "contracts": len(list(CON_DIR.glob("*.txt"))) if CON_DIR.exists() else 0,
        "disclosures": len(list(DIS_DIR.glob("*.txt"))) if DIS_DIR.exists() else 0,
        "regulation": len(list(REG_DIR.glob("*.txt"))) if REG_DIR.exists() else 0,
    }


def download_all(csa=CSA_TARGET, contracts=CONTRACT_TARGET, disclosures=DISCLOSURE_TARGET):
    """Fill the four folders up to the targets. Already-saved files count toward the target."""
    DATA.mkdir(parents=True, exist_ok=True)
    download_bcbs()
    have = counts()
    if have["csa"] < csa:
        download_csas(csa)
    if have["contracts"] < contracts:
        download_contracts(contracts)
    if have["disclosures"] < disclosures:
        download_disclosures(disclosures)
    done = counts()
    print("corpus", done)
    return done


if __name__ == "__main__":
    download_all()
