"""Builds tests/TEST_CASES.md from the case files, requirements and the collected pytest tests.

Run:  python3 tests/eval/build_catalog.py
The document is generated, so its counts always match what actually runs.
"""

import collections
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import casebook  # noqa: E402

OUT = HERE.parent / "TEST_CASES.md"

THEMES = [
    ("1. Routing: one question, one calculation", "The planner must pick the right tool and fill its arguments. Checked on the tool name(s) and the key arguments (counterparty, ticker, asset class and buckets).", {
        "direct_exposure": "Seed question phrased close to the README wording: today's exposure.",
        "direct_pfe": "Seed question: future exposure.",
        "direct_margin": "Seed question: margin call.",
        "direct_market_data": "Seed question: spot price and volatility of a ticker.",
        "direct_haircut": "Seed question: haircut for corporate bonds, A to BBB, over 5 years.",
        "exposure": "Today's exposure, headroom, utilisation, breach, mark-to-market, written from the README rules (R08).",
        "pfe": "Potential future exposure, simulated peak, PFE check (R09).",
        "margin": "Collateral to call, CSA threshold and minimum transfer, return of collateral, what the agreement requires (R10).",
        "market": "Spot, volatility and last price for a named ticker (R11).",
        "haircut": "A haircut cell with no counterparty named: asset class and, when stated, maturity and rating (R12).",
        "margin_haircut_with_name": "Haircut asked about a named counterparty's collateral. The README sends this to the margin calculation, not the table.",
        "paraphrase_exposure": "Seed paraphrase of an exposure question.",
        "paraphrase_pfe": "Seed paraphrase of a PFE question.",
        "near_miss_safe_margin": "Contains the words 'margin call' inside a hypothetical. It must route, not be refused.",
        "tpl_exposure": "Template grid: 3 exposure phrasings x 3 counterparties.",
        "tpl_pfe": "Template grid: 3 PFE phrasings x 3 counterparties.",
        "tpl_margin": "Template grid: 3 margin phrasings (delivery amount, how much to post, return due) x 3 counterparties.",
        "tpl_market": "Template grid: 2 phrasings x 4 tickers (SPY, SHY, LQD, EURUSD=X).",
        "tpl_haircut": "Template grid: haircut for gold, cash, main index equities, other equities.",
        "tpl_haircut_args": "Template grid: government/corporate x AAA-AA/A-BBB x three maturities. Checks the bucket extraction (12 cases).",
        "para_exposure": "Claude-written natural paraphrases of exposure questions.",
        "para_pfe": "Claude-written paraphrases of PFE questions (forward-looking, percentile, simulate).",
        "para_margin": "Claude-written paraphrases of margin questions (top-up, outstanding variation margin, margin terms).",
        "para_market": "Claude-written paraphrases of price and volatility questions.",
        "para_haircut": "Claude-written paraphrases of haircut questions (discount, knocked off, long-dated).",
    }),
    ("2. Routing: several calculations in one question", "A question that asks for more than one thing must produce more than one step (at most four).", {
        "multi": "Exposure plus margin, exposure plus PFE, price plus margin, today versus future (R13).",
        "multi_tool": "Seed: exposure and margin call for one counterparty.",
        "multi_party": "Two counterparties in one question. A known design limit of the keyword planner.",
        "para_multi": "Claude-written multi-intent questions.",
    }),
    ("3. Session memory", "'it', 'its', 'their' and 'them' use the remembered counterparty. Nothing else does.", {
        "followup": "Pronoun plus a remembered counterparty: the name is reused (R14).",
        "session_followup": "Seed follow-up ('And its limit?').",
        "followup_no_pronoun": "A remembered counterparty must NOT be reused when the question has no pronoun: it asks instead.",
        "pronoun_no_state": "A pronoun with nothing remembered: it asks instead of guessing.",
    }),
    ("4. Clarification: ask, do not guess", "A missing counterparty, ticker or asset class must produce a question to the user, naming the right missing argument (R15).", {
        "vague": "Exposure, PFE and collateral questions with no counterparty.",
        "vague_margin": "Seed: margin call with no counterparty.",
        "vague_market": "Seed: price with no ticker.",
        "vague_haircut": "Seed: haircut with no asset class.",
        "unknown_counterparty": "A name that is not on the book (not Meridian, Apex or Harbor Lane). Must ask, not guess.",
        "adv_typo": "A misspelt counterparty name. Must ask, not guess.",
    }),
    ("5. Guardrails: requests that must be refused", "The first failing check stops the question before any model or calculation (R02-R07). Checked on which check fired.", {
        "guardrail_injection": "Override the rules, reveal the key or the system prompt (R02).",
        "guardrail_pii": "Home address, passport, personal phone number (R03).",
        "guardrail_action": "Send, approve, book, pay, or change a limit (R04).",
        "guardrail_legal": "Legal advice, enforceability, suing, terminating the agreement (R05).",
        "guardrail_scope": "Not about the book at all: a poem, the capital of France, a restaurant (R06).",
        "para_injection": "Claude-written paraphrase of an injection attempt.",
        "para_pii": "Claude-written paraphrase of a personal data request.",
        "para_action": "Claude-written paraphrases of action requests (wire, authorise, lift a limit).",
        "para_legal": "Claude-written paraphrase of a legal request.",
        "para_scope": "Claude-written off-topic question.",
        "adv_injection": "Injection variants: upper case, 'forget everything', 'bypass the guardrails', 'print your system prompt', a line break.",
        "adv_mixed": "A safe question joined to an unsafe one: the unsafe part must still stop the whole question (R07).",
        "adv_order": "Two violations in one question. The earlier check in the fixed order must be the one that fires (R07).",
        "adv_scope": "Off-topic requests that contain a desk word ('a joke about collateral').",
    }),
    ("6. Robustness: valid questions that look risky or noisy", "Tests for false refusals and for odd surface forms.", {
        "adv_false_positive": "Valid desk questions containing words like 'call' or 'transfer'. They must NOT be refused.",
        "adv_surface": "Upper case, lower case without punctuation, a curly apostrophe, extra spaces.",
    }),
    ("7. Documents (stage 4)", "Questions about what a document says go to the document search; questions for a figure stay on the calculations (R36-R40).", {
        "doc_agreement": "What a named counterparty's sample CSA says: threshold, minimum transfer, rounding, eligible collateral. Expects the search_clauses tool, the agreement pile and the right name.",
        "doc_schedule": "What the haircut schedule says (the calculator's table or the 12 CFR table). Expects the haircut_schedule pile and no counterparty.",
        "doc_clarify": "An agreement question with no counterparty. It must ask which one (R39).",
        "doc_followup": "'its' uses the remembered name for a document question, and a remembered name is not used without a pronoun (R14).",
        "doc_not_a_document_question": "Questions for a figure that mention a threshold, a minimum transfer or a haircut. They must stay on the calculators, not the search (R36).",
        "doc_edit_refused": "Requests to change a stored term or clause are stopped by the action check; a document question that asks for legal advice is stopped by the legal check (R40, R05).",
    }),
]

