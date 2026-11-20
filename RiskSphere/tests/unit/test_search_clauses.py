"""Stage 4: the document search. Piles, relevance floor, verbatim quoting, routing, read-only.

The embedding gateway is off in these tests, so ranking uses word overlap. The rules
under test (pile separation, the topic-word floor, the quote check, routing) do not
depend on which ranker ran.
"""

import hashlib
import json

import pytest

import agent
import books
import search
import tools

CLAUSES = {c["id"]: c for c in json.loads((books.DATA / "clauses.json").read_text())}


# ----------------------------------------------------------------------------- piles

AGREEMENT_QUERIES = [
    "minimum transfer amount", "threshold", "rounding", "eligible collateral", "what does the csa say",
    "government bonds discount", "gold",  # schedule words asked of the agreement pile
]
SCHEDULE_QUERIES = [
    "government bonds discount", "gold", "equities", "haircut schedule", "minimum transfer amount", "threshold",
]


@pytest.mark.parametrize("query", AGREEMENT_QUERIES)
def test_agreement_pile_never_returns_schedule_text(query):
    for hit in search.search(query, pile="agreement")["hits"]:
        assert CLAUSES[hit["id"]]["pile"] == "agreement" and hit["pile"] == "agreement"


@pytest.mark.parametrize("query", SCHEDULE_QUERIES)
def test_schedule_pile_never_returns_agreement_text(query):
    for hit in search.search(query, pile="haircut_schedule")["hits"]:
        assert CLAUSES[hit["id"]]["pile"] == "haircut_schedule" and hit["pile"] == "haircut_schedule"


def test_every_clause_has_exactly_one_known_pile_and_a_source():
    for c in CLAUSES.values():
        assert c["pile"] in search.PILES, c["id"]
        assert c["source"].strip(), c["id"]


def test_both_piles_are_populated():
    for pile in search.PILES:
        assert sum(1 for c in CLAUSES.values() if c["pile"] == pile) >= 4


def test_unknown_pile_is_an_error():
    with pytest.raises(ValueError):
        search.search("threshold", pile="filings")


def test_counterparty_filter_keeps_only_that_names_agreement_text():
    hits = search.search("minimum transfer amount", counterparty_id="apex", pile="agreement", top=5)["hits"]
    assert hits and all(CLAUSES[h["id"]]["counterparty_id"] == "apex" for h in hits)


# ----------------------------------------------------------------------------- relevance floor

@pytest.mark.parametrize("query", ["governing law of the contract", "what happens on a downgrade", "netting set definition"])
def test_a_question_nothing_answers_returns_no_passage(query):
    assert search.search(query, pile="agreement")["hits"] == []


def test_stop_words_alone_do_not_count_as_a_match():
    assert search.search("what does it say about the", pile="agreement")["hits"] == []


def test_a_weak_second_hit_is_dropped(monkeypatch):
    """A passage scoring under 85% of the best one is noise, even when it shares a word."""
    monkeypatch.setattr(search, "_overlap", lambda q, t: 0.9 if "paragraph 5" in t else 0.5)
    hits = search.search("Meridian minimum transfer amount", counterparty_id="meridian", pile="agreement", top=3)["hits"]
    assert [h["id"] for h in hits] == ["meridian-mta"]


# ----------------------------------------------------------------------------- the tool

def test_tool_returns_the_passage_word_for_word_with_its_source():
    out = tools.run("search_clauses", {"query": "What does the Meridian CSA say about the minimum transfer amount?",
                                       "pile": "agreement", "counterparty": "meridian"})
    assert out["found"] and out["hits"][0]["id"] == "meridian-mta"
    for hit in out["hits"]:
        assert hit["text"] == CLAUSES[hit["id"]]["text"]
        assert hit["source"] == CLAUSES[hit["id"]]["source"]
    assert out["counterparty_id"] == "meridian"


def test_tool_reports_not_found_instead_of_guessing():
    out = tools.run("search_clauses", {"query": "governing law", "pile": "agreement", "counterparty": "apex"})
    assert out["found"] is False and out["hits"] == []


def test_tool_defaults_to_the_agreement_pile():
    out = tools.run("search_clauses", {"query": "rounding", "counterparty": "harbor"})
    assert out["pile"] == "agreement" and out["hits"][0]["id"] == "harbor-rounding"


