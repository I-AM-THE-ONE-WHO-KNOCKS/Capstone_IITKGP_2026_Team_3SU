"""Rank the clause corpus for a question.

The dense signal here is the hosted embedding model, bge-small-en-v1.5. A
request holds at most 16 texts and vectors are cached on disk keyed by the
model and the text. The ranking itself, the lexical BM25 signal, and the
fusion of the two live in the retrieval module; this file is the embedding
client plus the glue that feeds the corpus in.

If the key, the URL, or the certificate is missing, the retriever ranks by
BM25 alone and the miss is logged. The sentences are samples written for the
project. A hit does not change the stored terms.
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

import agent
import log
import retrieval
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


def _cached_dense_fn():
    """Return a function that embeds a list of texts, reusing the disk cache.

    The returned function raises if the gateway, the key, or the certificate
    is unavailable, which the retriever treats as "drop to BM25 only". The
    cache is keyed by the model and the exact text, so the same sentence is
    embedded at most once across runs. Returns None when no key is set, so
    retrieval stays lexical without even attempting a call.
    """
    if not os.environ.get("EMBED_API_KEY", "").strip():
        return None
    model = _model()
    cache = _load_cache()

    def embed(texts):
        missing = [t for t in texts if (model + "::" + t) not in cache]
        if missing:
            fresh = _embed_many(missing)
            for text, vec in zip(missing, fresh):
                cache[model + "::" + text] = vec
            _save_cache(cache)
        return [cache[model + "::" + t] for t in texts]

    return embed


def search(question, counterparty_id="", top=2, pile=""):
    """Rank clause passages for a question with hybrid retrieval.

    counterparty_id, when set, keeps that name's passages plus the shared
    ones. pile is "csa" or "d499"; an empty pile searches both. top is how
    many hits to return. Each hit has source (S1, S2, ...), id, title, text,
    and the BM25, dense, and fused scores. mode says which signals fired.

    The dense signal reuses cached embeddings when the model and the text
    match. A failure of the embedding call drops to BM25 alone and logs it.
    Does not change stored terms and does not call the chat model.
    """
    log.work("Documents", f"retrieving from the {pile or 'combined'} pile")
    agent.load_secrets()
    pool = clause_rows()
    if pile:
        pool = [clause for clause in pool if (clause.get("pile") or "csa") == pile]
    if counterparty_id:
        pool = [clause for clause in pool if clause["counterparty_id"] in (counterparty_id, "")]

    result = retrieval.retrieve(question, pool, top=top, dense_fn=_cached_dense_fn())
    log.debug("retrieve " + json.dumps({
        "mode": result["mode"],
        "pool": len(pool),
        "returned": len(result["hits"]),
    }))
    return {
        "mode": result["mode"],
        "model": _model() if "dense" in result["mode"] else "",
        "hits": result["hits"],
    }
