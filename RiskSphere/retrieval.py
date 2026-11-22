"""Hybrid retrieval over the clause corpus.

Two signals rank the passages and a fusion step combines them:

  - a lexical signal, Okapi BM25, that rewards rare shared words;
  - a dense signal, cosine over hosted embeddings, that rewards meaning.

The two rankings are merged with reciprocal rank fusion, which needs no
model of its own and does not care that BM25 and cosine live on different
scales. When the embedding gateway is unreachable, retrieval falls back to
the lexical signal alone and says so, so a demo still runs with the model
off. Every hit carries a stable source tag, S1, S2 and so on, so the
wording can cite it and the critic can check the quote against it.

Nothing here changes a stored term or calls the chat model.
"""

import math
import re

import log

# how many results each signal contributes to the fusion
_SIGNAL_DEPTH = 10
# reciprocal rank fusion constant; 60 is the value from the original paper
_RRF_K = 60

_WORD = re.compile(r"[a-z0-9]+")
# short function words that add noise to a lexical match
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is", "are",
    "was", "were", "be", "by", "with", "as", "at", "it", "its", "this", "that",
    "do", "does", "we", "our", "what", "which", "how", "me", "my", "i",
}


def _tokens(text):
    """Lower-case word tokens with the stop words dropped."""
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _bm25_scores(query_tokens, docs_tokens, k1=1.5, b=0.75):
    """Okapi BM25 score of the query against every document.

    docs_tokens is a list of token lists, one per passage. Returns a list of
    floats in the same order. A passage that shares no query word scores 0.
    """
    n = len(docs_tokens)
    if n == 0:
        return []
    doc_len = [len(d) for d in docs_tokens]
    avg_len = sum(doc_len) / n if n else 0.0
    # document frequency of each query term
    df = {}
    for term in set(query_tokens):
        df[term] = sum(1 for d in docs_tokens if term in d)
    scores = [0.0] * n
    for i, doc in enumerate(docs_tokens):
        freq = {}
        for w in doc:
            freq[w] = freq.get(w, 0) + 1
        total = 0.0
        for term in set(query_tokens):
            if df.get(term, 0) == 0:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            tf = freq.get(term, 0)
            denom = tf + k1 * (1 - b + b * (doc_len[i] / avg_len if avg_len else 1))
            total += idf * (tf * (k1 + 1)) / denom if denom else 0.0
        scores[i] = total
    return scores


def _rank_order(scores):
    """Indices of scores from best to worst; a zero score does not rank."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [i for i in order if scores[i] > 0]


def _rrf(rankings):
    """Reciprocal rank fusion of several rankings.

    rankings is a list of ranking lists, each a list of passage indices from
    best to worst. Returns {index: fused_score}. A passage ranked near the
    top by either signal floats up; agreement between signals compounds.
    """
    fused = {}
    for ranking in rankings:
        for place, idx in enumerate(ranking[:_SIGNAL_DEPTH]):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (_RRF_K + place + 1)
    return fused


def retrieve(question, passages, top=3, dense_fn=None):
    """Rank passages for a question and tag the winners.

    passages is a list of dicts with id, title, and text. dense_fn, when
    given, maps a list of strings to a list of vectors and is used for the
    cosine signal; a failure or a missing dense_fn drops to lexical only.
    top is how many hits to return.

    Returns {mode, hits}. mode is "hybrid (bm25+dense)" when both signals
    fired, otherwise "lexical (bm25)". Each hit has source (S1, S2, ...),
    id, title, text, and the per-signal and fused scores.
    """
    if not passages:
        return {"mode": "lexical (bm25)", "hits": []}

    query_tokens = _tokens(question)
    docs_tokens = [_tokens(p["title"] + " " + p["text"]) for p in passages]
    bm25 = _bm25_scores(query_tokens, docs_tokens)
    bm25_rank = _rank_order(bm25)

    dense = [0.0] * len(passages)
    dense_rank = []
    mode = "lexical (bm25)"
    if dense_fn is not None:
        try:
            vectors = dense_fn([p["text"] for p in passages])
            query_vec = dense_fn([question])[0]
            dense = [_cosine(query_vec, v) for v in vectors]
            dense_rank = _rank_order(dense)
            mode = "hybrid (bm25+dense)"
        except Exception as exc:  # noqa: BLE001 - any gateway failure falls back
            log.work("Documents", "embedding call failed, ranking by BM25 alone")
            log.error(f"dense signal unavailable, lexical only: {exc}")
            dense_rank = []

    rankings = [bm25_rank]
    if dense_rank:
        rankings.append(dense_rank)
    fused = _rrf(rankings)
    if not fused:
        # nothing shared a word and dense was off; keep the lexical order so
        # the caller still gets a best effort instead of an empty answer
        fused = {i: 1.0 / (i + 1) for i in range(len(passages))}

    order = sorted(fused, key=lambda i: fused[i], reverse=True)[:top]
    hits = []
    for place, i in enumerate(order):
        passage = passages[i]
        hits.append({
            "source": f"S{place + 1}",
            "id": passage["id"],
            "title": passage["title"],
            "text": passage["text"],
            "bm25": round(bm25[i], 4),
            "dense": round(dense[i], 4),
            "score": round(fused[i], 6),
        })
    return {"mode": mode, "hits": hits}


def _cosine(a, b):
    """Cosine similarity of two equal-length vectors. Zero vectors score 0."""
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / math.sqrt(na * nb)
