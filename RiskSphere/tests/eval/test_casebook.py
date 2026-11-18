"""Guards the evaluation data itself: schema, provenance, coverage, leakage."""

import collections

import pytest

import casebook

REQS = casebook.load_requirements()
CASES = casebook.load_cases()


def test_there_are_cases():
    assert CASES


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_schema(case):
    assert casebook.schema_errors(case, REQS) == [], case["_file"]


def test_ids_unique():
    dupes = [i for i, n in collections.Counter(c["id"] for c in CASES).items() if n > 1]
    assert not dupes, f"duplicate ids: {dupes}"


def test_queries_unique_within_split():
    seen = collections.Counter((c["split"], casebook.normalise(c["query"]), c["last_counterparty"]) for c in CASES)
    assert not [k for k, n in seen.items() if n > 1], "duplicate query in a split"


def test_dev_and_test_splits_do_not_share_queries():
    dev = {casebook.normalise(c["query"]) for c in CASES if c["split"] == "dev"}
    test = {casebook.normalise(c["query"]) for c in CASES if c["split"] == "test"}
    assert not dev & test


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_index_overlap_flag_is_truthful(case):
    """index_overlap must say whether the query is already a semantic-index example."""
    sim, example = casebook.max_index_similarity(case["query"])
    assert case["index_overlap"] == (sim >= casebook.OVERLAP_THRESHOLD), \
        f"{case['id']}: similarity {sim:.2f} to {example!r}"


def test_held_out_split_never_overlaps_the_index():
    leaks = [c["id"] for c in CASES if c["split"] == "test" and c["index_overlap"]]
    assert not leaks, f"held-out cases leak from intents.json: {leaks}"


def test_requirement_ids_are_unique_and_well_formed():
    raw = casebook.REQUIREMENTS_PATH.read_text()
    import json
    ids = [r["id"] for r in json.loads(raw)]
    assert len(ids) == len(set(ids))
    assert all(r["text"] and r["source"] and r["area"] for r in REQS.values())


def test_requirement_coverage_report(capsys):
    """Informational: which requirements have no routing case yet (math/pfe/llm need other layers)."""
    covered = {rid for c in CASES for rid in c["requirement_ids"]}
    missing = sorted(set(REQS) - covered)
    with capsys.disabled():
        print(f"\n[coverage] {len(covered)}/{len(REQS)} requirements covered by routing cases; "
              f"uncovered: {', '.join(missing)}")