CONV_CATS = {
    "clarify_resolve": "The system asks for a missing counterparty, ticker or asset class and the user's next reply completes the question (R15, R30).",
    "clarify_retry": "The reply does not supply the value (unknown name, 'I am not sure', the wrong kind of value, repeating the question). It must ask again and keep the open question (R31).",
    "memory_pronoun": "'its', 'their' and 'them' resolve to the counterparty from earlier turns, including two tools in one question (R14).",
    "memory_switch": "Naming a different counterparty replaces the memory; 'its' then means the new one (R35).",
    "memory_neutral_turn": "A price or haircut question between two counterparty questions must not wipe the memory (R35).",
    "memory_no_pronoun": "Memory is NOT used when the question has no pronoun: it asks instead (R14, R15).",
    "interrupted_clarify": "The user ignores the open question and asks something new. The new question is answered as asked and the old one is dropped (R32).",
    "refusal_midway": "A refused turn (action, injection, scope, legal, PII) in the middle of a conversation. Memory survives, and a pending question survives (R33).",
    "kill_switch": "The kill switch is on for one turn only: that turn is refused, memory is untouched, service resumes afterwards (R01, R33).",
    "long_session": "6 to 9 turn sessions mixing every behaviour above.",
    "fresh_session": "After a restart there is no memory and no pending question (R34).",
    "document_search": "Document questions inside a conversation: the name is remembered, a missing name is asked for, and a refused edit keeps the memory (R36, R39, R40).",
}