def test_tool_rejects_an_empty_query_and_an_unknown_name():
    with pytest.raises(ValueError):
        tools.run("search_clauses", {"query": "  "})
    with pytest.raises(ValueError):
        tools.run("search_clauses", {"query": "threshold", "counterparty": "zenith"})


def test_searching_changes_no_stored_file():
    watched = [books.DATA / n for n in ("clauses.json", "csa_terms.csv", "collateral.csv", "limits.csv")]
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in watched]
    for pile in search.PILES:
        tools.run("search_clauses", {"query": "threshold haircut", "pile": pile})
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in watched]


def test_the_tool_is_served_over_mcp(mcp):
    mcp.start()
    names = {t["name"] for t in mcp._request("tools/list", {})["tools"]}
    assert "search_clauses" in names
    out = mcp.call_tool("search_clauses", {"query": "minimum transfer amount", "pile": "agreement", "counterparty": "meridian"})
    assert out["found"] and out["hits"][0]["id"] == "meridian-mta"


# ----------------------------------------------------------------------------- what counts as a document question

DOCUMENT_QUESTIONS = [
    "What does the Meridian CSA say about the minimum transfer amount?",
    "Which paragraph of the Apex agreement covers rounding?",
    "Quote the clause on eligible collateral for Harbor Lane",
    "What does the haircut schedule say about government bonds?",
    "According to the 12 CFR table, what discount applies to gold?",
    "Does the Meridian CSA state a threshold?",
    "Read me the wording of the Apex minimum transfer clause",
]
NUMBER_QUESTIONS = [
    "What is Meridian's threshold?",
    "What is Harbor Lane's CSA threshold and minimum transfer amount?",
    "What does the credit support agreement require Apex to post?",
    "Is Meridian owed a collateral top-up under the CSA?",
    "Quote SPY for me",
    "What haircut applies to corporate bonds rated A to BBB over 5 years?",
    "How much margin does Apex owe?",
]


@pytest.mark.parametrize("question", DOCUMENT_QUESTIONS)
def test_document_questions_are_recognised(question):
    assert agent.is_document_question(question)


@pytest.mark.parametrize("question", NUMBER_QUESTIONS)
def test_questions_for_a_figure_are_not_document_questions(question):
    assert not agent.is_document_question(question)


@pytest.mark.parametrize("question,pile", [
    ("What does the Meridian CSA say about rounding?", "agreement"),
    ("What does the haircut schedule say about government bonds?", "haircut_schedule"),
    ("According to the 12 CFR table, what discount applies to gold?", "haircut_schedule"),
    ("What does the Apex CSA say about haircuts on corporate bonds?", "agreement"),
    ("What does the d499 table say about equities?", "haircut_schedule"),
])
def test_pile_is_chosen_from_the_question(question, pile):
    assert agent.infer_pile(question) == pile


# ----------------------------------------------------------------------------- planning

def test_offline_planner_routes_a_document_question_to_the_search():
    plan = agent.heuristic_plan("What does the Meridian CSA say about the minimum transfer amount?", {})
    assert [s["tool"] for s in plan["steps"]] == ["search_clauses"]
    assert plan["steps"][0]["args"]["counterparty"] == "meridian"
    assert plan["intent"] == ["document_search"]


def test_offline_planner_still_sends_a_figure_question_to_the_calculation():
    plan = agent.heuristic_plan("What is Meridian's threshold?", {})
    assert [s["tool"] for s in plan["steps"]] == ["calculate_margin_call"]


def test_agreement_question_with_no_name_asks_which_counterparty():
    plan = agent.heuristic_plan("What does the CSA say about rounding?", {})
    assert plan["needs_clarification"] and plan["missing_arg"] == "counterparty"


def test_schedule_question_needs_no_counterparty():
    plan = agent.heuristic_plan("What does the haircut schedule say about gold?", {})
    assert not plan.get("needs_clarification")
    assert plan["steps"][0]["args"]["pile"] == "haircut_schedule"


def test_a_pronoun_uses_the_remembered_name():
    plan = agent.heuristic_plan("What does its CSA say about rounding?", {"last_counterparty": "apex"})
    assert plan["steps"][0]["args"]["counterparty"] == "apex"


def test_the_name_asked_for_is_used_for_the_follow_up_search():
    cfg = agent.load_config()
    question = "Which paragraph covers rounding?"
    first = agent.heuristic_plan(question, {})
    state = {"pending_tool": first["pending_tool"]}
    resolved = agent.resolve_pending("Apex Financial", state)
    step = resolved["steps"][0]
    assert step["tool"] == "search_clauses" and step["args"]["counterparty"] == "apex"
    assert step["args"]["query"] == question
    assert cfg  # config loads


