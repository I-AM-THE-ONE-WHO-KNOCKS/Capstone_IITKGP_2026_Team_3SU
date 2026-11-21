"""Turn downloaded documents into passages for the document search.

A source is downloaded once into data/docs/raw/, its SHA-256 is checked against
the pinned value, and its text is cut into short passages written to
data/docs/index/<id>.json. Both folders are gitignored: the BIS licence allows
brief excerpts with the source stated, not the whole document in a public
repository. books.clause_rows() adds any index it finds to the passages in
data/clauses.json.

Run `python3 scripts/fetch_documents.py` to download and index. Reading a PDF
needs pypdf (requirements-dev.txt). Searching the index afterwards does not.
"""

import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "docs" / "raw"
INDEX_DIR = ROOT / "data" / "docs" / "index"
MAX_CHARS = 600

SOURCES = [
    {
        "id": "d499",
        "url": "https://www.bis.org/publications/202004-standards-margin-requirements-non-centrally-cleared-derivatives.pdf",
        "sha256": "3ba9da23ae55c5dc4ad482e06eb084ae9c9cae7400a83f5778e986dae490ece6",
        "pile": "haircut_schedule",
        "title": "BCBS-IOSCO d499",
        "source": ("BCBS-IOSCO, Margin requirements for non-centrally cleared derivatives (d499, April 2020), "
                   "PDF {pages}. (c) Bank for International Settlements 2020. Local copy, not committed"),
        # The running header or footer on each page, with the printed page number beside it.
        "page_header": r"^\s*(\d+\s+)?Margin requirements for non-centrally cleared derivatives(\s+\d+)?\s*$",
    },
]

# A sentence ends at ". ", "; ", ": ", "! " or "? " when a capital letter, an opening bracket,
# a digit or a quotation mark follows.
_SENTENCE_END = re.compile(r"(?<=[.;:!?])\s+(?=[A-Z(\d\"\u201c\u2022])")
_DOT_LEADER = re.compile(r"\.{6,}")  # table of contents lines

# Abbreviations whose full stop does not end a sentence. Only these are protected; a single
# capital letter is not, because "Appendix B. Standardised" really does end a sentence.
_ABBREVIATIONS = ("e.g", "i.e", "U.S", "U.K", "vs", "No", "Art", "Fig", "Eq", "cf", "approx",
                  "Inc", "Ltd", "Corp", "Co", "Dr", "Mr", "Ms", "St", "al")
_ABBREVIATION = re.compile(r"\b(?:" + "|".join(re.escape(a) for a in _ABBREVIATIONS) + r")\.")
# A line that ends a sentence or a table row, followed by a line that starts one.
_LINE_ENDS = re.compile(r"[.;:!?)\d]$")
_LINE_STARTS = re.compile(r"[A-Z(\d\u2022]")
_ENDS_SENTENCE = re.compile(r"[.;:!?)\d%\"\u201d]$")


def clean_page(text, header_pattern):
    """Drop the running header and blank lines. Join wrapped lines with a space.

    A line break is kept as "\\n" where a sentence or a table row plainly ends:
    the line before ends with . ; : ! ? ) or a digit, and the next starts with a capital
    letter, a bracket or a digit. Those breaks are hard boundaries for split_sentences.
    """
    header = re.compile(header_pattern)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()
             if ln.strip() and not header.match(ln)]
    out = ""
    for line in lines:
        if not out:
            out = line
        elif _LINE_ENDS.search(out) and _LINE_STARTS.match(line):
            out += "\n" + line
        else:
            out += " " + line
    return out


def split_sentences(text):
    """Split one cleaned page into sentences, or into table rows where no punctuation ends them.

    Protected abbreviations keep their full stop. Each "\\n" from clean_page is a boundary.
    Returns non-empty strings with no line breaks inside.
    """
    protected = _ABBREVIATION.sub(lambda m: m.group(0).replace(".", "\x00"), text)
    sentences = []
    for block in protected.split("\n"):
        for piece in _SENTENCE_END.split(block):
            piece = piece.replace("\x00", ".").strip()
            if piece:
                sentences.append(piece)
    return sentences


