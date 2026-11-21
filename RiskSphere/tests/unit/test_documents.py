"""Real documents for the search: download check, cleaning, chunking, and loading.

The PDF and its index are gitignored (the BIS licence allows brief excerpts only),
so these tests use small inputs. The last test runs only where
scripts/fetch_documents.py has been run, and checks the real index.
"""

import io
import json

import pytest

import agent
import books
import documents
import search

HEADER = documents.SOURCES[0]["page_header"]


def test_clean_page_drops_running_headers_and_joins_lines():
    page = "\n26 Margin requirements for non-centrally cleared derivatives\n \nAppendix B\nStandardised  haircut schedule\n"
    assert documents.clean_page(page, HEADER) == "Appendix B Standardised haircut schedule"
    page = "Margin requirements for non-centrally cleared derivatives 5\nMonitoring and evaluation"
    assert documents.clean_page(page, HEADER) == "Monitoring and evaluation"


def test_clean_page_keeps_a_sentence_that_only_mentions_the_title():
    page = "The report Margin requirements for non-centrally cleared derivatives was published in 2015."
    assert documents.clean_page(page, HEADER) == page


def _sentences(n, words=12):
    return " ".join(f"Sentence {i} " + "word " * words + "ends here." for i in range(n))


def test_chunks_are_whole_sentences_within_the_limit_and_never_cross_a_page():
    pages = [_sentences(12), _sentences(5)]
    chunks = documents.chunk_pages(pages, "doc", max_chars=200)
    assert {c["page"] for c in chunks} == {1, 2}
    for c in chunks:
        assert len(c["text"]) <= 200
        assert c["text"].endswith("ends here.")
    joined = " ".join(c["text"] for c in chunks if c["page"] == 1)
    assert joined == pages[0]  # nothing lost, nothing reordered


def test_a_sentence_longer_than_the_limit_is_kept_whole():
    long = "A " + "very " * 200 + "long sentence."
    chunks = documents.chunk_pages([long], "doc", max_chars=100)
    assert [c["text"] for c in chunks] == [long]


def test_table_of_contents_lines_are_never_indexed():
    pages = ["Appendix B: Standardised haircut schedule ........................ 26", "Short.", _sentences(1)]
    chunks = documents.chunk_pages(pages, "doc")
    assert chunks and all("...." not in c["text"] for c in chunks)
    assert "Sentence 0 word" in chunks[0]["text"]  # the real text on page 3 is kept


def test_a_sentence_that_runs_over_a_page_break_is_one_passage():
    pages = ["The margin requirements apply to all covered entities and the", "initial margin collected must be segregated."]
    [chunk] = documents.chunk_pages(pages, "doc")
    assert chunk["text"] == "The margin requirements apply to all covered entities and the initial margin collected must be segregated."
    assert (chunk["page"], chunk["last_page"]) == (1, 2)


def test_a_capitalised_start_on_the_next_page_is_a_new_sentence():
    pages = ["The rule applies to all entities and the", "Committee will review the whole framework in 2027."]
    chunks = documents.chunk_pages(pages, "doc", max_chars=45)
    assert [c["text"] for c in chunks] == [
        "The rule applies to all entities and the",
        "Committee will review the whole framework in 2027.",
    ]


def test_bullet_items_are_separate_sentences():
    text = "The scope covers the following:\n• the extent of the rules applied\n• the entities that are covered"
    pieces = documents.split_sentences(text)
    assert pieces == ["The scope covers the following:", "• the extent of the rules applied", "• the entities that are covered"]


def test_citation_names_a_page_range_when_a_passage_spans_two_pages():
    rows = documents.to_clauses([{"id": "d-p1-1", "page": 1, "last_page": 2, "text": "x."}], documents.SOURCES[0])
    assert "PDF pages 1-2" in rows[0]["source"]
    rows = documents.to_clauses([{"id": "d-p3-1", "page": 3, "last_page": 3, "text": "x."}], documents.SOURCES[0])
    assert "PDF page 3." in rows[0]["source"]


def test_chunk_ids_are_unique_and_name_the_page():
    chunks = documents.chunk_pages([_sentences(30), _sentences(30)], "d499", max_chars=150)
    ids = [c["id"] for c in chunks]
    assert len(ids) == len(set(ids))
    assert ids[0] == "d499-p1-1"


def test_passages_take_the_shape_of_clauses_json_with_a_page_citation():
    rows = documents.to_clauses([{"id": "d499-p30-1", "page": 30, "text": "Gold 15."}], documents.SOURCES[0])
    assert set(rows[0]) == {"id", "counterparty_id", "pile", "source", "title", "text"}
    assert rows[0]["pile"] == "haircut_schedule" and rows[0]["counterparty_id"] == ""
    assert "PDF page 30" in rows[0]["source"] and "Bank for International Settlements" in rows[0]["source"]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_download_with_the_wrong_hash_is_deleted_and_refused(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Response(b"not the real pdf"))
    with pytest.raises(RuntimeError, match="does not match"):
        documents.fetch(documents.SOURCES[0], raw_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_a_download_with_the_right_hash_is_kept(tmp_path, monkeypatch):
    body = b"pinned bytes"
    source = dict(documents.SOURCES[0], sha256=__import__("hashlib").sha256(body).hexdigest())
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Response(body))
    path = documents.fetch(source, raw_dir=tmp_path)
    assert path.read_bytes() == body