def _model_plan(tool, args):
    return {"intent": ["x"], "refuse": False, "refusal_reason": "", "reason": "", "steps": [{"tool": tool, "args": args}]}


def test_a_model_that_sends_a_document_question_to_the_margin_tool_is_corrected():
    cfg = agent.load_config()
    question = "What does the Meridian CSA say about the minimum transfer amount?"
    plan = agent.validate_plan(_model_plan("calculate_margin_call", {"counterparty": "meridian"}), cfg)
    plan = agent.ground_args(question, plan, {})
    assert [s["tool"] for s in plan["steps"]] == ["search_clauses"]
    assert plan["steps"][0]["args"]["counterparty"] == "meridian"


def test_the_search_runs_on_the_users_words_and_the_computed_pile():
    cfg = agent.load_config()
    question = "What does the haircut schedule say about gold?"
    plan = agent.validate_plan(
        _model_plan("search_clauses", {"query": "a rewrite by the model", "pile": "agreement", "counterparty": "apex"}), cfg)
    plan = agent.ground_args(question, plan, {})
    args = plan["steps"][0]["args"]
    assert args["query"] == question and args["pile"] == "haircut_schedule"
    assert args["counterparty"] == ""  # apex was never mentioned


def test_the_prompt_describes_the_tool_and_keeps_figures_on_the_calculators():
    prompt = agent.load_config()["plan_prompt"]
    assert "search_clauses(query, pile, counterparty)" in prompt
    assert "still uses calculate_margin_call or regulatory_haircut" in prompt
    assert "document_search" in prompt


# ----------------------------------------------------------------------------- guardrails

@pytest.mark.parametrize("question", DOCUMENT_QUESTIONS)
def test_document_questions_pass_every_guardrail(question):
    assert agent.prefilter(question, {})["stop"] is None


@pytest.mark.parametrize("question", [
    "Change the Meridian threshold in the CSA to USD 2,000,000",
    "Update the Apex CSA minimum transfer amount to 50,000",
    "Rewrite the Harbor clause so the threshold is 1,000,000",
])
def test_an_edit_of_a_stored_term_is_stopped_by_the_action_check(question):
    assert agent.prefilter(question, {})["stop"] == "action"


@pytest.mark.parametrize("question", [
    "Update me on the Meridian threshold",
    "What is the threshold set to for Apex?",
])
def test_asking_about_a_term_is_not_an_edit(question):
    assert agent.prefilter(question, {})["stop"] is None


# ----------------------------------------------------------------------------- the quote check

PASSAGE = CLAUSES["meridian-mta"]["text"]


def test_quote_ok_accepts_a_verbatim_quote_ignoring_case_spacing_and_end_punctuation():
    assert agent.quote_ok("The Minimum Transfer Amount is USD 250,000.", [PASSAGE])
    assert agent.quote_ok("the minimum  transfer amount is usd 250,000", [PASSAGE])


@pytest.mark.parametrize("quote", [
    "The Minimum Transfer Amount is USD 205,000",       # digits swapped
    "The Minimum Transfer Amount is about USD 250,000",  # a word added
    "Meridian may reduce the Minimum Transfer Amount",  # invented
    "",
])
def test_quote_ok_rejects_anything_that_is_not_in_the_passage(quote):
    assert not agent.quote_ok(quote, [PASSAGE])


def test_quoted_strings_finds_straight_and_curly_quotes_and_skips_short_ones():
    text = 'It says "A delivery smaller than the Minimum Transfer Amount is not required" and “USD 250,000 applies here”, "ok".'
    assert len(agent.quoted_strings(text)) == 2


def _row(hits):
    return [{"tool": "search_clauses", "ok": True, "output": {"found": bool(hits), "hits": hits}}]


HIT = [{"id": "meridian-mta", "text": PASSAGE, "score": 0.9, "pile": "agreement", "source": "x"}]


def test_critic_keeps_wording_that_quotes_the_passage():
    wording = 'Paragraph 5 says "The Minimum Transfer Amount is USD 250,000." This is the agreement text.'
    assert agent.critic(wording, _row(HIT)) == []


def test_critic_rejects_a_quote_that_is_not_in_the_passage():
    wording = 'Paragraph 5 says "The Minimum Transfer Amount is USD 300,000."'
    issues = agent.critic(wording, _row(HIT))
    assert any("not in the passage" in i for i in issues)


