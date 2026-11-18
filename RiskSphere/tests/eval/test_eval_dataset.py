"""Layer 4: run every routing case through the real agent code.

Offline (default): guardrails + keyword planner. Cases listed in known_issues.json
are strict xfails: they document a defect that exists today and turn red when it
is fixed, so the list can only shrink. With RUN_LIVE_EVAL=1 the hosted planner is
used and nothing is xfailed (use run_eval.py for the scored report).
"""

import json

import pytest

import agent
import casebook
import runner
from conftest import LIVE

CASES = casebook.load_cases()
KNOWN = {} if LIVE else json.loads((casebook.HERE / "known_issues.json").read_text())


def _param(case):
    marks = []
    if case["id"] in KNOWN:
        k = KNOWN[case["id"]]
        marks.append(pytest.mark.xfail(strict=True, reason=f"{k['class']} | {k['kind']}: {k['detail']}"))
    return pytest.param(case, id=case["id"], marks=marks)


@pytest.mark.parametrize("case", [_param(c) for c in CASES])
def test_eval_case(case):
    planner = "live" if (LIVE and agent.llm_enabled()) else "offline"
    ok, kind, detail, _ = runner.evaluate(case, planner)
    assert ok, f"{kind}: {detail}"


def test_known_issues_refer_to_real_cases():
    ids = {c["id"] for c in CASES}
    assert set(KNOWN) <= ids, f"stale entries: {sorted(set(KNOWN) - ids)}"
