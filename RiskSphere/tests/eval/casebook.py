"""Load, validate and compare evaluation cases.

A case is one JSON object per line in tests/eval/cases/*.jsonl.

Routing-case fields (layer == "route"):
  id, layer, query, last_counterparty, category, requirement_ids,
  source, split, expected_stop, needs_clarification, expected_tools,
  expected_args, expected_missing_arg (only when needs_clarification),
  index_overlap (True when the query is (near-)identical to an example in
  data/intents.json, which the semantic matcher already knows).

Optional: generator (model id, required for llm_paraphrase), reviewed (bool,
required for llm_paraphrase: has a person verified the label?), notes.

source: author_seed | spec_derived | template | llm_paraphrase | peer | public | log
split : dev (may be tuned against) | test (frozen; never tune against it)
"""

import difflib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
REQUIREMENTS_PATH = HERE / "requirements.json"
INTENTS_PATH = HERE.parents[1] / "data" / "intents.json"

SOURCES = {"author_seed", "spec_derived", "template", "llm_paraphrase", "peer", "public", "log"}
SPLITS = {"dev", "test"}
LAYERS = {"route"}
STOPS = {None, "kill", "injection", "pii", "action", "legal", "scope"}
TOOLS = {"current_exposure", "calculate_margin_call", "get_market_data", "regulatory_haircut", "check_pfe_limit", "search_clauses"}
MISSING_ARGS = {"counterparty", "ticker", "asset_class"}
OVERLAP_THRESHOLD = 0.85

REQUIRED = ["id", "layer", "query", "last_counterparty", "category", "requirement_ids",
            "source", "split", "expected_stop", "needs_clarification",
            "expected_tools", "expected_args", "index_overlap"]


def normalise(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9=+<>\- ]", " ", text.lower())).strip()


def similarity(a, b):
    return difflib.SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def load_requirements():
    return {r["id"]: r for r in json.loads(REQUIREMENTS_PATH.read_text())}


def index_examples():
    rows = json.loads(INTENTS_PATH.read_text())
    return [(r["intent"], ex) for r in rows for ex in r.get("examples", [])]


def max_index_similarity(query):
    best = max(((similarity(query, ex), ex) for _, ex in index_examples()), default=(0.0, ""))
    return best


def load_cases(splits=None):
    cases = []
    for path in sorted(CASES_DIR.glob("*.jsonl")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip():
                case = json.loads(line)
                case["_file"] = f"{path.name}:{n}"
                cases.append(case)
    if splits:
        cases = [c for c in cases if c.get("split") in splits]
    return cases


def schema_errors(case, requirements):
    """Return a list of problems with one case (empty list = valid)."""
    errs = []
    for key in REQUIRED:
        if key not in case:
            errs.append(f"missing field {key}")
    if errs:
        return errs
    if case["layer"] not in LAYERS:
        errs.append(f"bad layer {case['layer']}")
    if not isinstance(case["query"], str) or not case["query"].strip():
        errs.append("query must be a non-empty string")
    if case["source"] not in SOURCES:
        errs.append(f"bad source {case['source']}")
    if case["split"] not in SPLITS:
        errs.append(f"bad split {case['split']}")
    if case["expected_stop"] not in STOPS:
        errs.append(f"bad expected_stop {case['expected_stop']}")
    if case["source"] == "llm_paraphrase":
        if not case.get("generator"):
            errs.append("llm_paraphrase needs generator")
        if not isinstance(case.get("reviewed"), bool):
            errs.append("llm_paraphrase needs reviewed (bool)")
    if not case["requirement_ids"]:
        errs.append("requirement_ids is empty")
    for rid in case["requirement_ids"]:
        if rid not in requirements:
            errs.append(f"unknown requirement {rid}")
    if not isinstance(case["needs_clarification"], bool):
        errs.append("needs_clarification must be bool")
    if not isinstance(case["index_overlap"], bool):
        errs.append("index_overlap must be bool")
    for tool in case["expected_tools"]:
        if tool not in TOOLS:
            errs.append(f"bad tool {tool}")
    if case["expected_stop"] is not None:
        if case["expected_tools"] or case["needs_clarification"]:
            errs.append("a stopped question cannot expect tools or clarification")
    elif case["needs_clarification"]:
        if case.get("expected_missing_arg") not in MISSING_ARGS:
            errs.append("clarification case needs expected_missing_arg")
        if case["expected_tools"]:
            errs.append("clarification case must not expect tools")
    else:
        if not case["expected_tools"]:
            errs.append("a routed question must expect at least one tool")
        if len(case["expected_tools"]) > 4:
            errs.append("more than 4 tools")
        if not isinstance(case["expected_args"], list):
            errs.append("expected_args must be a list")
    return errs


# ----------------------------------------------------------------------------- conversations
CONV_DIR = HERE / "conversations"
OUTCOMES = {"answer", "clarify", "refuse"}
CONV_REQUIRED = ["id", "category", "requirement_ids", "source", "split", "turns"]


def load_conversations(splits=None):
    """Multi-turn scenarios, one JSON object per line in tests/eval/conversations/*.jsonl.

    turn: {user, expect: {outcome, stop|missing_arg|tools+args}, state_after: {last_counterparty, pending},
           env (optional), new_session (optional, a restart: state must not survive)}
    pending is None or the missing argument name of the open clarification.
    """
    convs = []
    for path in sorted(CONV_DIR.glob("*.jsonl")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip():
                conv = json.loads(line)
                conv["_file"] = f"{path.name}:{n}"
                convs.append(conv)
    if splits:
        convs = [c for c in convs if c.get("split") in splits]
    return convs


def conv_schema_errors(conv, requirements):
    errs = [f"missing field {k}" for k in CONV_REQUIRED if k not in conv]
    if errs:
        return errs
    if conv["source"] not in SOURCES:
        errs.append(f"bad source {conv['source']}")
    if conv["split"] not in SPLITS:
        errs.append(f"bad split {conv['split']}")
    for rid in conv["requirement_ids"]:
        if rid not in requirements:
            errs.append(f"unknown requirement {rid}")
    if not conv["requirement_ids"]:
        errs.append("requirement_ids is empty")
    if len(conv["turns"]) < 2:
        errs.append("a conversation needs at least 2 turns")
    for i, t in enumerate(conv["turns"], 1):
        where = f"turn {i}"
        if not isinstance(t.get("user"), str) or not t["user"].strip():
            errs.append(f"{where}: user must be a non-empty string")
        e = t.get("expect") or {}
        if e.get("outcome") not in OUTCOMES:
            errs.append(f"{where}: bad outcome")
        elif e["outcome"] == "refuse" and e.get("stop") not in STOPS - {None} | {"planner"}:
            errs.append(f"{where}: refuse needs a stop")
        elif e["outcome"] == "clarify" and e.get("missing_arg") not in MISSING_ARGS:
            errs.append(f"{where}: clarify needs missing_arg")
        elif e["outcome"] == "answer":
            if not e.get("tools"):
                errs.append(f"{where}: answer needs tools")
            for tool in e.get("tools", []):
                if tool not in TOOLS:
                    errs.append(f"{where}: bad tool {tool}")
        sa = t.get("state_after")
        if not isinstance(sa, dict) or "last_counterparty" not in sa or "pending" not in sa:
            errs.append(f"{where}: state_after needs last_counterparty and pending")
        elif sa["pending"] is not None and sa["pending"] not in MISSING_ARGS:
            errs.append(f"{where}: bad pending {sa['pending']}")
    return errs
