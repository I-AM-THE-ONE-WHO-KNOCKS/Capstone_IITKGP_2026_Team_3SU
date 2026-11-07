# PFE-Copilot: from CSA documents to a risk dashboard, daily pack and crisis stress tests

**Executive PG Programme in Generative AI and Agentic AI - IIT Kharagpur / upGrad - Capstone Track B (API-side, hosted <=8B LLM)**

> **Design principle:** the LLM *plans and extracts*; code *computes and verifies*; the product is a **reviewable file a desk can use**, not a chat answer. Nothing is sent, booked or changed without a named human approver.

---

## 1a. Risk dashboard: future risk and crisis risk (`streamlit run app.py`)

| Tab | What it shows | Method |
|---|---|---|
| **Overview** | KPI tiles, RAG table (PFE vs limit, margin action, top MtM driver), daily-pack download | Monte-Carlo PFE + deterministic CSA engine |
| **Future risk (PFE)** | Fan chart (50/75/90/95/99%), expected exposure, collateralised vs uncollateralised, limit line; sliders for **margin period of risk** and **volatility multiplier** | Correlated GBM from Yahoo Finance history; VM-only CSA collateral with MPOR lag |
| **Crisis scenarios** | Historical replays (**GFC 2008, COVID 2020, rates 2022, taper 2013**), hypothetical shocks (equity -20/-40%, USD spike, rates up) and a **custom shock builder**. Shows gap exposure after collateral, stressed margin calls (**liquidity need**), BREACH today vs after stress, Excel export | Realised Yahoo returns over the crisis window applied to trades **and posted collateral together** (wrong-way / gap risk); stressed PFE re-simulated from the shocked market with higher vol/correlation |
| **Reverse stress** | How far must markets move (down/up) before exposure after collateral breaches the limit | Uniform market-move scan with limit-crossing marker |
| **Copilot** | Chat that can produce all the files below and run stress tests ("What happens in a COVID-style crash?") | Planner-executor-critic agent |

Stress assumptions to state in the report: instantaneous shock before any new margin call (gap exposure), stressed PFE vol multiplier 1.5 and correlation blend 0.2 (configurable via `STRESS_VOL_MULT`, `STRESS_CORR_BLEND`), a "uniform move" reverse stress, linear products only. If a ticker has no history for a crisis window, the shock is set to 0 and a data note is shown - do not quote such a scenario.

## 1. What it produces (usable outputs)

| # | Output | Format | Verification before a human sees it | Consumer |
|---|---|---|---|---|
| 1 | **CSA term sheet** extracted from real SEC EDGAR CSAs (threshold, MTA, rounding, independent amount, base currency, valuation agent, notification time, eligible collateral) | `.xlsx` (colour-coded status + Evidence sheet) and CDM-inspired `.json` | **Verification gate:** quote must exist verbatim in the source, contain the number, and state exactly one amount - else `REVIEW` / `NOT_FOUND`. `apply_terms()` needs a named approver, writes a backup + audit log | Collateral onboarding / legal ops |
| 2 | **Daily exposure & margin pack** | `.xlsx`: Summary (RAG), Margin_Actions, Exceptions (analyst notes), PFE chart, CSA terms used, Lineage | Exception notes pass a figure-grounding check; red banner if synthetic fallback data was used | CCR analysts / limit monitoring |
| 3 | **Margin call notice** | `.docx` pre-filled (calc steps, trades, approval block) + `margin_calls_upload.csv` | Amounts from deterministic CSA engine; status `DRAFT_PENDING_APPROVAL` | Collateral operations |
| 4 | **Stress test workbook** (`stress_<scenario>.xlsx`): shocks applied, per-counterparty stressed MtM / collateral / exposure / PFE / status, liquidity sheet | `.xlsx` | Shocks from realised returns or declared presets; status colour-coded; data-gap notes | Risk managers / treasury |
| 5 | **Dispute reconciliation** | waterfall (MtM difference -> collateral value -> rounding/MTA/terms), `AGREE / PARTIAL_AGREE / DISPUTE`, agreed vs disputed amount -> `dispute_responses.csv` | Deterministic attribution; undisputed amount computed per CSA mechanics | Dispute desk |
| 6 | Q&A with citations (PFE limit, margin, haircut, CSA/BCBS clause lookup, Yahoo Finance vol) | chat + `[S#]` sources | Critic: numbers/status/citations must trace to tool output | Anyone |

### Why these outputs (pattern seen in industry deployments)
- **JPMorgan COIN** extracted structured attributes from commercial loan agreements rather than chatting about them; **ISDA/DRS Ark 51** converts CSAs into CDM data for collateral systems such as **Vermeg COLLINE**, where data goes through four-/six-eyes validation.
- **Morgan Stanley Debrief** drafts notes, tasks and emails that are saved into Salesforce for the advisor to review - and Morgan Stanley evaluates each use case on test sets before release.
- Collateral vendors (e.g. OSTTRA triResolve) emphasise exception-based workflows and dispute management as the biggest inefficiency - hence outputs 3 and 4.
- Industry commentary attributes a large share of collateral disputes to incorrect or inconsistent CSA/agreement data - hence output 1 plus an audit trail.

