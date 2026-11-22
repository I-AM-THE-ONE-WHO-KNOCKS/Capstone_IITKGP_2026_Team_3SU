"""Score the retriever against a frozen gold set.

This measures retrieval on its own, apart from whether the final wording is
any good. For each labelled query it ranks the pile and checks where the
relevant passage lands. It reports Recall@1, Recall@3, Recall@5, mean
reciprocal rank, and nDCG@5, averaged over the set.

With no embedding key the dense signal is off, so the run is the lexical
BM25 retriever and is fully deterministic. With a key set, both the lexical
and the hybrid numbers are printed side by side so the lift from the dense
signal is visible. Writes a per-query CSV to the outputs folder.
"""

import csv
import json
import math
from pathlib import Path

import agent
import log
import retrieval
import search
from books import clause_rows

HERE = Path(__file__).resolve().parent
GOLD = HERE / "data" / "retrieval_gold.json"
OUT = HERE / "outputs" / "retrieval_eval.csv"
KS = (1, 3, 5)


def _pile(pile):
    """Passages in one pile, in corpus order."""
    return [c for c in clause_rows() if (c.get("pile") or "csa") == pile]


def _rank_of_relevant(hits, relevant):
    """1-based rank of the first relevant hit, or 0 if none of them ranked."""
    for place, hit in enumerate(hits):
        if hit["id"] in relevant:
            return place + 1
    return 0


def _ndcg_at(hits, relevant, k):
    """nDCG@k with binary relevance. One relevant passage per query here."""
    dcg = 0.0
    for place, hit in enumerate(hits[:k]):
        if hit["id"] in relevant:
            dcg += 1.0 / math.log2(place + 2)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / ideal if ideal else 0.0


def _score(dense_fn, label, rows):
    """Run every gold query through the retriever and average the metrics."""
    totals = {f"recall@{k}": 0.0 for k in KS}
    totals["mrr"] = 0.0
    totals["ndcg@5"] = 0.0
    n = len(rows)
    for item in rows:
        pool = _pile(item["pile"])
        result = retrieval.retrieve(item["query"], pool, top=5, dense_fn=dense_fn)
        hits = result["hits"]
        relevant = set(item["relevant"])
        rank = _rank_of_relevant(hits, relevant)
        for k in KS:
            if 0 < rank <= k:
                totals[f"recall@{k}"] += 1.0
        totals["mrr"] += (1.0 / rank) if rank else 0.0
        totals["ndcg@5"] += _ndcg_at(hits, relevant, 5)
        item.setdefault("_ranks", {})[label] = rank
    return {metric: value / n for metric, value in totals.items()} if n else totals


def evaluate():
    """Score the retriever and return (gold rows, {mode: metrics}).

    Always scores the lexical signal. When an embedding key is set it also
    scores the hybrid signal, so a caller can show the lift side by side.
    """
    agent.load_secrets()
    rows = json.loads(GOLD.read_text())
    log.work("Documents", f"scoring retrieval on {len(rows)} labelled queries")
    results = {"lexical (bm25)": _score(None, "lexical", rows)}
    dense_fn = search._cached_dense_fn()
    if dense_fn is not None:
        results["hybrid (bm25+dense)"] = _score(dense_fn, "hybrid", rows)
    return rows, results


def main():
    """Score lexical, and hybrid when a key is set, then write the CSV."""
    log.configure()
    rows, results = evaluate()

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query", "pile", "relevant", "rank_lexical", "rank_hybrid"])
        for item in rows:
            ranks = item.get("_ranks", {})
            writer.writerow([
                item["query"], item["pile"], ";".join(item["relevant"]),
                ranks.get("lexical", ""), ranks.get("hybrid", ""),
            ])

    print(f"retrieval eval over {len(rows)} queries")
    header = ["mode"] + [f"recall@{k}" for k in KS] + ["mrr", "ndcg@5"]
    print("  ".join(f"{h:>18}" for h in header))
    for mode, metric in results.items():
        line = [mode] + [f"{metric[f'recall@{k}']:.3f}" for k in KS]
        line += [f"{metric['mrr']:.3f}", f"{metric['ndcg@5']:.3f}"]
        print("  ".join(f"{v:>18}" for v in line))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