def test_no_index_means_only_clauses_json(tmp_path):
    assert documents.load_index(tmp_path / "missing") == []
    assert len(books.clause_rows()) == len(json.loads((books.DATA / "clauses.json").read_text()))


def _write_index(tmp_path, monkeypatch, rows):
    index = tmp_path / "index"
    index.mkdir()
    (index / "d499.json").write_text(json.dumps(rows))
    monkeypatch.setattr("books.DOC_INDEX_DIR", index)


def test_indexed_passages_are_searched_and_stay_in_their_pile(tmp_path, monkeypatch):
    rows = documents.to_clauses(
        [{"id": "d499-p14-2", "page": 14, "text": "All margin transfers between parties may be subject to a de-minimis minimum transfer amount not to exceed 500,000."}],
        documents.SOURCES[0])
    _write_index(tmp_path, monkeypatch, rows)
    hits = search.search("de-minimis minimum transfer amount", pile="haircut_schedule")["hits"]
    assert hits and hits[0]["id"] == "d499-p14-2" and "PDF page 14" in hits[0]["source"]
    assert all(h["id"] != "d499-p14-2" for h in search.search("de-minimis minimum transfer amount", pile="agreement", top=10)["hits"])


def test_an_indexed_passage_can_be_quoted_word_for_word(tmp_path, monkeypatch):
    text = "Equities included in major stock indices 15 Gold 15 Additional (additive) haircut on asset 8"
    _write_index(tmp_path, monkeypatch, documents.to_clauses([{"id": "d499-p30-1", "page": 30, "text": text}], documents.SOURCES[0]))
    hit = search.search("gold haircut", pile="haircut_schedule", top=5)["hits"]
    passages = [h["text"] for h in hit]
    assert agent.quote_ok("Equities included in major stock indices 15 Gold 15", passages)
    assert not agent.quote_ok("Gold 12", passages)


REAL_INDEX = books.DATA / "docs" / "index" / "d499.json"


@pytest.mark.skipif(not REAL_INDEX.exists(), reason="run scripts/fetch_documents.py to build the d499 index")
def test_real_d499_index_contains_appendix_b_and_the_minimum_transfer_rule():
    rows = json.loads(REAL_INDEX.read_text())
    texts = " ".join(r["text"] for r in rows)
    assert len(rows) > 100
    assert "Appendix B Standardised haircut schedule" in texts
    assert "Equities included in major stock indices 15 Gold 15" in texts
    assert "minimum transfer amount not to exceed €500,000" in texts
    assert all(r["pile"] == "haircut_schedule" and "PDF page" in r["source"] for r in rows)
    assert len({r["id"] for r in rows}) == len(rows)


# ----------------------------------------------------------------------------- pruning the vector cache

def _cache_file(tmp_path, monkeypatch, entries):
    path = tmp_path / "embed_cache.json"
    path.write_text(json.dumps(entries))
    monkeypatch.setattr(search, "CACHE", path)
    return path


M = "bge-small-en-v1.5"


def test_prune_removes_only_old_versions_of_the_documents_passages(tmp_path, monkeypatch):
    path = _cache_file(tmp_path, monkeypatch, {
        f"{M}::d499-p14-2::2.3 All margin transfers (new text)": [1.0],
        f"{M}::d499-p14-2::The threshold is applied ... 2.3 All margin (old text)": [2.0],   # stale
        f"{M}::d499-p99-1::a passage that no longer exists": [3.0],                         # stale
        f"{M}::d499-appendix-b-corporate::Appendix B. Standardised haircut schedule.": [4.0],  # clauses.json
        f"{M}::meridian-mta::Meridian Capital sample CSA, paragraph 5.": [5.0],
        f"{M}::intent::margin_call::0::What margin call do we have on Meridian?": [6.0],
        f"{M}::cfr45-gse::12 CFR Appendix B to Part 45, Table B.": [7.0],
    })
    removed = search.prune_cache("d499-p", {("d499-p14-2", "2.3 All margin transfers (new text)")})
    assert removed == 2
    left = json.loads(path.read_text())
    assert f"{M}::d499-p14-2::2.3 All margin transfers (new text)" in left
    assert not any("old text" in k or "no longer exists" in k for k in left)
    assert len(left) == 5  # the current passage, the excerpt, the sample clause, the intent, the 12 CFR passage


def test_prune_with_nothing_stale_leaves_the_file_untouched(tmp_path, monkeypatch):
    path = _cache_file(tmp_path, monkeypatch, {f"{M}::d499-p1-1::current": [1.0]})
    before = path.read_text()
    assert search.prune_cache("d499-p", {("d499-p1-1", "current")}) == 0
    assert path.read_text() == before


def test_rebuilding_the_index_prunes_the_cache(tmp_path, monkeypatch):
    _cache_file(tmp_path, monkeypatch, {
        f"{M}::d499-p1-1::An old version of the first passage that is long enough to keep.": [1.0],
        f"{M}::meridian-mta::kept": [2.0],
    })
    monkeypatch.setattr(documents, "fetch", lambda source, raw_dir=None: tmp_path / "d499.pdf")
    monkeypatch.setattr(documents, "read_pdf_pages", lambda path: ["The first passage is now worded differently and is long enough."])
    done = documents.build_index(documents.SOURCES[0], raw_dir=tmp_path, index_dir=tmp_path / "index")
    assert done == {"passages": 1, "pruned": 1}
    left = json.loads((tmp_path / "embed_cache.json").read_text())
    assert list(left) == [f"{M}::meridian-mta::kept"]