## 2. Architecture

![PFE-Copilot architecture](docs/architecture.png)

*(Vector: `docs/architecture.svg`.)* Flow: guardrails -> planner (LLM, validated JSON) -> executor over **9 deterministic tools** (5 analytic + 4 that write files) -> composer <-> critic -> answer + files + human-approval banner, fully traced.

## 3. Data (real wherever possible)

| Data | Source | Notes |
|---|---|---|
| ISDA CSAs / schedules | **SEC EDGAR** full-text search, exhibit filings | `N_CSA_DOCS` configurable; manual PDFs can be dropped into `data/csa/` |
| Margin & haircut regulation | **BCBS-IOSCO d317** | haircut table also encoded in `regulatory_haircut` (spot-check vs the PDF) |
| Prices, FX, vol, correlation | **Yahoo Finance** via `yfinance` | cached; runs are flagged `FALLBACK_SYNTHETIC` if Yahoo is unreachable |
| Counterparties, trades, limits | **Illustrative stand-in** | real books are confidential; no employer data or code is used. Terms can be replaced by approved extractions from real CSAs (output 1) |

## 4. Track B requirement mapping

| Requirement | Where |
|---|---|
| Spec + frozen eval set before agent evaluation | Sections 2 and 6 - **80+ queries** (limit, margin, haircut, clause, market data, multi-turn, refusal, **deliverables**) |
| >=3 prompting strategies, small-model failure modes | Section 7 |
| RAG: chunking experiments, BM25+dense, reranker, citations, recall@k separate from answer quality | Section 4 incl. **4.3 blind, human-labelled Recall@K / MRR / nDCG** |
| Agent: >=3 tools, planner->executor->critic, memory, error handling | Library + Sections 8, 10 |
| Evaluation: accuracy, completion, tool calls, cost, latency, failure taxonomy | Section 9 |
| **Extraction quality** (new) | Section 5b: human review sheet -> VERIFIED-gate precision, coverage, LLM vs regex baseline |
| Tracing + trace dashboard | `outputs/traces.jsonl` + Streamlit **Observability** tab (latency p50/p95, tool usage, flags, per-trace inspector); forward records to Langfuse/LangSmith if your rubric requires a specific tool |
| Ship: front-end (with file downloads), container, rate limit, cache | Section 11 |
| Responsible AI: injection, PII, refusal, HITL | Section 10 |
| "What did we do to compensate for a small model?" | Section 12 |

## 5. Quickstart

```bash
# 1. Open PFE_Copilot_Capstone.ipynb (Colab/Jupyter). Config cell: GROUP_ID (selects your endpoint), CA_CERT_PATH
#    (your group's .crt - upload it to Colab), API key via Colab secret LLM_API_KEY or the hidden prompt.
#    Model: Qwen/Qwen3-8B. SEC_USER_AGENT is pre-set. Set FINAL_RUN = True for the graded run (live data only, 500 CSAs).
# 2. Run top to bottom. Manual steps (about 1 hour total):
#      4.3  label outputs/retrieval_labeling.csv (0/1/2)         -> re-run the evaluation cell
#      5b   label outputs/extraction_review.csv (human_correct)  -> re-run eval_extraction()
#      5b   optional: APPLY=True with approved_by to load a real CSA's terms into the engine
# 3. Section 13 builds pfe_copilot_submission.zip.
streamlit run app.py          # dashboard (overview, future risk, crisis scenarios, reverse stress) + copilot chat with file downloads
docker build -t pfe-copilot . && docker run -p 8501:8501 -e LLM_BASE_URL=... -e LLM_API_KEY=... -e LLM_MODEL=... pfe-copilot
```
Without an LLM key the notebook runs in **dry-run mode** (rule-based planner, regex extractor, deterministic answers): every tool and file generator works, but report numbers must come from the live endpoint.

## 5b. Track B endpoint constraints and how the client handles them

| Constraint (learner guide) | Handling |
|---|---|
| Model `Qwen/Qwen3-8B`; Qwen3 emits `<think>` blocks | thinking disabled (`enable_thinking=false` + `/no_think`) and any `<think>` text stripped; JSON extraction tolerant of prose |
| Private CA certificate, HTTPS verification must stay on | `requests` with `verify=<your .crt>` (`CA_CERT_PATH`); clear error message if missing |
| 1 active request per key; 60 requests/min | global lock + throttle (<=50/min); 429/503 retried after 2/4/8/16 s |
| 8192 tokens total; `max_tokens` <= 1024 | caps clamped to 1024; prompts/passages truncated; per-query token budget |
| No embeddings endpoint | local `all-MiniLM-L6-v2` embeddings and local cross-encoder reranker |
| Tool-call support | planner emits schema-validated JSON plans (JSON mode with automatic fallback + repair loop) - constrained output and validation are the focus of the track; native function-calling can be added as an experiment |
| Cost control | every response cached on disk (`outputs/llm_cache.jsonl`, excluded from the zip) - re-runs and demos cost no calls |

