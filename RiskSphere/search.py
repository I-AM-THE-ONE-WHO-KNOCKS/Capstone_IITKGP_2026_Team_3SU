"""Rank sample agreement sentences with the hosted embedding API.

The model is bge-small-en-v1.5. A request holds at most 16 texts. Vectors
are compared with cosine similarity. If the key, the URL, or the
certificate is missing, word overlap is used and the miss is logged.

The sentences are samples written for the project, plus the 12 CFR haircut
table. Each belongs to one pile. A hit does not change the stored terms. The
planner reaches this through the search_clauses tool.
"""

import json
import math
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

import agent
import log
from books import clause_rows

CACHE = Path(__file__).resolve().parent / "data" / "embed_cache.json"
INTENTS_PATH = Path(__file__).resolve().parent / "data" / "intents.json"
BATCH = 16
PILES = ("agreement", "haircut_schedule")
RELATIVE_FLOOR = 0.85  # a later hit must score at least this share of the best one

# Words that say what kind of question this is, not what it is about.
_STOP = {
    "a", "an", "the", "what", "which", "does", "do", "is", "are", "of", "in", "on", "for", "to", "and", "or",
    "about", "say", "says", "state", "states", "it", "its", "me", "show", "tell", "give", "quote", "cite",
    "clause", "clauses", "paragraph", "wording", "text", "document", "per", "under", "with", "our", "we",
    "how", "much", "any", "there", "this", "that", "please", "exactly", "according", "from",
}


# Names of documents and of the source itself. Every passage of a pile shares these, so they
# say which document is meant, not what the question is about.
_DOCUMENT_NAMES = {
    "csa", "credit", "support", "annex", "agreement", "contract", "schedule", "table", "sample",
    "haircut", "d499", "bcbs", "iosco", "basel", "cfr", "12", "part", "45", "appendix", "b",
    "standardised", "standardized", "regulation", "rule", "framework", "paragraph",
}


def _counterparty_names():
    """Every word of every counterparty name and id, for example meridian, capital, apex, harbor, lane."""
    import books

    words = set()
    for row in books.names_only():
        words.update(re.findall(r"[a-z0-9]+", (row["name"] + " " + row["counterparty_id"]).lower()))
    return words


def _stem(word):
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def content_words(text):
    """Lower-case words that carry the topic, with plural endings removed."""
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1}


def topic_words(text):
    """The words that say what a question is about: content words minus document and company names."""
    names = {_stem(w) for w in _DOCUMENT_NAMES | _counterparty_names()}  # "bcbs" is stemmed to "bcb" too
    return {w for w in content_words(text) if w not in names}


def shares_a_topic_word(question, clause):
    """True when the question and the passage have at least one topic word in common.

    This is the floor under the ranker. Without it the best of a few unrelated
    passages would be returned for any question, and it would be quoted. Company and
    document names do not count: every Apex passage says "Apex" and "CSA", so a question
    about Apex's governing law must not match on those words alone.
    """
    return bool(topic_words(question) & content_words(clause["title"] + " " + clause["text"]))


def _model():
    """Embedding model name. Defaults to bge-small-en-v1.5."""
    return os.environ.get("EMBED_MODEL", "bge-small-en-v1.5")


def _ssl_context():
    """TLS context for the embedding gateway. Verification stays on.

    Requires EMBED_CA_CERT to point at a readable certificate file.
    """
    cert = os.environ.get("EMBED_CA_CERT", "").strip()
    if not cert:
        raise RuntimeError("EMBED_CA_CERT is not set. Point it at vm02-ca.crt.")
    if not os.path.isfile(cert):
        raise RuntimeError(f"embedding certificate not readable: {cert}")
    return ssl.create_default_context(cafile=cert)


