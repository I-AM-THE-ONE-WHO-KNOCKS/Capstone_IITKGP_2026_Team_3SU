# Testing RiskSphere

How to run the tests, how to read the results, and what the results do and do not show. For the full list of cases and tests, see [TEST_CASES.md](TEST_CASES.md), which is generated from the code.

Status date: 2026-11-20. Numbers in section 6 come from the runs listed there.

## 1. What is tested

| Layer | What it checks | Needs network |
|---|---|---|
| Arithmetic | Exposure, status thresholds, haircuts, margin amounts and rounding, and the future-exposure (PFE) simulation, compared with hand-computed values and an independent exact-arithmetic reference (`tests/oracle.py`) | No |
| Agent logic | The six guardrail checks and their order, plan validation, clarification questions, the critic that rejects wrong wording, and the step that drops names or tickers the question never contained | No |
| Real data | The calculator's haircut cells against the real d499 PDF (each cell is either equal to it or labelled as an extension), the PDF download hash check, page cleaning and chunking, and the DTCC trade selection and loading | No |
| Document search (stage 4) | The two piles never mix, a question nothing answers returns no passage, quotes must be word for word, document questions go to the search and figure questions do not, an edit of a term is refused, and the sample sentences agree with the stored terms | No |
| Single-question cases | 198 questions, each with an expected outcome: refused by a named check, a clarification for a named argument, or specific tools and arguments | No (offline planner) or Yes (live planner) |
| Conversations | 38 multi-turn scenarios (106 turns) that check what is remembered between turns: the last counterparty, pronouns, an open clarification, refusals in the middle, restarts | No (offline planner) or Yes (live planner) |
| Harness and data guards | The scoring code, and checks that the case files are well-formed and do not leak | No |

The offline planner is the keyword fallback in `agent.py`. The live planner is Qwen3-8B on the course gateway. Both are scored on the same cases.

## 2. Quick start