def test_critic_rejects_wording_that_quotes_nothing():
    issues = agent.critic("The agreement sets a minimum transfer of about a quarter million.", _row(HIT))
    assert any("no passage is quoted" in i for i in issues)


def test_critic_rejects_an_invented_figure_even_next_to_a_good_quote():
    wording = 'It says "The Minimum Transfer Amount is USD 250,000." The threshold is 750,000.'
    assert any("ungrounded" in i for i in agent.critic(wording, _row(HIT)))


def test_critic_requires_wording_to_say_when_nothing_was_found():
    assert agent.critic("The agreement requires a threshold of 1,000,000.", _row([])) != []
    assert agent.critic("No passage was found for that question.", _row([])) == []


def test_a_document_shaped_question_about_another_subject_is_out_of_scope(monkeypatch):
    """The 'What does X say about Y' frame alone must not pass the semantic scope fallback."""
    monkeypatch.setattr(
        "search.match_intents",
        lambda q, threshold=None, top=None: [{"intent": "document_search", "tool": "search_clauses", "score": 0.9, "mode": "x"}],
    )
    assert agent.prefilter("What does the cafeteria menu say about lunch?", {})["stop"] == "scope"


# ----------------------------------------------------------------------------- live regressions, replayed
# Each test feeds plan_with_model the exact JSON the hosted model returned on 2026-11-20.

def _replay(monkeypatch, steps, intent):
    reply = json.dumps({"intent": intent, "refuse": False, "refusal_reason": "", "reason": "r", "steps": steps})
    monkeypatch.setattr(agent, "chat", lambda prompt, max_tokens=None: reply)


def test_live_gold_11_a_figure_question_sent_to_the_search_gets_the_keyword_plan(monkeypatch):
    _replay(monkeypatch, [{"tool": "search_clauses", "args": {"query": "credit support agreement require Apex to post",
                                                              "pile": "agreement", "counterparty": "apex"}}], ["search_clauses"])
    plan = agent.plan_with_model("What does the credit support agreement require Apex to post?", {})
    assert [s["tool"] for s in plan["steps"]] == ["calculate_margin_call"]
    assert plan["steps"][0]["args"]["counterparty"] == "apex"


def test_live_gold_12_an_invented_asset_class_does_not_crash_and_a_named_book_goes_to_margin(monkeypatch):
    _replay(monkeypatch, [{"tool": "regulatory_haircut", "args": {"asset_class": "collateral", "maturity_bucket": "",
                                                                  "rating_bucket": ""}}], ["regulatory_haircut"])
    plan = agent.plan_with_model("What haircut does Apex's collateral get?", {})
    assert [s["tool"] for s in plan["steps"]] == ["calculate_margin_call"]
    assert plan["intent"] == ["margin_call"]


def test_live_gold_19_other_equities_are_not_the_main_index(monkeypatch):
    _replay(monkeypatch, [{"tool": "regulatory_haircut", "args": {"asset_class": "equity_main_index", "maturity_bucket": "",
                                                                  "rating_bucket": ""}}], ["regulatory_haircut"])
    plan = agent.plan_with_model("What haircut do other equities get?", {})
    assert plan["steps"][0]["args"]["asset_class"] == "equity_other"


def test_an_invented_asset_class_with_nothing_stated_asks_for_one(monkeypatch):
    _replay(monkeypatch, [{"tool": "regulatory_haircut", "args": {"asset_class": "collateral"}}], ["regulatory_haircut"])
    plan = agent.plan_with_model("What is the haircut?", {})
    assert plan["needs_clarification"] and plan["missing_arg"] == "asset_class"


def test_the_model_choice_stands_when_the_question_states_nothing_different(monkeypatch):
    _replay(monkeypatch, [{"tool": "regulatory_haircut", "args": {"asset_class": "gold"}}], ["regulatory_haircut"])
    plan = agent.plan_with_model("What haircut applies to gold?", {})
    assert plan["steps"][0]["args"]["asset_class"] == "gold"


def test_a_real_document_question_keeps_the_search(monkeypatch):
    _replay(monkeypatch, [{"tool": "search_clauses", "args": {"query": "q", "pile": "agreement", "counterparty": "apex"}}],
            ["document_search"])
    plan = agent.plan_with_model("What does the Apex CSA say about rounding?", {})
    assert [s["tool"] for s in plan["steps"]] == ["search_clauses"]
