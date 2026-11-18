"""Multi-turn scenarios: memory, pronouns, clarification, refusals in the middle, restarts.

Each conversation is played through app.answer with the calculation server mocked
and the session file in a temp dir. A conversation passes only if every turn does
(outcome and memory after the turn). Known failures (known_issues_conversations.json)
are strict xfails: they document a defect and turn red when it is fixed.
With RUN_LIVE_EVAL=1 the hosted planner is used and nothing is xfailed.
"""

import json

import pytest

import agent
import casebook
import runner
from conftest import LIVE

CONVS = casebook.load_conversations()
KNOWN = {} if LIVE else json.loads((casebook.HERE / "known_issues_conversations.json").read_text())


def _param(conv):
    marks = []
    if conv["id"] in KNOWN:
        k = KNOWN[conv["id"]]
        marks.append(pytest.mark.xfail(strict=True, reason=f"turn {k['first_failed_turn']} {k['kind']}: {k['detail']}"))
    return pytest.param(conv, id=conv["id"], marks=marks)


@pytest.mark.parametrize("conv", [_param(c) for c in CONVS])
def test_conversation(conv):
    planner = "live" if (LIVE and agent.llm_enabled()) else "offline"
    res = runner.evaluate_conversation(conv, planner)
    bad = [(i, t) for i, t in enumerate(res["turns"], 1) if not t["ok"]]
    assert res["ok"], "; ".join(f"turn {i} {t['user']!r}: {t['kind']} - {t['detail']}" for i, t in bad)


def test_known_conversation_issues_refer_to_real_conversations():
    assert set(KNOWN) <= {c["id"] for c in CONVS}


@pytest.mark.parametrize("conv", CONVS, ids=[c["id"] for c in CONVS])
def test_conversation_schema(conv):
    assert casebook.conv_schema_errors(conv, casebook.load_requirements()) == [], conv["_file"]


def test_conversation_ids_unique_and_nonempty():
    ids = [c["id"] for c in CONVS]
    assert ids and len(ids) == len(set(ids))


def test_real_session_file_is_never_touched(tmp_path):
    """The runner must use its own temp state, never data/session_state.json."""
    real = agent.ROOT / "data" / "session_state.json"
    before = real.read_bytes() if real.exists() else None
    runner.evaluate_conversation(CONVS[0], "offline")
    assert (real.read_bytes() if real.exists() else None) == before