UNIT = [
    ("tests/unit/test_math.py", "Financial arithmetic on the sample book", {
        "test_regulatory_haircut_schedule": "Spot-checks four cells of the haircut table (cash 0%, corporate >5y A-BBB 12%, government 1-5y AAA-AA 2%, main index equity 15%).",
        "test_unknown_asset_class_rejected": "An unknown asset class such as crypto raises an error.",
        "test_harbor_breach": "With recorded prices, Harbor Lane's exposure is 1,034,960, headroom -34,960, status BREACH.",
        "test_meridian_margin_terms": "Meridian's threshold (1,000,000) and minimum transfer (250,000) are read correctly.",
        "test_pfe_engine_self_checks": "The PFE engine passes its own validation (closed form, netting, collateral, margin period).",
    }),
    ("tests/unit/test_math_book.py", "Exposure and margin arithmetic: hand values, boundaries and an independent oracle", {
        "test_sample_book_golden_values": "Hand-computed exposure, collateral, status and delivery for all three counterparties (e.g. Meridian delivery 680,000, Apex 990,000, Harbor 1,040,000).",
        "test_status_boundaries": "Status at 849,999 OK, 850,000 WARNING, 1,000,000 WARNING (exactly 100% is not a breach), 1,000,001 BREACH.",
        "test_exposure_floored_at_zero": "Over-collateralised: exposure is 0, never negative.",
        "test_negative_mark_counterparty_owes_nothing": "A negative mark gives zero exposure.",
        "test_collateral_counts_after_haircut": "Cash, corporate bond, government bond and equity count at face x (1 - haircut).",
        "test_unmapped_collateral_asset_is_an_error": "An asset with no haircut mapping is an error, not a silent zero.",
        "test_delivery_rounding_and_mta": "Delivery below the minimum transfer is NO_ACTION; at the minimum it is called; rounded UP to the step; an exact multiple is not bumped.",
        "test_return_rounding_and_mta": "Returns follow the same minimum transfer rule and are rounded DOWN.",
        "test_threshold_and_independent_amount": "Credit support = mark + independent amount - threshold.",
        "test_below_threshold_means_no_credit_support": "A mark below the threshold gives zero credit support and no action.",
        "test_code_matches_oracle_on_random_books": "60 random books: the code equals an independent exact-arithmetic oracle on every field, and invariants hold (no delivery and return together, amounts on the rounding step).",
        "test_more_collateral_never_raises_exposure": "20 random books: adding collateral never increases exposure.",
    }),
    ("tests/unit/test_pfe_properties.py", "Future-exposure engine properties and haircut table properties", {
        "test_same_seed_is_deterministic": "Same seed gives identical peaks and fan.",
        "test_different_seed_changes_paths_but_stays_close": "A different seed changes the paths but stays within 12%.",
        "test_today_mark_matches_notional_formula": "Today's mark equals notional x (spot/strike - 1).",
        "test_uncollateralised_peak_not_below_closed_form_at_step_k": "Peak is not below the lognormal closed form at 21, 63 and 126 days, long and short.",
        "test_collateral_never_raises_the_peak": "Collateralised peak never exceeds the uncollateralised peak.",
        "test_huge_threshold_turns_collateral_off": "A huge threshold makes the two peaks equal.",
        "test_offsetting_trades_net_to_near_zero": "Equal and opposite trades net to zero.",
        "test_shorter_margin_period_lowers_collateralised_peak": "A one-day margin period gives a lower collateralised peak (under 35% of uncollateralised).",
        "test_higher_threshold_never_lowers_the_collateralised_peak": "A higher threshold never lowers the collateralised peak.",
        "test_check_limit_status_uses_unrounded_ratio": "A peak of 1,000,000.4 is WARNING; 1,000,001 is BREACH.",
        "test_check_limit_refuses_when_engine_checks_fail": "No number is quoted when the engine self-checks fail.",
        "test_haircut_increases_with_maturity": "Longer maturity never has a smaller haircut.",
        "test_lower_rating_never_has_smaller_haircut": "A-BBB never has a smaller haircut than AAA-AA.",
        "test_corporate_haircut_not_below_government": "Corporate is never below government for the same cell.",
        "test_haircuts_between_0_and_100_and_cash_is_zero": "Every haircut is in [0, 100) and cash is 0.",
        "test_haircut_matches_oracle_table": "The table equals the independent copy in the oracle.",
    }),
    ("tests/unit/test_agent_logic.py", "Guardrail order, plan validation, clarification and the critic", {
        "test_kill_switch_short_circuits": "With the kill switch on, the first check fails and the later checks are reported as not run.",
        "test_guardrail_order": "One example for each check (injection, PII, action, legal, scope) and one clean question.",
        "test_plan_rejects_intent_tool_mismatch": "A plan whose intent and tool disagree is rejected.",
        "test_plan_accepts_multi_intent": "A two-intent, two-tool plan is accepted.",
        "test_vague_query_clarification_and_followup": "'Check margin call' asks for a counterparty; naming one resolves it; 'What is the haircut?' asks for an asset class.",
        "test_critic_accepts_faithful_wording": "Wording that uses the tool's figures and status passes.",
        "test_critic_rejects_hallucination": "A wrong figure, 'within its limit' on a BREACH, and an invented margin call are all rejected.",
        "test_required_arg_holds_every_rule": "One function, required_arg(), says which argument each tool needs: counterparty, ticker or asset class, and for the document search a counterparty only on the agreement pile.",
    }),
    ("tests/unit/test_guardrail_regressions.py", "New guardrail patterns, false-refusal guards and grounding of model arguments", {
        "test_must_stop": "13 phrasings that must be stopped by the named check (injection, action, legal, scope).",
        "test_valid_question_not_refused": "14 valid desk questions with risky-sounding words that must pass every check.",
        "test_counterparty_is_grounded": "A counterparty the model invented is blanked; one named in the question, or reached with a pronoun plus memory, is kept.",
        "test_ticker_is_grounded": "A ticker not present in the question is blanked.",
        "test_two_counterparties_both_kept_when_both_named": "Both names are kept when the user names both.",
        "test_planner_that_guesses_a_name_now_triggers_clarification": "A model that copies the example (the live failure) now ends in a clarification.",
        "test_prompt_contract": "The planner prompt has no named-counterparty or tool example to copy, states the blank-argument rule in prose, and spells out margin and limit wording.",
        "test_haircut_buckets_stated_in_the_question_override_the_model": "Maturity and rating buckets stated in the question replace the model's choice.",
    }),
    ("tests/unit/test_clause_consistency.py", "The sample agreement sentences agree with the stored terms and the haircut table", {
        "test_agreement_clause_matches_csa_terms": "One test per counterparty clause: any threshold or minimum transfer stated in the prose equals csa_terms.csv, no other dollar amount appears, and no other counterparty is named.",
        "test_haircut_note_matches_calculator_table": "Every cell stated in the haircut note equals the table in books.py, and no percentage appears that is not in the table.",
        "test_every_counterparty_has_its_threshold_and_minimum_transfer_stated": "Each stored term has a sentence behind it that can be searched and quoted.",
        "test_clause_ids_unique_and_fields_present": "Clause ids are unique and every clause has id, counterparty, title and text.",
        "test_checker_catches_wrong_threshold": "Planted defect: a wrong threshold is reported.",
        "test_checker_catches_wrong_minimum_transfer": "Planted defect: a wrong minimum transfer amount is reported.",
        "test_checker_catches_stray_amount_and_other_counterparty": "Planted defects: an unexplained dollar amount, and another counterparty's name, are reported.",
        "test_checker_catches_the_old_haircut_note": "The earlier haircut note (1% government, 8% corporate) is reported.",
        "test_checker_catches_a_single_changed_cell": "Changing one haircut cell from 12 to 10 percent is reported.",
        "test_cfr_passage_matches_the_saved_table": "One test per 12 CFR passage: its percentages, in order, equal the rows of the saved Table B (data/docs/haircut_schedule_12cfr45.md).",
        "test_every_cfr_passage_in_clauses_json_is_checked": "A new 12 CFR passage cannot be added without a check.",
        "test_checker_catches_a_changed_cfr_figure": "Planted defect: 8.0 changed to 6.0 in a 12 CFR passage is reported.",
        "test_checker_catches_a_wrong_rounding_clause": "Planted defect: a rounding clause that says USD 5,000 when csa_terms.csv says 10,000 is reported.",
        "test_saved_d499_excerpt_has_the_ten_rows_of_appendix_b": "The saved excerpt of the real d499 PDF (data/docs/d499_appendix_b.md) has all ten rows of Appendix B.",
        "test_calculator_cell_labelled_d499_equals_the_pdf": "One test per calculator cell that claims to come from d499: its value equals the row in the real PDF.",
        "test_cells_missing_from_d499_are_labelled_as_an_extension": "The A+ to BBB- rows and other equity, which the PDF does not contain, are labelled as an extension.",
        "test_every_calculator_cell_is_either_d499_or_labelled_extension": "No cell of the table is unlabelled.",
        "test_haircut_tool_reports_where_the_cell_came_from": "The haircut tool says the 12% cell is not in d499 and the gold cell is.",
        "test_d499_passages_quote_the_saved_excerpt": "The three d499 passages in clauses.json quote the saved excerpt row for row.",
    }),
    ("tests/unit/test_search_clauses.py", "Stage 4 document search: piles, relevance floor, quoting, routing and the edit guard", {
        "test_agreement_pile_never_returns_schedule_text": "For seven queries, including schedule words, the agreement pile returns only agreement passages (R37).",
        "test_schedule_pile_never_returns_agreement_text": "For six queries, including threshold and minimum transfer, the schedule pile returns only schedule passages (R37).",
        "test_every_clause_has_exactly_one_known_pile_and_a_source": "Every passage belongs to one known pile and names its source.",
        "test_both_piles_are_populated": "Each pile has at least four passages.",
        "test_unknown_pile_is_an_error": "A pile name that is not agreement or haircut_schedule raises an error.",
        "test_counterparty_filter_keeps_only_that_names_agreement_text": "Searching Apex's agreement returns only Apex passages.",
        "test_a_question_nothing_answers_returns_no_passage": "Governing law, a downgrade and a netting set return no passage (R39).",
        "test_stop_words_alone_do_not_count_as_a_match": "'what does it say about the' matches nothing.",
        "test_a_weak_second_hit_is_dropped": "A second passage scoring under 85% of the best is dropped.",
        "test_tool_returns_the_passage_word_for_word_with_its_source": "The tool's passage text and source equal the stored ones exactly (R38).",
        "test_tool_reports_not_found_instead_of_guessing": "A question with no answer returns found = false and no hits (R39).",
        "test_tool_defaults_to_the_agreement_pile": "With no pile given, the agreement pile is searched.",
        "test_tool_rejects_an_empty_query_and_an_unknown_name": "An empty query and an unknown counterparty are errors.",
        "test_searching_changes_no_stored_file": "Searching both piles leaves clauses.json, csa_terms.csv, collateral.csv and limits.csv byte-identical (R40).",
        "test_the_tool_is_served_over_mcp": "The real MCP server lists search_clauses in tools/list and answers a tools/call.",
        "test_document_questions_are_recognised": "Seven phrasings of 'what does the document say' are recognised (R36).",
        "test_questions_for_a_figure_are_not_document_questions": "Seven questions for a figure, including 'Quote SPY for me', are not (R36).",
        "test_pile_is_chosen_from_the_question": "A named regulation or table picks the schedule, a named agreement picks the agreement, then topic words decide.",
        "test_offline_planner_routes_a_document_question_to_the_search": "The keyword planner returns the search tool, the name and the document_search intent.",
        "test_offline_planner_still_sends_a_figure_question_to_the_calculation": "'What is Meridian's threshold?' still goes to the margin calculation.",
        "test_agreement_question_with_no_name_asks_which_counterparty": "An agreement question with no name asks which counterparty (R39).",
        "test_schedule_question_needs_no_counterparty": "A schedule question runs without a name.",
        "test_a_pronoun_uses_the_remembered_name": "'its CSA' uses the remembered counterparty.",
        "test_the_name_asked_for_is_used_for_the_follow_up_search": "After 'Which paragraph covers rounding?' and the reply 'Apex Financial', the original question is searched for Apex.",
        "test_a_model_that_sends_a_document_question_to_the_margin_tool_is_corrected": "If the model picks the margin tool for a document question, Python replaces it with the search.",
        "test_the_search_runs_on_the_users_words_and_the_computed_pile": "The search query is the user's question and the pile is computed in Python; a model's rewrite, its pile and an unmentioned name are discarded.",
        "test_the_prompt_describes_the_tool_and_keeps_figures_on_the_calculators": "The planner prompt describes search_clauses and says figures still go to the calculators.",
        "test_document_questions_pass_every_guardrail": "Seven document questions are not refused by any check.",
        "test_an_edit_of_a_stored_term_is_stopped_by_the_action_check": "'Change the Meridian threshold in the CSA to...' and two similar edits are stopped by the action check (R40).",
        "test_asking_about_a_term_is_not_an_edit": "'Update me on the Meridian threshold' and 'What is the threshold set to for Apex?' are not refused.",
        "test_quote_ok_accepts_a_verbatim_quote_ignoring_case_spacing_and_end_punctuation": "A verbatim quote passes the quote check (R38).",
        "test_quote_ok_rejects_anything_that_is_not_in_the_passage": "Swapped digits, an added word, an invented sentence and an empty quote fail the check.",
        "test_quoted_strings_finds_straight_and_curly_quotes_and_skips_short_ones": "Quotations are found inside straight and curly quotes; very short ones are ignored.",
        "test_critic_keeps_wording_that_quotes_the_passage": "Wording that quotes the passage word for word stays.",
        "test_critic_rejects_a_quote_that_is_not_in_the_passage": "Wording with an altered quotation is dropped (R38).",
        "test_critic_rejects_wording_that_quotes_nothing": "Wording that paraphrases without quoting is dropped.",
        "test_critic_rejects_an_invented_figure_even_next_to_a_good_quote": "An invented number beside a good quote is still caught.",
        "test_critic_requires_wording_to_say_when_nothing_was_found": "When nothing was found, wording must say so, and must not state a term (R39).",
        "test_a_document_shaped_question_about_another_subject_is_out_of_scope": "The 'What does X say about Y' frame alone does not pass the semantic scope fallback.",
        "test_live_gold_11_a_figure_question_sent_to_the_search_gets_the_keyword_plan": "Replays the live model sending 'What does the credit support agreement require Apex to post?' to the search; Python uses the keyword plan, the margin call.",
        "test_live_gold_12_an_invented_asset_class_does_not_crash_and_a_named_book_goes_to_margin": "Replays the live model inventing asset class 'collateral'; it is cleared, and a haircut on a named counterparty goes to its margin call.",
        "test_live_gold_19_other_equities_are_not_the_main_index": "Replays the live model reading 'other equities' as main-index; the question's 'other' wins.",
        "test_an_invented_asset_class_with_nothing_stated_asks_for_one": "An invented asset class with none stated in the question becomes a clarification.",
        "test_the_model_choice_stands_when_the_question_states_nothing_different": "A correct model choice is left alone.",
        "test_a_real_document_question_keeps_the_search": "The fallback does not touch a real document question.",
        "test_a_company_or_document_name_alone_is_not_a_match": "Five questions whose only shared words are a company or document name (Apex, CSA, 12 CFR, d499) return no passage. Found by hand: 'What does the Apex CSA say about governing law?' used to return two Apex passages.",
        "test_the_subject_of_the_question_still_finds_its_passage": "Questions that name a company and a real subject (minimum transfer, rounding, eligible collateral, GSE debt, gold) still find their passage.",
        "test_known_issue_interest_on_cash_collateral_is_not_answered_by_eligibility": "Known issue (strict xfail): a question about interest on cash collateral matches the eligibility passage because both mention cash collateral.",
        "test_known_issue_crypto_assets_are_not_in_the_schedule": "Known issue (strict xfail): a crypto haircut question matches the currency-mismatch row on the word 'asset'.",
    }),
    ("tests/unit/test_documents.py", "Real documents for the search: download check, cleaning, chunking and loading", {
        "test_clean_page_drops_running_headers_and_joins_lines": "The running page header of the PDF is removed and lines are joined.",
        "test_clean_page_keeps_a_sentence_that_only_mentions_the_title": "A sentence that mentions the document title is not mistaken for a header.",
        "test_chunks_are_whole_sentences_within_the_limit_and_never_cross_a_page": "Passages are whole sentences, within the length limit, on one page, with nothing lost or reordered.",
        "test_a_sentence_longer_than_the_limit_is_kept_whole": "A very long sentence is not cut in the middle.",
        "test_chunk_ids_are_unique_and_name_the_page": "Passage ids are unique and carry the page number for citation.",
        "test_passages_take_the_shape_of_clauses_json_with_a_page_citation": "Indexed passages have the same fields as clauses.json and cite the page and the copyright holder.",
        "test_a_download_with_the_wrong_hash_is_deleted_and_refused": "A download whose SHA-256 differs from the pinned one is deleted and never indexed.",
        "test_a_download_with_the_right_hash_is_kept": "A download with the pinned SHA-256 is kept.",
        "test_no_index_means_only_clauses_json": "Without a local index the search uses clauses.json alone.",
        "test_indexed_passages_are_searched_and_stay_in_their_pile": "An indexed d499 passage is found in the schedule pile and never in the agreement pile.",
        "test_an_indexed_passage_can_be_quoted_word_for_word": "A quotation from an indexed passage passes the quote check; an altered figure fails it.",
        "test_table_of_contents_lines_are_never_indexed": "Contents lines with dot leaders are removed before passages are built, so they never join real text.",
        "test_a_sentence_that_runs_over_a_page_break_is_one_passage": "A sentence split by a page break, with a lowercase continuation, is kept whole and cites both pages.",
        "test_a_capitalised_start_on_the_next_page_is_a_new_sentence": "A new sentence starting on the next page is not joined to the previous page.",
        "test_bullet_items_are_separate_sentences": "Bullet items (•) each start their own sentence.",
        "test_citation_names_a_page_range_when_a_passage_spans_two_pages": "The citation says 'PDF pages 1-2' for a passage across two pages and 'PDF page 3' for one page.",
        "test_prune_removes_only_old_versions_of_the_documents_passages": "Pruning removes stale cached vectors of the rebuilt document's passages only; the clauses.json excerpts (d499-appendix-b-...), sample clauses, intent examples and other documents stay.",
        "test_prune_with_nothing_stale_leaves_the_file_untouched": "When nothing is stale, the cache file is not rewritten.",
        "test_rebuilding_the_index_prunes_the_cache": "Rebuilding the index removes the cached vector of a passage whose text changed and reports how many were removed.",
        "test_real_d499_index_contains_appendix_b_and_the_minimum_transfer_rule": "Runs only where the PDF was fetched: the real index has Appendix B, the EUR 500,000 minimum transfer rule, and page citations.",
    }),
    ("tests/unit/test_dtcc.py", "A trade book built from real DTCC public swap reports", {
        "test_index_swap_keeps_the_real_size_level_and_dates": "A real S&P 500 index swap keeps its notional, index level and expiry, marked on the total return index.",
        "test_index_swap_filters": "Other underliers, amendments, terminations, non-trade events, expired trades and non-USD notionals are dropped.",
        "test_index_swap_with_a_nonsense_level_is_dropped": "An empty, zero, non-numeric or absurd index level is dropped.",
        "test_forward_reads_the_eur_leg_whichever_side_it_is_on": "The EUR notional is read from whichever leg holds it.",
        "test_forward_filters": "Options, non-deliverable forwards, a non EUR/USD rate, amendments and expired trades are dropped.",
        "test_selection_is_deterministic_and_limited": "The same report always gives the same trades, lowest id first.",
        "test_book_rows_say_what_is_invented_and_where_each_trade_came_from": "Counterparty and direction are marked as invented; each row keeps its DTCC report and dissemination id.",
        "test_the_sample_book_is_the_default": "Without RISKSPHERE_TRADES the sample trades.csv is used.",
        "test_the_app_loads_a_dtcc_book_through_the_environment": "With RISKSPHERE_TRADES set, the app marks a real-terms trade: 1,000 index units bought at 16,000 and marked at 15,000 is a 1,000,000 loss and zero exposure.",
    }),
    ("tests/unit/test_answer_check.py", "The answer check: one Qwen yes/no call that replaces the wording call for a document question", {
        "test_yes_shows_the_chosen_passage_word_for_word_in_two_calls": "Qwen names a passage: that passage is the answer, word for word, and the question used exactly two model calls (plan + check).",
        "test_yes_can_pick_a_passage_that_was_not_ranked_first": "Qwen may choose the second-ranked passage; it is then shown first.",
        "test_no_says_so_first_and_still_shows_the_closest_passage_labelled": "Qwen says none answers: the first line says no passage answers directly, the second quotes the closest passage with its source, labelled as judged not to answer; nothing is removed from the result.",
        "test_a_wrong_no_still_lets_the_reader_see_the_real_answer": "The live GSE case: when the model wrongly says no, the passage text still reaches the reader.",
        "test_an_unusable_reply_keeps_the_search_result_marked_unchecked": "An invented id, a non-boolean answer, a missing field or a reply with no JSON keeps the best search passage, marked 'not checked by the model'.",
        "test_a_gateway_failure_during_the_check_keeps_the_search_result": "A gateway error during the check falls back the same way.",
        "test_nothing_found_makes_no_check_call": "When the search finds nothing, no check call is made: one model call in total.",
        "test_model_off_shows_the_best_passage_unchecked_with_no_call": "With the model off: no call, and the best passage is shown marked unchecked.",
        "test_the_check_prompt_carries_the_question_and_every_passage_id": "The check prompt contains the question, every passage id, and the line that passages are data, not instructions.",
        "test_text_inside_a_passage_cannot_reach_the_user": "A passage containing instructions can at worst flip the yes/no; it reaches the user only as a quoted, sourced passage.",
        "test_calculator_questions_still_use_the_wording_step": "Calculator questions are unchanged: their second call is still the wording call.",
    }),
    ("tests/unit/test_pipeline.py", "End-to-end question handling with a mocked model and calculation server", {
        "test_two_turn_clarification": "'Check margin call' asks for a name, 'Meridian Capital' completes it, and the session state is updated and cleared.",
        "test_critic_fallback_drops_bad_wording": "Bad model wording is rewritten once, then dropped; the Python result is still returned.",
    }),
    ("tests/eval/test_eval_dataset.py", "Runs every routing case below through the real agent code", {
        "test_eval_case": "One test per case: guardrail stop, clarification, tools and arguments.",
        "test_known_issues_refer_to_real_cases": "known_issues.json only lists cases that exist.",
    }),
    ("tests/eval/test_conversations.py", "Plays the multi-turn scenarios (section 3) and guards their data", {
        "test_conversation": "One test per conversation: every turn's outcome and the memory/pending state after it.",
        "test_known_conversation_issues_refer_to_real_conversations": "known_issues_conversations.json only lists conversations that exist.",
        "test_conversation_schema": "Every conversation and turn has valid fields and expectations.",
        "test_conversation_ids_unique_and_nonempty": "Conversation ids are unique.",
        "test_real_session_file_is_never_touched": "The runner uses a temporary session file, never data/session_state.json.",
    }),
    ("tests/eval/test_casebook.py", "Guards the evaluation data itself", {
        "test_there_are_cases": "The case set is not empty.",
        "test_case_schema": "Every case has all fields, valid values, and consistent expectations.",
        "test_ids_unique": "No duplicate ids.",
        "test_queries_unique_within_split": "No duplicate question inside a split.",
        "test_dev_and_test_splits_do_not_share_queries": "The dev and the held-out split share no question.",
        "test_index_overlap_flag_is_truthful": "Each case's index_overlap flag matches its real similarity to a semantic-index example.",
        "test_held_out_split_never_overlaps_the_index": "No held-out case is a copy of a semantic-index example.",
        "test_requirement_ids_are_unique_and_well_formed": "Requirement ids are unique and complete.",
        "test_requirement_coverage_report": "Prints which requirements have routing cases.",
    }),
    ("tests/eval/test_run_eval.py", "Guards the scoring harness", {
        "test_wilson_interval_known_values": "The confidence interval matches known values.",
        "test_metrics_guardrail_precision_recall_and_false_refusal": "Precision, recall and false-refusal rate are computed correctly on a tiny hand-made set.",
        "test_metrics_clarification": "Clarification precision and recall are computed correctly.",
        "test_full_offline_report_builds_and_renders": "A full offline report builds and renders.",
        "test_conversation_report_builds_and_scores": "The conversation report builds, counts turns correctly and renders.",
    }),
]