Setup helpers: the certificate file name (`vm04-ca.crt` etc.) identifies your VM, so the notebook derives your base URL from `CA_CERT_PATH` even if you do not know your group number; **Section 1b runs a pre-flight check** (LLM `/models`, certificate, Yahoo Finance, SEC EDGAR, BIS) and prints what to fix; packaging aborts if an API-key-like string is found in any file, and a `.gitignore` excludes the cache, certificates and `.env`.

Budget for the first live run: ~600 LLM calls (about 1-2 hours at endpoint latency) plus about 1 hour of manual labelling (retrieval sheet, extraction sheet).

## 6. Results (fill from your live run)

| Metric | Value |
|---|---|
| End-to-end accuracy strict / lenient (80+ queries) / task completion | _ / _ / _ |
| Avg tool calls, latency p50/p95, tokens, cost per query | _ |
| Retrieval: regex recall@5 vs **human** strict recall@1/3/5, MRR, nDCG@5 per mode | _ |
| Best prompting strategy (route accuracy, JSON first-try rate) | _ |
| **CSA extraction:** coverage; VERIFIED share; **VERIFIED precision**; REVIEW precision; LLM vs regex agreement | _ |
| Failure taxonomy counts | _ |
| Deliverable + stress queries passing (file created + figures correct) | _ |
| Stress scenarios run on real history: liquidity need, BREACH before -> after (screenshot the dashboard) | _ |

## 7. Responsible AI and controls

- **Human-in-the-loop:** drafts only (`DRAFT_PENDING_APPROVAL`); no sending, booking or approving; term changes require `approved_by`, produce a backup and an audit CSV.
- **Verification gate on extraction:** LLM output reaches the engine only if quote-in-source, number-in-quote and single-amount checks pass **and** a human approves.
- **Grounding critic** on every prose answer; deterministic fallback when it fails.
- **Prompt injection / PII:** retrieved text sanitised and redacted; document-poisoning test in Section 10.
- **Transparency:** Lineage sheet, data-source flags, synthetic-data banner.

## 8. Limitations (state in the report)

`VERIFIED` means *literally supported by the quote*, not *legally correct* - party mapping (Party A vs B) and elections still need a human. JSON output is CDM-**inspired**, not validated against the FINOS CDM schema. Linear forwards only; GBM with historical vol; VM-only CSA with cash VM and uniform MPOR; illustrative portfolio and limits; EDGAR CSAs are other companies' agreements; dispute tolerance (`max(rounding, 1% of call)`) is an assumption; haircut table to be spot-checked; recall labels pooled from the same four systems.

## 9. Repository layout

```
pfe_copilot_submission/
├── PFE_Copilot_Capstone.ipynb    # Main executable notebook
├── pfe_copilot.py                # Core library (generated by the notebook)
├── app.py                        # Streamlit web application
├── Dockerfile                    # Container configuration
├── requirements.txt              # Project dependencies
├── README.md                     # Overview and setup
├── docs/
│   ├── architecture.png
│   ├── architecture.svg
│   └── REPORT_AND_DEMO_OUTLINE.md   # 12-page report map + 10-minute demo run of show
├── data/
│   ├── csa/                      # Saved SEC EDGAR ISDA/CSA documents (.txt / .json)
│   ├── reg/                      # BCBS-IOSCO regulation (d317 .pdf / .txt)
│   ├── market/                   # Cached Yahoo Finance CSVs
│   └── portfolio/                # counterparties.csv & trades.csv (+ .bak.csv after apply)
└── outputs/
    ├── eval_set.json             # 80+ query evaluation set
    ├── eval_results.csv          # Benchmark outputs
    ├── retrieval_labeling.csv    # Blind labelling sheet (human 0/1/2)
    ├── retrieval_eval_human.csv  # Strict Recall@K / MRR / nDCG
    ├── extraction_review.csv     # Human review of CSA extraction
    ├── csa_terms_audit.csv       # Audit trail of applied terms (if any)
    ├── traces.jsonl              # Execution traces
    └── deliverables/             # daily_ccr_pack_*.xlsx, stress_<scenario>_*.xlsx, margin_notice_*.docx, margin_calls_upload.csv,
                                  # dispute_responses.csv, csa_terms_extracted.xlsx / .json
```