def chunk_pages(pages, doc_id, max_chars=MAX_CHARS):
    """Cut cleaned page texts into passages of whole sentences, at most max_chars where possible.

    A sentence that runs over a page break is joined to its continuation on the next page, so a
    passage does not start or end in the middle of a sentence. A passage may then span two pages;
    it records both, and its id names the first. A sentence longer than max_chars becomes a passage
    of its own. Contents lines (dot leaders) are removed before grouping, so they never join real text.
    Returns [{"id", "page", "last_page", "text"}].
    """
    sentences = []  # [first page, last page, text]
    for page_no, text in enumerate(pages, 1):
        pieces = [p for p in split_sentences(text) if not _DOT_LEADER.search(p)] if text else []
        if not pieces:
            continue
        if sentences and not _ENDS_SENTENCE.search(sentences[-1][2]) and pieces[0][:1].islower():
            sentences[-1][1] = page_no
            sentences[-1][2] += " " + pieces.pop(0)
        sentences.extend([page_no, page_no, piece] for piece in pieces)
    out, current = [], None
    for first, last, sentence in sentences:
        if current and len(current[2]) + 1 + len(sentence) <= max_chars:
            current[1] = last
            current[2] += " " + sentence
            continue
        if current:
            out.append(current)
        current = [first, last, sentence]
    if current:
        out.append(current)
    passages, counter = [], {}
    for first, last, text in out:
        if _DOT_LEADER.search(text) or len(text) < 40:
            continue
        counter[first] = counter.get(first, 0) + 1
        passages.append({"id": f"{doc_id}-p{first}-{counter[first]}", "page": first, "last_page": last, "text": text})
    return passages


def to_clauses(passages, source):
    """The passage rows in the shape of data/clauses.json, so search treats them the same way."""
    rows = []
    for p in passages:
        last = p.get("last_page", p["page"])
        pages = f"page {p['page']}" if last == p["page"] else f"pages {p['page']}-{last}"
        rows.append({
            "id": p["id"],
            "counterparty_id": "",
            "pile": source["pile"],
            "source": source["source"].format(pages=pages),
            "title": f"{source['title']} {pages}",
            "text": p["text"],
        })
    return rows


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch(source, raw_dir=RAW_DIR):
    """Download one source unless a copy with the pinned hash is already there. Returns the path.

    Raises RuntimeError when the downloaded file does not have the pinned SHA-256, and
    deletes it, so a changed or substituted document is never indexed.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{source['id']}.pdf"
    if path.exists() and sha256(path) == source["sha256"]:
        return path
    request = urllib.request.Request(source["url"], headers={"User-Agent": "RiskSphere-document-fetch"})
    with urllib.request.urlopen(request, timeout=60) as response:
        path.write_bytes(response.read())
    got = sha256(path)
    if got != source["sha256"]:
        path.unlink()
        raise RuntimeError(f"{source['id']}: SHA-256 {got} does not match the pinned {source['sha256']}")
    return path


def read_pdf_pages(path):
    """Text of each page, via pypdf. Imported here so the app itself needs no package."""
    import pypdf

    return [page.extract_text() or "" for page in pypdf.PdfReader(str(path)).pages]


def build_index(source, raw_dir=RAW_DIR, index_dir=INDEX_DIR):
    """Download, check, extract, chunk, and write data/docs/index/<id>.json. Returns the row count."""
    path = fetch(source, raw_dir)
    pages = [clean_page(t, source["page_header"]) for t in read_pdf_pages(path)]
    rows = to_clauses(chunk_pages(pages, source["id"]), source)
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / f"{source['id']}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    return len(rows)


def load_index(index_dir=INDEX_DIR):
    """Every indexed passage, or [] when nothing has been fetched yet."""
    rows = []
    if index_dir.is_dir():
        for path in sorted(index_dir.glob("*.json")):
            rows.extend(json.loads(path.read_text()))
    return rows