def _embed_batch(texts):
    """Embed one batch, at most BATCH texts. Returns vectors in input order.

    POSTs {"model", "input"} and sorts the response by data[].index.
    Retries HTTP 429 and 503 after 2, 4, and 8 seconds. Does not log the
    key or the raw vectors. Raises if the key or the base URL is missing,
    or if the vector count does not match the input.
    """
    key = os.environ.get("EMBED_API_KEY", "").strip()
    base = os.environ.get("EMBED_BASE_URL", "").strip()
    if not key or not base:
        raise RuntimeError("EMBED_API_KEY or EMBED_BASE_URL is not set")
    url = base.rstrip("/") + "/embeddings"
    body = json.dumps({"model": _model(), "input": texts}).encode()
    waits = (0, 2, 4, 8)
    last_error = None
    for attempt, wait in enumerate(waits):
        if wait:
            time.sleep(wait)
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60, context=_ssl_context()) as response:
                payload = json.loads(response.read().decode())
            rows = payload.get("data") or []
            rows.sort(key=lambda row: row.get("index", 0))
            vectors = [row["embedding"] for row in rows]
            if len(vectors) != len(texts):
                raise RuntimeError("embedding API returned the wrong number of vectors")
            log.trace(f"embed batch n={len(texts)} dim={len(vectors[0])} model={_model()}")
            return vectors
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (429, 503) and attempt < len(waits) - 1:
                log.trace(f"embed HTTP {exc.code}, retry")
                continue
            log.error(f"embed HTTP {exc.code}")
            raise RuntimeError(f"embedding API HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            log.error(f"embed unreachable: {exc.reason}")
            raise RuntimeError(f"could not reach the embedding API: {exc.reason}") from exc
    raise RuntimeError(f"embedding API still failing: {last_error}")


def _embed_many(texts):
    """Embed a long list by slicing it into batches of 16. Order is preserved."""
    out = []
    for start in range(0, len(texts), BATCH):
        out.extend(_embed_batch(texts[start:start + BATCH]))
    return out


def _cosine(a, b):
    """Cosine similarity of two equal-length vectors. Returns 0 when either is zero."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _overlap(query, text):
    """Share of query words, longer than two characters, that also appear in text.

    Used when the embedding call fails. Returns a fraction from 0 to 1.
    """
    q = {w for w in query.lower().split() if len(w) > 2}
    t = {w for w in text.lower().split() if len(w) > 2}
    if not q:
        return 0.0
    return len(q & t) / len(q)


def _load_cache():
    """Read cached vectors, keyed by model, clause id, and text. A bad file returns {}."""
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text())
    except json.JSONDecodeError:
        return {}


def prune_cache(id_prefix, keep):
    """Drop cached vectors for old versions of one document's passages. Returns how many went.

    id_prefix selects the document, for example "d499-p" for the indexed d499 PDF. Only
    entries whose passage id starts with it are touched; the sample sentences, the
    clauses.json excerpts (d499-appendix-b-...), the intent examples, and other documents
    are left alone. keep is the set of (id, text) pairs in the new index; any entry for the
    document that is not one of them can never be used again, because a cache key includes
    the text. Entries for another embedding model are judged by id and text alone.
    """
    cache = _load_cache()
    stale = []
    for key in cache:
        parts = key.split("::", 2)
        if len(parts) == 3 and parts[1].startswith(id_prefix) and (parts[1], parts[2]) not in keep:
            stale.append(key)
    for key in stale:
        del cache[key]
    if stale:
        _save_cache(cache)
    return len(stale)


def _save_cache(cache):
    """Write the vector cache. cache maps a string key to a list of floats."""
    CACHE.write_text(json.dumps(cache))


def search(question, counterparty_id="", top=2, pile=""):
    """Rank sample sentences for a question.

    counterparty_id, when set, keeps that name's sentences plus the shared
    ones. pile, when set, is "agreement" or "haircut_schedule" and keeps only
    that pile; the two never mix. An unknown pile raises ValueError. top is
    how many hits to return. Each hit has id, title, text, pile, source and
    score. A passage that shares no topic word with the question is not
    returned, and a later hit scoring under 85% of the best is dropped. An empty
    hits list means nothing in the pile answers the question.
    mode is the model name, or "keyword" after a fallback.

    Cached vectors are reused when the model and the sentence text match.
    A failure of the embedding call logs an error and scores by word overlap
    instead. Does not change stored terms and does not call the chat model.
    """
    agent.load_secrets()
    clauses = clause_rows()
    if pile:
        if pile not in PILES:
            raise ValueError(f"pile must be one of {list(PILES)}")
        clauses = [c for c in clauses if c.get("pile") == pile]
    if counterparty_id:
        pool = [c for c in clauses if c["counterparty_id"] in (counterparty_id, "")]
    else:
        pool = clauses

    mode = "keyword"
    vectors = None
    query_vec = None
    try:
        cache = _load_cache()
        model = _model()
        missing = []
        missing_at = []
        vectors = [None] * len(pool)
        for i, clause in enumerate(pool):
            key = model + "::" + clause["id"] + "::" + clause["text"]
            if key in cache:
                vectors[i] = cache[key]
            else:
                missing.append(clause["text"])
                missing_at.append(i)
        if missing:
            fresh = _embed_many(missing)
            for i, vec in zip(missing_at, fresh):
                clause = pool[i]
                cache[model + "::" + clause["id"] + "::" + clause["text"]] = vec
                vectors[i] = vec
            _save_cache(cache)
        query_vec = _embed_many([question])[0]
        mode = model
        log.debug(
            "embed search "
            + json.dumps({"model": model, "hits_ready": len(pool), "dim": len(query_vec)})
        )
    except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError, OSError, ValueError) as exc:
        log.error(f"embed search fell back to word overlap: {exc}")
        vectors = None

    scored = []
    for i, clause in enumerate(pool):
        if vectors is not None and query_vec is not None:
            score = _cosine(query_vec, vectors[i])
        else:
            score = _overlap(question, clause["title"] + " " + clause["text"])
        scored.append((score, clause))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    hits = []
    for score, clause in scored:
        if len(hits) == top:
            break
        if not shares_a_topic_word(question, clause):
            continue
        if hits and score < RELATIVE_FLOOR * hits[0]["score"]:
            continue  # a weaker hit that only shares a name or a common word is noise
        hits.append({
            "id": clause["id"],
            "title": clause["title"],
            "text": clause["text"],
            "pile": clause.get("pile", ""),
            "source": clause.get("source", ""),
            "score": round(score, 4),
        })
    return {"mode": mode, "model": _model() if mode != "keyword" else "", "hits": hits}


def load_intents():
    """Read the canonical intent/tool catalog from data/intents.json."""
    if not INTENTS_PATH.exists():
        return []
    try:
        return json.loads(INTENTS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def match_intents(question, threshold=None, top=None):
    """Match a question semantically against tool exemplars in data/intents.json.

    Uses hosted embeddings (bge-small-en-v1.5) when EMBED_API_KEY and
    EMBED_BASE_URL are set, caching exemplar vectors in embed_cache.json.
    Falls back to word overlap when the embedding service is not configured
    or fails. Returns a list of dicts sorted by score descending:
    [{"intent", "tool", "required_args", "score", "mode"}, ...].
    """
    agent.load_secrets()
    cfg = agent.load_config()
    intents = load_intents()
    if not intents:
        return []
    if threshold is None:
        threshold = float(cfg.get("semantic_threshold", 0.55))
    if top is None:
        top = int(cfg.get("semantic_top_k", 3))

    items = []
    for row in intents:
        for idx, example in enumerate(row.get("examples") or []):
            items.append({
                "key_id": f"intent::{row['intent']}::{idx}",
                "intent": row["intent"],
                "tool": row["tool"],
                "required_args": row.get("required_args") or [],
                "text": example,
            })
    if not items:
        return []

    mode = "keyword"
    vectors = None
    query_vec = None
    embed_ready = bool(os.environ.get("EMBED_API_KEY", "").strip() and os.environ.get("EMBED_BASE_URL", "").strip())
    if embed_ready:
        try:
            cache = _load_cache()
            model = _model()
            missing = []
            missing_at = []
            vectors = [None] * len(items)
            for i, item in enumerate(items):
                key = f"{model}::{item['key_id']}::{item['text']}"
                if key in cache:
                    vectors[i] = cache[key]
                else:
                    missing.append(item["text"])
                    missing_at.append(i)
            if missing:
                fresh = _embed_many(missing)
                for i, vec in zip(missing_at, fresh):
                    item = items[i]
                    cache[f"{model}::{item['key_id']}::{item['text']}"] = vec
                    vectors[i] = vec
                _save_cache(cache)
            query_vec = _embed_many([question])[0]
            mode = model
        except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError, OSError, ValueError) as exc:
            log.error(f"intent match fell back to word overlap: {exc}")
            vectors = None

    best_by_tool = {}
    oos_best = 0.0
    stopwords = {"what", "the", "are", "for", "and", "with", "from", "that", "this", "have", "does", "today", "how", "much", "give", "show", "need"}
    q_words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2 and w not in stopwords}
    eff_threshold = threshold if mode != "keyword" else 0.45
    for i, item in enumerate(items):
        if vectors is not None and query_vec is not None:
            score = _cosine(query_vec, vectors[i])
        else:
            t_words = {w for w in re.findall(r"[a-z0-9]+", item["text"].lower()) if len(w) > 2 and w not in stopwords}
            score = (len(q_words & t_words) / len(q_words)) if q_words else 0.0
        tool = item["tool"]
        if not tool:
            oos_best = max(oos_best, score)
            continue
        prev = best_by_tool.get(tool)
        if prev is None or score > prev["score"]:
            best_by_tool[tool] = {
                "intent": item["intent"],
                "tool": tool,
                "required_args": item["required_args"],
                "score": round(score, 4),
                "mode": mode,
            }

    ranked = [row for row in best_by_tool.values() if row["score"] >= eff_threshold and row["score"] > oos_best]
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked[:top]