Run these from the `RiskSphere` folder. The application itself needs no packages; only pytest is added.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
```

Expected result:

```
1079 passed, 1 skipped, 10 xfailed in about 17 seconds
```

This runs entirely offline. The model and embedding gateways are switched off for every test, the session file and the trace log go to a temporary folder, and market prices are recorded values, so it needs no keys and no Yahoo access. A clean checkout of the repository passes as-is.

Developed and run on Python 3.15 with pytest 9. The code uses only the standard library, but the suite has not been run on older Python versions.

## 3. Reading the results

- **Green with `x`**: an expected failure (xfail). These are known defects, listed in section 7. They are recorded in `tests/eval/known_issues.json` (single questions) and `tests/eval/known_issues_conversations.json` (conversations).
- **Red with `XPASS(strict)`**: a known defect was fixed. This is good news that needs one step: run `python3 tests/eval/triage.py`, review the diff in the two known-issues files, and commit them. The lists are meant to shrink.
- **Red with `FAILED`**: a regression, or a new case that fails. The message names the kind, for example `guard_missed`, `wrong_tools`, `turn_outcome` or `state_pending`.

A failing conversation reports the first wrong turn. Later turns depend on the state that turn left behind, so read the first failure first.

## 4. Scored reports

pytest gives pass or fail. The harness gives rates with 95% confidence intervals, per category, per requirement and per source.

```bash
.venv/bin/python tests/eval/run_eval.py                          # routing and conversations, offline planner
.venv/bin/python tests/eval/run_eval.py --suite conversations    # multi-turn scenarios only
.venv/bin/python tests/eval/run_eval.py --suite routing          # single questions only
.venv/bin/python tests/eval/run_eval.py --planner live           # hosted model (section 5)
.venv/bin/python tests/eval/run_eval.py --planner both
.venv/bin/python tests/eval/run_eval.py --exclude-overlap        # leave out semantic-index examples
```

Reports are written to `tests/results/<timestamp>-<planner>[-conversations].md` and `.json`. The Markdown files are meant to be read and committed; the JSON files are gitignored.

What the headline numbers mean:

| Metric | Definition |
|---|---|
| End-to-end pass | The case outcome matched in full: refusal, clarification, or tools and arguments |
| Unsafe requests blocked | Cases that should be refused and were stopped by any check. The next row requires the right check |
| False refusals | Valid questions that a guardrail stopped |
| Routing, tool set | Right tools chosen (order ignored). The next row also requires the key arguments |
| Clarification precision and recall | Asked when it should, and did not ask when it should not |
| Conversation pass | Every turn right, outcome and memory after the turn |
| Turns pass, up to first failure | Per-turn accuracy that ignores turns after a conversation has already gone wrong |

By default every case is scored. Cases whose question is an exact copy of a semantic-index example in `data/intents.json` are also reported on a separate line, because the matcher has seen those.

## 5. Live testing with the hosted model

Prerequisites:

1. `secrets.local.env` filled in, with `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_CA_CERT` (an absolute path to `vm04-ca.crt`), and for embeddings `EMBED_API_KEY`, `EMBED_BASE_URL`, `EMBED_CA_CERT`. Use `cp -n secrets.example.env secrets.local.env` for the first copy. Plain `cp` overwrites an existing file with the blank template, which silently turns the live model off.
2. A check that the model is on:
   ```bash
   python3 -c "import agent; agent.load_secrets(); print(agent.llm_enabled())"   # must print True
   ```

Then run `run_eval.py --planner live`. The harness paces itself at 1.5 seconds per question to stay under the gateway limit of 60 requests a minute with one request at a time. A full run of both suites takes about 12 to 15 minutes and prints nothing until it finishes. A single suite takes about half of that.

To run the pytest cases against the live planner instead of the offline one: `RUN_LIVE_EVAL=1 .venv/bin/pytest -q tests/eval`. Known-issue markers are not applied in that mode, so expect different failures.

A live run sends the test questions and the planner prompt to the course gateway. It sends no book data and no secrets.

## 5a. Real data

The suite never downloads anything. Two optional scripts fetch real data, and the tests that need it are skipped where it has not been fetched.

```bash
.venv/bin/python scripts/fetch_documents.py     # BCBS-IOSCO d499 PDF -> data/docs/index/ (gitignored)
.venv/bin/python scripts/build_dtcc_book.py     # DTCC swap reports -> data/dtcc/trades_dtcc.csv (gitignored)
```

On a fresh checkout one test is skipped (`test_real_d499_index_contains_appendix_b_and_the_minimum_transfer_rule`). Once `fetch_documents.py` has run, it runs too, and the count is 1080 passed, 10 xfailed. Apart from that one test, no test reads the local index: `conftest.py` points the search at an empty folder, so every other result is the same on every machine.

`RISKSPHERE_TRADES=dtcc/trades_dtcc.csv` runs the app on the real-terms book. The tests always use the sample book.

## 6. Latest results

Single questions, 198 cases, including 22 document cases:

| | Offline planner | Live planner (hosted Qwen3-8B) |
|---|---|---|
| End-to-end pass | 196 / 198 (99.0%) | 198 / 198 (100%) |
| Unsafe requests blocked | 48 / 48 | 48 / 48 |
| False refusals | 0 / 150 | 0 / 150 |
| Routing, tools and arguments | 132 / 134 | 134 / 134 |
| Document cases | 22 / 22 | 22 / 22 |

Conversations, 38 scenarios and 106 turns:

| | Offline planner | Live planner |
|---|---|---|
| Whole conversations pass | 32 / 38 (84.2%) | 32 / 38 (84.2%) |
| Turns pass | 99 / 106 (93.4%) | 99 / 106 (93.4%) |

The live figures come from the run `20261120-221246` (reports in `tests/results/`). The offline figures were re-run on the same day.

How to read these numbers:

- **They are regression results, not an accuracy estimate.** The guardrails, the planner prompt and the keyword rules were changed after looking at these exact cases. Scores on cases the code was tuned against overstate performance on new questions. An honest estimate needs the frozen held-out set (section 8).
- **The first live run after adding the document search scored 194 / 198** (run `20261120-213425`). Three were regressions: a figure question sent to the search (GOLD-11), an asset class the model invented (GOLD-12), and "other equities" read as main-index equities (GOLD-19). Each is now corrected in Python after the model plans, and a unit test replays the model's exact output. The fourth, DOC-17, had an ambiguous expected answer and was replaced with a clearer question; the reason is in the case's notes.
- **Intervals are wide.** For example, 100% on 198 cases has a 95% lower bound of about 98%, on this question distribution only.
- **Live runs are a single run at temperature 0**, on a model that can change on the gateway.
- **An earlier live run scored 100% on a smaller set** because it excluded 11 questions that are semantic-index examples. Two of those 11 failed live. The harness now scores everything by default.

## 7. Known issues

Ten cases fail by design of the current code and are recorded as strict expected failures.

| Id | What happens |
|---|---|
| CONV-21 | "Check margin call", then "What is Apex's PFE?" runs the margin call. Any later question that names a counterparty is taken as the answer to the open question |
| CONV-33, CONV-34 | The session file is never reset at start-up, so a new run keeps the previous run's counterparty and open question |
| CONV-09, CONV-10 | A reply that does not answer the open question ("Zenith Bank", "I am not sure") gets a "no tool matched" refusal instead of the question being asked again |
| CONV-11 | A counterparty name given in reply to a ticker question is answered as an exposure question. The expected behaviour here is a contested label |
| GOLD-25 | "Compare Meridian and Apex exposure" returns one counterparty. The planner takes one counterparty per step |
| PARA-22 | "Where are we today and where could we get to?" returns only the future-exposure tool in keyword mode. The live planner passes it |
| Search near miss: interest | "What interest is paid on Harbor Lane's cash collateral?" matches the eligibility passage, which shares "cash collateral". The search alone cannot tell. With the model on, the answer check rejects it |
| Search near miss: crypto | "What haircut applies to crypto assets?" matches the d499 currency row on the word "asset". With the model on, the answer check rejects it |

CONV-21, CONV-33 and CONV-34 are product bugs and still unfixed. The expected behaviour for the conversation cases where the README is silent was agreed in advance: a new full question drops an open one, a refusal keeps memory and an open question, and a new run starts empty.

## 8. Limits of this suite

- **No held-out set yet.** Every case is in the `dev` split. The frozen `test` split (blind questions written by teammates) does not exist, and `run_eval.py --splits test` stops with a message saying so. Do not tune against that split once it exists.
- **Some labels are unreviewed.** The 29 paraphrase cases were written by a model (claude-sonnet-5-5), and their labels have not been checked by a person. A few other labels are marked contested in `TEST_CASES.md`.
- **Six requirements have no test:** the gateway retry waits (R26), the keyword fallback flag (R24), the English-only rule (R25), the 1,000-character limit (R27), tool failure over MCP (R28), and the API key never being logged (R29).
- **Conversation tests mock the calculation server.** They test planning and memory, not the numbers. The arithmetic is tested separately.
- **The guardrails are regex lists.** The adversarial cases sample how they fail. They do not prove a new phrasing cannot get through.
- **Live results depend on the gateway.** A rate-limit response or a model change can alter a run.

## 9. Changing the tests

1. Edit or add a case in `tests/eval/generate.py`. Write the expected outcome from the README rule, never from what the code returns today.
2. Regenerate and check: `python3 tests/eval/generate.py`, then `.venv/bin/pytest -q`.
3. If the new case fails because of a real defect, run `python3 tests/eval/triage.py` to record it as a known issue, and review the diff.
4. Regenerate the catalogue: `python3 tests/eval/build_catalog.py`.

Rules:

- Every case needs a `source`, a `split` and at least one `requirement_id` from `tests/eval/requirements.json`. The data guards in `tests/eval/test_casebook.py` and `test_conversations.py` enforce this.
- A case marked `index_overlap` must really be near-identical to an example in `data/intents.json`, and a held-out case must never be.
- The harness never touches `data/session_state.json` or `outputs/traces.jsonl`. A test enforces it.

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `pytest: command not found` | Use `.venv/bin/pytest`, or activate the virtual environment first |
| The app or a live run says the model was not called, or traces show `rules_only` | An API key is empty, or `PFE_LLM_OFF=1` is set. Check `secrets.local.env` and run `env \| grep PFE_` |
| Live run exits with "live planner needs LLM_API_KEY and LLM_BASE_URL" | The same cause. Fill in `secrets.local.env`, then run the `llm_enabled()` check in section 5 |
| Certificate error on a live run | `LLM_CA_CERT` or `EMBED_CA_CERT` is not an absolute path to a readable certificate file |
| Many `429` or `503` responses | The gateway allows 60 requests a minute and one at a time. Do not run two live runs together |
| A test is red with `XPASS(strict)` | A known defect was fixed. See section 3 |
| `No cases in split(s) 'test'` | Expected: the held-out split has not been written yet |
| A new run starts with a "Reply (or new question):" prompt | Leftover state from the previous run in `data/session_state.json`. This is the restart bug in section 7. Delete that file to start clean |

## 11. File map

```
tests/
  conftest.py                     isolation: temp state, gateways off, recorded prices
  oracle.py                       independent exact-arithmetic reference
  TEST_CASES.md                   generated catalogue of every case and test
  TESTING.md                      this file
  unit/                           arithmetic, PFE, agent logic, guardrails, pipeline
  eval/
    requirements.json             41 requirements, each traced to the README or an agreed default
    cases/*.jsonl                 198 single questions (document.jsonl holds the 22 stage 4 cases)
    conversations/*.jsonl         38 multi-turn scenarios
    generate.py                   builds the case files
    casebook.py                   loads and validates cases
    runner.py                     runs one case or conversation through the real code
    run_eval.py                   scored reports
    triage.py                     snapshots known issues
    build_catalog.py              builds TEST_CASES.md
    known_issues*.json            strict expected failures
  results/                        saved reports (.md committed, .json ignored)
```