def esc(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def expected(c):
    if c["expected_stop"]:
        return f"Refused by **{c['expected_stop']}** check"
    if c["needs_clarification"]:
        return f"Asks for **{c['expected_missing_arg']}**"
    args = "; ".join(f"{k}={v}" for a in c["expected_args"] for k, v in a.items())
    return f"`{' + '.join(c['expected_tools'])}`" + (f"<br>{esc(args)}" if args else "")


def collect_counts():
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    counts = collections.Counter()
    for line in out.splitlines():
        if "::" in line:
            counts[line.split("[")[0]] += 1
    return counts


def main():
    cases = casebook.load_cases()
    reqs = casebook.load_requirements()
    known = json.loads((HERE / "known_issues.json").read_text())
    by_cat = collections.defaultdict(list)
    for c in cases:
        by_cat[c["category"]].append(c)
    mapped = {cat for _, _, cats in THEMES for cat in cats}
    missing = set(by_cat) - mapped
    assert not missing, f"categories without a description: {sorted(missing)}"
    counts = collect_counts()
    live = sorted((HERE.parent / "results").glob("*-live.json"))
    live_line = ""
    if live:
        r = json.loads(live[-1].read_text())
        e = r["metrics"]["end_to_end"]
        live_line = (f"The latest live run ({live[-1].name}) passed {e['k']} of {e['n']} scored cases with the hosted Qwen3-8B planner "
                     f"and embeddings.")

    n_src = collections.Counter(c["source"] for c in cases)
    n_unit = sum(n for k, n in counts.items() if "/test_eval_dataset.py" not in k and "test_case_schema" not in k and "index_overlap_flag" not in k)
    L = ["# RiskSphere test catalogue", "",
         f"_Generated by `tests/eval/build_catalog.py` on {date.today().isoformat()}. Do not edit by hand; edit the cases or the descriptions in the script and regenerate._", "",
         "## 1. What is tested and how", "",
         "RiskSphere answers questions about a small sample derivative book. A question passes through six guardrail checks, a planner that chooses a calculation, "
         "Python calculations reached through MCP, and an optional wording step with a critic. The tests check each of those parts, separately and together.", "",
         "| Layer | What it checks | Where |", "|---|---|---|",
         "| Arithmetic | Exposure, status, haircuts, margin amounts, rounding, and the PFE simulation. Compared with hand values and an independent oracle. | `tests/unit/test_math*.py`, `test_pfe_properties.py` |",
         "| Agent logic | Guardrail order, plan validation, clarification, the critic, grounding of model arguments. | `tests/unit/test_agent_logic.py`, `test_guardrail_regressions.py`, `test_pipeline.py` |",
         f"| Evaluation cases | {len(cases)} single questions with an expected outcome (refusal, clarification, or tools and arguments). | `tests/eval/cases/*.jsonl` |",
         "| Conversations | Multi-turn scenarios that check memory, pronouns, clarification and refusals across turns. | `tests/eval/conversations/*.jsonl`, `test_conversations.py` |",
         "| Harness and data guards | The scoring code, and checks that the case data is well-formed and leak-free. | `tests/eval/test_casebook.py`, `test_run_eval.py` |", "",
         "### How to run", "",
         "```bash", "cd RiskSphere", ".venv/bin/pytest -q                                   # everything, offline, no network",
         "python3 tests/eval/run_eval.py                        # scored reports (routing + conversations), offline keyword planner",
         "python3 tests/eval/run_eval.py --suite conversations  # multi-turn scenarios only",
         "python3 tests/eval/run_eval.py --planner live         # scored report with the hosted model (needs secrets.local.env)",
         "python3 tests/eval/run_eval.py --splits test          # frozen held-out split only",
         "python3 tests/eval/generate.py                        # rebuild the case files", "```", "",
         "Offline runs never call the network: the model and embedding gateways are switched off, session state and traces go to a temporary folder, and market prices come from recorded values.", "",
         "### Numbers", "",
         f"- Evaluation cases: **{len(cases)}** ({', '.join(f'{k} {v}' for k, v in sorted(n_src.items()))}).",
         f"- Unit and data-guard tests: **{n_unit}** collected test items (parametrised cases counted individually).",
         f"- Conversation scenarios: **{len(casebook.load_conversations())}** ({sum(len(c['turns']) for c in casebook.load_conversations())} turns).",
         f"- Requirements: **{len(reqs)}**. Known issues: **{len(known)}** routing and **{len(json.loads((HERE / 'known_issues_conversations.json').read_text()))}** conversation (strict expected failures, listed in section 6).",
         f"- {live_line}" if live_line else "", ""]

    # ---- evaluation cases
    L += ["## 2. Evaluation cases by category", "",
          "Each case is one question and an expected outcome. 'Expected' is either a refusal by a named guardrail check, a clarification question for a named missing argument, "
          "or the calculation(s) and key arguments the planner must choose. `Src` is the provenance (seed, spec = written from the README, tpl = template, llm = written by Claude and not yet reviewed by a person). "
          "`Req` is the requirement it traces to. A warning sign marks a known offline issue.", ""]
    src_short = {"author_seed": "seed", "spec_derived": "spec", "template": "tpl", "llm_paraphrase": "llm", "peer": "peer", "public": "pub", "log": "log"}
    for title, intro, cats in THEMES:
        total = sum(len(by_cat[c]) for c in cats if c in by_cat)
        L += [f"### {title} ({total} cases)", "", intro, ""]
        for cat, desc in cats.items():
            rows = sorted(by_cat.get(cat, []), key=lambda c: c["id"])
            if not rows:
                continue
            L += [f"#### `{cat}` ({len(rows)})", "", desc, "",
                  "| Id | Question | Remembered name | Expected | Src | Req |", "|---|---|---|---|---|---|"]
            for c in rows:
                flag = " \u26a0" if c["id"] in known else ""
                L.append(f"| {c['id']}{flag} | {esc(c['query'])} | {c['last_counterparty'] or '-'} | {expected(c)} | {src_short[c['source']]} | {', '.join(c['requirement_ids'])} |")
            L.append("")


    # ---- conversations
    convs = casebook.load_conversations()
    known_c = json.loads((HERE / "known_issues_conversations.json").read_text())
    by_ccat = collections.defaultdict(list)
    for c in convs:
        by_ccat[c["category"]].append(c)
    missing_c = set(by_ccat) - set(CONV_CATS)
    assert not missing_c, f"conversation categories without a description: {sorted(missing_c)}"
    L += [f"## 3. Conversation scenarios ({len(convs)} conversations, {sum(len(c['turns']) for c in convs)} turns)", "",
          "The routing cases above are single questions. These scenarios play several turns in order through the real question handler, with the calculation server mocked and the session file in a temporary folder. "
          "After every turn the test checks the outcome (answer with these tools, a clarification for this argument, or a refusal by this check) **and** the state: the remembered counterparty and the open clarification. "
          "A conversation passes only if every turn passes. A warning sign marks a known offline issue.", "",
          "Expected behaviour where the README was silent was agreed in advance: (1) a new full question drops an open clarification, (2) a refusal keeps both memory and an open clarification, (3) a new run starts with empty memory.", ""]
    for cat, desc in CONV_CATS.items():
        rows = sorted(by_ccat.get(cat, []), key=lambda c: c["id"])
        if not rows:
            continue
        L += [f"### `{cat}` ({len(rows)})", "", desc, ""]
        for c in rows:
            flag = " \u26a0" if c["id"] in known_c else ""
            L += [f"**{c['id']}**{flag} ({', '.join(c['requirement_ids'])})" + (f" _{esc(c['notes'])}_" if c.get("notes") else ""), "",
                  "| # | User says | Expected | Memory after | Open question after |", "|---|---|---|---|---|"]
            for i, t in enumerate(c["turns"], 1):
                e = t["expect"]
                if e["outcome"] == "refuse":
                    exp = f"Refused by **{e['stop']}**"
                elif e["outcome"] == "clarify":
                    exp = f"Asks for **{e['missing_arg']}**"
                else:
                    a = "; ".join(f"{k}={v}" for x in e.get("args", []) for k, v in x.items())
                    exp = f"`{' + '.join(e['tools'])}`" + (f"<br>{esc(a)}" if a else "")
                extra = (" (kill switch on)" if t.get("env") else "") + (" (after a restart)" if t.get("new_session") else "")
                sa = t["state_after"]
                L.append(f"| {i} | {esc(t['user'])}{extra} | {exp} | {sa['last_counterparty'] or '-'} | {sa['pending'] or '-'} |")
            L.append("")

    # ---- unit tests
    L += ["## 4. Unit and data-guard tests", ""]
    for path, title, tests in UNIT:
        L += [f"### {title}", "", f"`{path}`", "", "| Test | Items | What it checks |", "|---|---|---|"]
        for name, desc in tests.items():
            n = counts.get(f"{path}::{name}", 0)
            assert n, f"{path}::{name} is not collected; fix the catalogue"
            L.append(f"| `{name}` | {n} | {esc(desc)} |")
        collected = {k.split("::")[1] for k in counts if k.startswith(path + "::")}
        undocumented = collected - set(tests)
        assert not undocumented, f"undocumented tests in {path}: {sorted(undocumented)}"
        L.append("")

    # ---- requirements
    covered = collections.Counter(rid for c in cases for rid in c["requirement_ids"])
    for c in convs:
        for rid in c["requirement_ids"]:
            covered[rid] += 1
    L += ["## 5. Requirement traceability", "",
          "Each requirement is a rule taken from the README or the configuration. 'Eval cases' counts the routing cases and conversations that exercise it. "
          "Requirements with no routing case are covered by unit tests (arithmetic, PFE, critic) or are not yet covered (marked **gap**).", "",
          "| Id | Area | Requirement | Eval cases |", "|---|---|---|---|"]
    unit_cover = {"R16": "unit", "R17": "unit", "R18": "unit", "R19": "unit", "R20": "unit", "R21": "unit", "R22": "unit", "R23": "unit", "R01": "unit"}
    for rid, r in reqs.items():
        n = covered.get(rid, 0)
        status = str(n) if n else (f"0 ({unit_cover[rid]} tests)" if rid in unit_cover else "0 (**gap**)")
        L.append(f"| {rid} | {r['area']} | {esc(r['text'])} | {status} |")
    L.append("")
    gaps = [rid for rid in reqs if not covered.get(rid) and rid not in unit_cover]
    L += [f"Gaps (no test yet): {', '.join(gaps) if gaps else 'none'}. These are the failure-path tests planned next: gateway retry, keyword fallback, CJK rule, input length, MCP failure, the key never logged.", ""]

    # ---- known issues
    L += ["## 6. Known issues (strict expected failures, offline planner)", ""]
    if known:
        L += ["These cases fail today with the keyword planner. They are recorded as strict expected failures: the suite stays green while they fail and goes red the moment one is fixed, so the list can only shrink.", "",
              "| Id | Question | Problem |", "|---|---|---|"]
        q = {c["id"]: c["query"] for c in cases}
        for k, v in known.items():
            L.append(f"| {k} | {esc(q[k])} | {esc(v['kind'])}: {esc(v['detail'])} |")
    else:
        L.append("None.")
    if known_c:
        L += ["", "### Conversation issues", "",
              "| Id | Category | First failing turn | Problem |", "|---|---|---|---|"]
        cq = {c["id"]: c for c in convs}
        for k, v in known_c.items():
            turn = cq[k]["turns"][v["first_failed_turn"] - 1]["user"]
            L.append(f"| {k} | {cq[k]['category']} | {v['first_failed_turn']}: {esc(turn)} | {esc(v['kind'])}: {esc(v['detail'])} |")
    L += ["", "## 7. Data provenance and limits", "",
          "- **Seed** cases were written by the author of the agent, so they match its phrasing. **Spec** cases were written from the README rules. **Template** cases are generated from templates and the sample book. "
          "**llm** cases were written by claude-sonnet-5-5 and their labels are **not yet reviewed by a person**.",
          "- All cases are in the `dev` split, which may be tuned against. A frozen held-out `test` split (blind questions from teammates) does not exist yet. Headline numbers must come only from that split.",
          "- The semantic matcher is built from `data/intents.json`. Questions that are copies of its examples are flagged `index_overlap` and excluded from scores by default.",
          "- Scores on the dev split are not an honest estimate of performance: the guardrails and the planner were changed after seeing these failures.",
          "- The guardrails are regex lists. A new phrasing can still get through; the adversarial cases sample this, they do not prove it cannot happen.", ""]
    OUT.write_text("\n".join(x for x in L if x is not None) + "\n")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
