"""Rank sample agreement sentences with the hosted embedding API.

The model is bge-small-en-v1.5. A request holds at most 16 texts. Vectors
are compared with cosine similarity. If the key, the URL, or the
certificate is missing, word overlap is used and the miss is logged.

The sentences are samples written for the project. A hit does not change
the stored terms, and the planner does not call search() yet.
"""

import json
import math
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

import agent
import log
from books import clause_rows

CACHE = Path(__file__).resolve().parent / "data" / "embed_cache.json"
BATCH = 16


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


def _save_cache(cache):
    """Write the vector cache. cache maps a string key to a list of floats."""
    CACHE.write_text(json.dumps(cache))


def search(question, counterparty_id="", top=2):
    """Rank sample sentences for a question.

    counterparty_id, when set, keeps that name's sentences plus the shared
    ones. top is how many hits to return. Each hit has id, title, text,
    and score. mode is the model name, or "keyword" after a fallback.

    Cached vectors are reused when the model and the sentence text match.
    A failure of the embedding call logs an error and scores by word overlap
    instead. Does not change stored terms and does not call the chat model.
    """
    agent.load_secrets()
    clauses = clause_rows()
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
    for score, clause in scored[:top]:
        hits.append({
            "id": clause["id"],
            "title": clause["title"],
            "text": clause["text"],
            "score": round(score, 4),
        })
    return {"mode": mode, "model": _model() if mode != "keyword" else "", "hits": hits}
