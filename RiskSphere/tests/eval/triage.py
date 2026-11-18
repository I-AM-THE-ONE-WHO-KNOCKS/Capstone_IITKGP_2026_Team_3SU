"""Snapshot today's offline failures into known_issues.json.

Run:  python3 tests/eval/triage.py
Each entry becomes a strict xfail in pytest: the suite stays green while the
defect exists, and goes red (XPASS) the moment someone fixes it, forcing the
entry to be deleted. The file should only ever shrink. Review it by hand.
"""

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1])]
os.environ.update(PFE_LLM_OFF="1", LLM_API_KEY="", LLM_BASE_URL="", EMBED_API_KEY="", EMBED_BASE_URL="", PFE_KILL_SWITCH="0")

import casebook  # noqa: E402
import runner  # noqa: E402

CLASS = {
    runner.GUARD_MISSED: "bug: guardrail does not stop what the README says it stops",
    runner.GUARD_WRONG: "bug: wrong guardrail fired",
    runner.FALSE_REFUSAL: "bug: valid desk question refused (offline scope check; live embeddings may rescue it)",
}

issues = {}
for case in casebook.load_cases():
    ok, kind, detail, _ = runner.evaluate(case, "offline")
    if not ok:
        issues[case["id"]] = {"kind": kind, "class": CLASS.get(kind, "limit: keyword fallback planner"), "detail": detail}
(HERE / "known_issues.json").write_text(json.dumps(dict(sorted(issues.items())), indent=1) + "\n")
print(f"{len(issues)} known routing issues written")

conv_issues = {}
for conv in casebook.load_conversations():
    res = runner.evaluate_conversation(conv, "offline")
    if not res["ok"]:
        i = res["first_failure"]
        t = res["turns"][i - 1]
        conv_issues[conv["id"]] = {"first_failed_turn": i, "kind": t["kind"], "detail": t["detail"]}
(HERE / "known_issues_conversations.json").write_text(json.dumps(dict(sorted(conv_issues.items())), indent=1) + "\n")
print(f"{len(conv_issues)} known conversation issues written")
