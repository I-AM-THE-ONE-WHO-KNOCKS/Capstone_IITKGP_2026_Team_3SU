# Multi-Agent Financial Risk Analysis & Collateral Management System

The working system is RiskSphere. This note is the wider brief. The working cut, including what is already built, is `counterparty-risk/README.md`.

What we kept from this note: Python is the only place a money figure is produced. The orchestrator plans and explains. The new work is data quality, market-risk scenarios, an initial-margin proxy, collateral shortfall, and expected loss from synthetic PD and LGD. The simulated PFE stays labelled as a prototype, not SA-CCR. Initial margin is not called SIMM. A person still signs off. Nothing is sent or booked.

What we did not take from the suggested stack: LangGraph, Pandas, FastAPI, Streamlit, and Docker. The desk stays the standard library and one Qwen call at a time. The six specialists are Python services. Only the orchestrator, and the wording pass after a result exists, call the chat model.

The embedding API is the VM02 gateway, model `bge-small-en-v1.5`, `POST https://103.42.50.41:1035/v1/embeddings`, certificate `vm02-ca.crt`. It replaces the local Ollama embedder. The client is `counterparty-risk/search.py`.

Stages in the working folder:

1. Today's book. Built. Exposure, variation margin, Yahoo, haircut table, critic.
2. Simulated PFE. Built. Engine checks, then the paths.
3. Data quality, scenario P&L, initial-margin proxy, collateral shortfall, expected loss. Not built.
4. Filings and the quote check, using the hosted embedder. The client is in place. The filings are not.
5. Alerts, the consolidated report, and draft files. Not built.

## 1. Project overview

Build a multi-agent system that analyses derivative portfolios and
produces explainable risk assessments covering:

-   Market risk
-   Potential Future Exposure (PFE)
-   Initial Margin (IM) and Variation Margin (VM)
-   Collateral sufficiency and haircut impact
-   Counterparty credit risk
-   Risk-limit breaches and consolidated reporting

**Core design decision:** all financial calculations must be implemented
in deterministic Python functions. LLMs must not calculate, estimate,
round, or alter financial values. Agents use Python calculation services
and then explain the returned results.

This is an educational prototype, not a production risk engine or a
regulatory capital calculator. Regulatory implementation requires
complete rule interpretation, approved data, legal agreements, model
governance and independent validation.

## 2. Objectives and scope

### In scope

1.  Ingest trade, market, counterparty, collateral and agreement data.
2.  Validate data completeness, formats, units and consistency.
3.  Calculate market sensitivities and scenario P&L for a defined
    instrument set.
4.  Calculate a clearly labelled **simulation-based PFE** for the
    prototype.
5.  Calculate illustrative VM, IM proxy and collateral coverage.
6.  Calculate credit-risk metrics where suitable inputs are supplied.
7.  Detect limit breaches, missing collateral and unusual exposures.
8.  Produce traceable reports with inputs, formulas, assumptions and
    calculation versions.

### Out of scope for the first version

-   Production trading or collateral calls.
-   Legal determination of enforceable netting.
-   Full regulatory SA-CCR implementation unless every required trade
    attribute and rule is implemented and independently tested.
-   Official ISDA SIMM implementation or certification.
-   Live broker execution, payment or settlement.
-   Treating public market prices as a substitute for
    institution-specific trade, CSA, counterparty or collateral records.

## 3. Proposed agents

Use six specialist agents plus a workflow orchestrator. Agents are
workflow components; calculations remain in a shared Python calculation
layer.

  -----------------------------------------------------------------------
  Component               Main responsibility     Python services /
                                                  outputs
  ----------------------- ----------------------- -----------------------
  1\. Orchestrator & Data Understand request,     Schema checks,
  Quality Agent           select workflow, load   missing-data checks,
                          data and enforce        run manifest
                          validation gates        

  2\. Market Risk Agent   Analyse market factors  Returns, volatility,
                          and portfolio           shocks, scenario P&L
                          sensitivities           

  3\. PFE Agent           Estimate future         Exposure paths,
                          exposure under defined  exposure distribution,
                          scenarios               PFE percentile

  4\. Margin Agent        Estimate current VM and MTM, net exposure, VM
                          an illustrative IM      call, IM proxy
                          requirement             

  5\. Collateral Agent    Assess collateral value Eligible value,
                          and coverage            haircut-adjusted value,
                                                  shortfall / surplus

  6\. Counterparty &      Assess counterparty     EAD proxy / configured
  Reporting Agent         metrics, limits and     EAD, PD×LGD×EAD
                          explain results         expected-loss estimate,
                                                  alerts and report
  -----------------------------------------------------------------------

The Orchestrator should coordinate the workflow and validate that each
agent has received the required inputs. It should not invent missing
data or fill missing financial values using an LLM.

## 4. End-to-end flow

``` text
User request / scheduled portfolio run
                 |
                 v
       Orchestrator Agent
                 |
                 v
       Data ingestion layer
                 |
                 v
       Data quality validation
          |              |
       Pass             Fail
          |              |
          v              v
   Python calculation   Data issue report
       services          (stop run)
          |
          +-------------------------------+
          |               |               |
          v               v               v
     Market Risk        PFE            Margin
       service         service         service
          |               |               |
          +---------------+---------------+
                          |
                          v
                  Collateral service
                          |
                          v
               Counterparty risk service
                          |
                          v
                 Independent checks
                          |
                          v
                Reporting agent / LLM
                          |
                          v
             Dashboard + downloadable report
```

### Detailed workflow

1.  **Ingest:** load trades, market observations, counterparty reference
    data, collateral inventory and agreement parameters.
2.  **Normalize:** standardize dates, currencies, units, identifiers,
    signs and instrument conventions.
3.  **Validate:** reject or quarantine invalid records; record every
    warning and failure.
4.  **Calculate market values:** value supported instruments and
    calculate risk factors using documented Python functions.
5.  **Generate scenarios:** create historical or hypothetical market
    shocks using a reproducible seed and a defined horizon.
6.  **Calculate PFE:** revalue trades under simulated scenarios and
    calculate positive net exposure at each future horizon.
7.  **Calculate margin:** calculate VM from current exposure and
    configured agreement terms; calculate IM only using an explicitly
    labelled methodology or proxy.
8.  **Assess collateral:** apply eligibility rules and haircuts to
    collateral, then compare adjusted collateral with the relevant
    requirement.
9.  **Assess counterparty risk:** combine exposure outputs with supplied
    PD/LGD data and configured limits.
10. **Validate outputs:** run reconciliation, boundary, regression and
    independent benchmark tests.
11. **Explain:** LLM summarizes only validated outputs, highlights
    drivers and cites the run ID and calculation version.
12. **Report:** store the inputs, outputs, assumptions, warnings, source
    timestamps and audit trail.

## 5. Calculation approach --- Python only

Create a separate calculation package. Agents call these functions
through typed interfaces; they do not implement formulas in prompts.

Suggested package:

``` text
src/
  agents/
    orchestrator.py
    market_risk_agent.py
    pfe_agent.py
    margin_agent.py
    collateral_agent.py
    counterparty_reporting_agent.py
  calculations/
    pricing.py
    market_risk.py
    scenarios.py
    pfe.py
    margin.py
    collateral.py
    counterparty.py
  data/
    loaders/
    validators/
    schemas/
  api/
  dashboard/
  tests/
  config/
```

### 5.1 Market risk

For the MVP, support a small, explicitly declared instrument set, such
as FX forwards and simple equity positions. Add swaps and options only
when the required pricing and sensitivity models are implemented and
tested.

Possible calculations:

-   Historical returns: `r_t = P_t / P_(t-1) - 1`
-   Historical volatility: standard deviation of returns, with a
    documented annualization convention.
-   Scenario P&L:
    `P&L_s = V(portfolio, scenario_s) - V(portfolio, base)`
-   Sensitivities: finite-difference repricing or analytical Greeks for
    supported instruments.

Do not apply equity-return formulas indiscriminately to rates, FX,
commodities or option prices. Use instrument-appropriate market
conventions.

### 5.2 Simulation-based PFE for the prototype

A transparent Monte Carlo / historical simulation approach is suitable
for a demonstrator:

1.  Select risk factors relevant to the portfolio.
2.  Estimate their distributions and dependence using a documented
    historical window or specified hypothetical scenarios.
3.  Generate reproducible future market paths.
4.  Revalue each trade at each future horizon.
5.  Aggregate trades at the legally and operationally defined
    netting-set level only when netting eligibility is explicitly
    represented in the input data.
6.  Apply collateral and margin terms consistently with the selected
    exposure definition.
7.  Calculate positive exposure for each scenario and horizon.
8.  Calculate a configured percentile (for example, 95th or 99th) across
    the simulated exposure distribution.

Illustrative definition:

`PFE(t, q) = quantile_q(max(net_exposure(t, scenario), 0))`

Document whether the result is gross or collateral-adjusted, the
confidence percentile, horizon, simulation method, number of paths,
netting assumptions and collateral treatment.

**Important:** this simulation-based PFE is not automatically Basel
SA-CCR PFE. SA-CCR is a prescribed regulatory method with trade-level
supervisory factors, asset-class add-ons, netting-set rules and a
multiplier. Do not label a simplified Monte Carlo result "SA-CCR
compliant".

### 5.3 Margin

Keep these concepts distinct:

-   **Variation Margin (VM):** generally reflects current mark-to-market
    exposure under the applicable agreement, including threshold and
    minimum transfer amount rules.
-   **Initial Margin (IM):** covers potential future exposure over a
    close-out period under the applicable methodology.
-   **PFE:** an exposure measure; it is not itself a universal margin
    formula.

For an MVP, implement an illustrative VM call calculation from net
mark-to-market, threshold, existing VM and MTA parameters. Define the
sign convention carefully (who owes whom).

For IM, either: - implement a clearly labelled educational
historical/Monte Carlo risk measure with documented horizon and
confidence level; or - implement a documented standardised schedule for
a narrow supported product set.

Do not claim to reproduce ISDA SIMM unless the full applicable licensed
methodology, inputs, risk weights, aggregation, governance and
validation requirements are met.

### 5.4 Collateral

For each collateral item, capture asset type, currency, market value,
valuation timestamp, eligibility, haircut and concentration limits.

Illustrative calculation:

`adjusted_collateral = market_value × (1 - haircut)`

For currency mismatch, wrong-way risk, concentration limits, settlement
timing and dispute status, apply explicit configurable rules. Do not
assume every asset is eligible collateral.

`collateral_shortfall = max(required_collateral - eligible_adjusted_collateral, 0)`

`collateral_surplus = max(eligible_adjusted_collateral - required_collateral, 0)`

### 5.5 Counterparty risk

Use supplied or explicitly synthetic inputs for:

-   PD --- probability of default
-   LGD --- loss given default
-   EAD --- exposure at default

Illustrative expected loss:

`expected_loss = PD × LGD × EAD`

Keep the definition and time horizon of PD consistent with the intended
use. Do not derive a real counterparty PD from a company name or LLM
judgement.

For regulatory EAD, implement the applicable regulatory framework
separately. Under Basel SA-CCR, the framework uses
`EAD = alpha × (RC + PFE)` with alpha of 1.4, subject to the detailed
CRE52 requirements and scope. A prototype proxy must not be presented as
regulatory EAD.

## 6. Data required and where to obtain it

A realistic risk calculation needs more than public market data.
Separate data into four groups.

  -----------------------------------------------------------------------
  Data group              Required fields         Recommended source for
                                                  prototype
  ----------------------- ----------------------- -----------------------
  Market data             FX rates, interest-rate RBI DBIE / RBI
                          curves, equity /        Financial Markets
                          commodity prices,       Watch, ECB Data Portal,
                          volatility              FRED

  Trade / portfolio data  Trade ID, product,      Synthetic CSV/JSON
                          notional, currency,     generated for the
                          direction, maturity,    project
                          trade date, market      
                          value, risk factors     

  Counterparty data       Counterparty ID, rating Synthetic reference
                          or internal grade, PD,  data; do not infer real
                          LGD, limits, legal      credit attributes
                          entity                  

  Collateral & agreement  Asset type, currency,   Synthetic CSA-like
  data                    market value, haircut,  agreement and
                          eligibility, threshold, collateral records
                          MTA, margin frequency,  
                          netting-set ID          
  -----------------------------------------------------------------------

### 6.1 Public market data sources

1.  **RBI Database on Indian Economy (DBIE)**\
    https://data.rbi.org.in\
    Use for Indian macroeconomic and financial time series. Some
    datasets are downloadable from the portal; confirm the series
    definition, frequency and publication lag.

2.  **RBI Financial Markets Watch**\
    https://www.rbi.org.in/Scripts/financialmarketswatch.aspx\
    Useful for Indian government securities and selected
    financial-market information. Check the publication date and
    frequency before using a series in a simulation.

3.  **ECB Data Portal API**\
    https://data.ecb.europa.eu/help/api/data\
    Provides documented API access to ECB statistical data, including
    relevant exchange-rate and interest-rate series. Use the series
    metadata and API documentation to construct requests.

4.  **Federal Reserve Bank of St. Louis FRED API**\
    https://fred.stlouisfed.org/docs/api/fred/overview.html\
    Useful for US and international macro-financial time series. An API
    key may be required. Check each series' source, units, frequency and
    release timing.

5.  **BIS statistics and Basel framework**\
    https://www.bis.org/statistics/\
    https://www.bis.org/basel_framework/\
    Use BIS for reference statistics and regulatory methodology, not as
    a substitute for trade-level market feeds.

### 6.2 Regulatory and methodology references

-   **Basel Framework --- SA-CCR, CRE52:**\
    https://www.bis.org/committees/bcbs/basel-framework/standard/cre/52/inforce/2023-01-01/published/2020-06-05\
    Primary reference for the Basel standardised counterparty credit
    risk approach. Read the full chapter, including replacement cost,
    PFE add-ons, asset classes and netting-set treatment.

-   **Basel Framework --- collateral recognition, CRE22:**\
    https://www.bis.org/committees/bcbs/basel-framework/standard/cre/22/inforce/2028-11-01/published/2025-10-28\
    Reference for collateral recognition and credit-risk mitigation
    requirements. Confirm the version applicable to the intended
    jurisdiction and reporting date.

-   **BCBS-IOSCO margin requirements:**\
    https://www.iosco.org/library/pubdocs/pdf/ioscopd423.pdf\
    Reference for margin principles for non-centrally cleared
    derivatives, including the distinction between current exposure and
    potential future exposure.

-   **ISDA SIMM methodology information:**\
    https://www.isda.org/isda-solutions-infohub/isda-simm/\
    Use as a methodology reference. Check licensing and the effective
    version before any implementation. Do not copy or claim compliance
    with SIMM based on a partial summary.

-   **ESMA EMIR reporting and data quality:**\
    https://www.esma.europa.eu/data-reporting/emir-reporting\
    Useful for understanding derivatives reporting, reconciliation and
    data-quality concepts. Public reporting data is not a complete
    substitute for a firm's private trade repository records.

### 6.3 Synthetic data strategy

For a four-week academic prototype, generate synthetic data for the
information that public sources will not provide:

-   100--500 derivative trades across a small supported product set.
-   10--20 counterparties with synthetic PD, LGD and exposure limits.
-   20--50 collateral records across cash and a few securities.
-   Agreement records with threshold, MTA, margin frequency, eligible
    collateral and haircut parameters.
-   Market time series from one or two official sources.

Create separate files such as:

``` text
data/
  trades.csv
  counterparties.csv
  collateral.csv
  agreements.csv
  market_fx.csv
  market_rates.csv
  expected_results.csv
```

Include both normal and edge-case records: missing market observations,
expired trades, negative MTM, over-collateralisation,
under-collateralisation, currency mismatch, ineligible collateral, limit
breaches and duplicate trade IDs.

## 7. Data ingestion and validation

Implement validation before any calculation.

### Structural validation

-   Required columns and data types exist.
-   IDs are unique where required.
-   Dates parse correctly and maturity is not before trade date.
-   Currency codes and product types belong to controlled enumerations.
-   Notional, prices, PD, LGD and haircuts are within permitted ranges.
-   Missing values are handled by explicit reject, quarantine or
    approved fallback rules.

### Financial consistency checks

-   Trade direction and sign conventions are consistent.
-   Market data timestamps are aligned to the valuation date.
-   Market data units and quote conventions are known (for example,
    USD/INR versus INR/USD).
-   No accidental mixing of percentages and decimals.
-   Collateral value and haircut use the same currency and valuation
    timestamp, or a documented FX conversion.
-   Trades are assigned to a netting set only when the dataset
    explicitly provides that relationship.
-   Agreement thresholds, MTA and eligible collateral rules are applied
    consistently.

### Market-data quality checks

-   Detect stale observations, gaps, duplicates and outliers.
-   Check holidays, weekends and source publication delays.
-   Store source, series ID, retrieval timestamp, observation date, unit
    and transformation history.
-   Do not silently forward-fill missing values across long gaps.
-   Retain raw source data so calculations can be reproduced.

## 8. Validation and testing strategy

Validation should cover the data, Python calculations, agent workflow
and final explanation.

### 8.1 Unit tests for every calculation

Examples:

  -----------------------------------------------------------------------
  Test                                Expected behaviour
  ----------------------------------- -----------------------------------
  Zero market movement                Scenario P&L is zero for a
                                      correctly valued unchanged position

  No positive exposure                PFE is zero under the defined
                                      positive-exposure convention

  Larger adverse market shock         Exposure should increase for a
                                      position whose risk direction
                                      implies it

  More eligible collateral            Collateral shortfall should not
                                      increase, all else equal

  Higher haircut                      Adjusted collateral should not
                                      increase

  PD = 0 or LGD = 0                   Expected loss is zero

  Duplicate trade ID                  Data validation rejects or
                                      quarantines the duplicate

  Missing required market input       Calculation stops or follows a
                                      documented fallback; no invented
                                      value

  Exposure above configured limit     Risk alert is generated
  -----------------------------------------------------------------------

### 8.2 Benchmark and independent calculation checks

-   Create a small set of hand-calculated portfolios and expected
    outputs.
-   Independently implement selected formulas in a separate test module
    or spreadsheet.
-   Compare Python outputs against those benchmarks within defined
    numerical tolerances.
-   For SA-CCR, compare test cases against worked examples or
    independently verified reference calculations based on the exact
    applicable CRE52 version.
-   For IM, compare only against the same methodology, same inputs and
    same version. Do not compare a Monte Carlo proxy directly with SIMM
    and expect equality.

### 8.3 Simulation validation

-   Fix and record random seeds for repeatability.
-   Increase simulation paths and check convergence of the selected PFE
    percentile.
-   Run sensitivity tests for volatility, correlation, horizon and
    confidence level.
-   Backtest realised future exposure against predicted exposure where
    sufficient historical data exists.
-   Test stressed periods and hypothetical stress scenarios.
-   Document limitations from short or incomplete historical series.

### 8.4 Agent and LLM validation

-   Agents must call approved Python functions rather than calculate in
    natural language.
-   Validate all tool inputs and outputs against JSON schemas.
-   The reporting agent must quote numeric results exactly from
    calculation outputs.
-   Reject unsupported claims, missing provenance or values not present
    in the calculation result.
-   Test prompt injection in uploaded files and user instructions.
-   Test that agents do not invent missing PD, LGD, collateral
    eligibility or legal netting status.
-   Log agent inputs, tool calls, outputs, model version and run ID.

### 8.5 Auditability

For each run, store:

-   Run ID and valuation timestamp.
-   Input file hashes and source references.
-   Data validation results and warnings.
-   Calculation code version and configuration version.
-   Scenario definitions, random seed, number of paths and confidence
    level.
-   Intermediate and final numerical outputs.
-   Agent summaries and any human overrides.

## 9. Suggested API contracts

Use strict typed inputs and outputs between agents and Python services.

Example PFE request:

``` json
{
  "portfolio_id": "PORT-001",
  "valuation_date": "2026-11-14",
  "netting_set_id": "NET-001",
  "horizon_days": 10,
  "confidence_level": 0.99,
  "simulation_paths": 10000,
  "seed": 42
}
```

Example PFE response:

``` json
{
  "portfolio_id": "PORT-001",
  "netting_set_id": "NET-001",
  "metric": "simulation_pfe",
  "pfe": 1250000.50,
  "currency": "INR",
  "confidence_level": 0.99,
  "horizon_days": 10,
  "simulation_paths": 10000,
  "methodology": "historical_or_monte_carlo_simulation",
  "calculation_version": "0.1.0",
  "warnings": []
}
```

The example values are illustrative only. The response must also include
the actual methodology, exposure definition, collateral treatment and
source-data timestamps in the implemented system.

## 10. Technology stack

  -----------------------------------------------------------------------
  Layer                               Suggested technology
  ----------------------------------- -----------------------------------
  Agent orchestration                 LangGraph

  LLM access                          OpenAI-compatible hosted instruct
                                      model within project constraints

  Calculation engine                  Python

  Data processing                     Pandas, NumPy, SciPy

  Validation                          Pydantic, Pandera or Great
                                      Expectations

  Market data ingestion               Requests / official source APIs and
                                      downloadable CSV

  Storage                             SQLite for MVP; PostgreSQL if
                                      needed

  API                                 FastAPI

  UI                                  Streamlit

  Testing                             Pytest

  Packaging                           Docker
  -----------------------------------------------------------------------

Use the LLM only for routing, tool selection, data-quality explanations,
exception summaries and report narration. The calculation engine should
remain independently runnable without an LLM.

## 11. Four-week implementation plan

  -----------------------------------------------------------------------
  Week                                Deliverables
  ----------------------------------- -----------------------------------
  Week 1 --- Data and foundation      Finalize narrow product scope;
                                      define schemas; source market time
                                      series; generate synthetic trades,
                                      counterparties, agreements and
                                      collateral; implement ingestion and
                                      validation; prepare 60--100 gold
                                      test cases before agent
                                      implementation.

  Week 2 --- Calculation engine       Implement market
                                      valuation/scenarios, simulation
                                      PFE, illustrative VM/IM approach,
                                      collateral haircuts and shortfall,
                                      and counterparty metrics. Add unit
                                      tests and hand-calculated
                                      benchmarks.

  Week 3 --- Multi-agent workflow     Implement orchestrator and
                                      specialist agents; connect agents
                                      to Python functions; add structured
                                      outputs, retries, error handling,
                                      audit logs and LLM explanations.

  Week 4 --- Evaluation and demo      Build Streamlit dashboard; run
                                      gold-set evaluation, edge cases,
                                      simulation convergence and agent
                                      safety tests; document assumptions
                                      and limitations; package with
                                      Docker and prepare demo.
  -----------------------------------------------------------------------

### Recommended MVP cut

To keep the project achievable in four weeks:

1.  Support FX forwards and simple equity positions only.
2.  Use official public time series for selected market factors.
3.  Generate synthetic trade, counterparty, collateral and agreement
    data.
4.  Implement simulation-based PFE and label it clearly as a prototype
    model.
5.  Implement transparent VM and collateral coverage calculations.
6.  Use a simple, documented IM proxy rather than claiming regulatory
    SIMM.
7.  Add counterparty expected-loss estimates using synthetic PD/LGD
    inputs.
8.  Demonstrate traceable reports and deterministic tests.

## 12. Dashboard outputs

Recommended dashboard sections:

-   Portfolio overview: trade count, notional, MTM and valuation date.
-   Market risk: risk-factor movements, volatility and scenario P&L.
-   PFE: PFE by counterparty/netting set, horizon and percentile.
-   Margin: VM amount and illustrative IM estimate, with methodology
    labels.
-   Collateral: eligible value, haircut-adjusted value, shortfall and
    surplus.
-   Counterparty: PD, LGD, EAD definition, expected loss and limit
    utilization.
-   Exceptions: missing data, stale prices, breaches and failed
    validations.
-   Audit: run ID, data sources, timestamps and calculation versions.

## 13. Key project rules

1.  Python is the single source of truth for all financial numbers.
2.  LLMs never calculate or modify financial values.
3.  Every metric must have a formula, unit, currency, horizon and
    methodology label.
4.  Never mix a prototype estimate with a regulatory metric without
    explicit qualification.
5.  Never assume legal netting, collateral eligibility or agreement
    terms.
6.  Public market data does not provide private trade, CSA or
    credit-risk information.
7.  Missing or invalid data must produce a visible warning or a failed
    run, not a guessed value.
8.  Every result must be reproducible from stored inputs, configuration
    and code version.
9.  Human review is required before any real-world margin call,
    collateral movement or credit decision.

## 14. Reference links

-   Basel Framework --- SA-CCR (CRE52):
    https://www.bis.org/committees/bcbs/basel-framework/standard/cre/52/inforce/2023-01-01/published/2020-06-05
-   Basel Framework --- collateral recognition (CRE22):
    https://www.bis.org/committees/bcbs/basel-framework/standard/cre/22/inforce/2028-11-01/published/2025-10-28
-   BCBS-IOSCO margin requirements:
    https://www.iosco.org/library/pubdocs/pdf/ioscopd423.pdf
-   ISDA SIMM: https://www.isda.org/isda-solutions-infohub/isda-simm/
-   RBI DBIE: https://data.rbi.org.in
-   RBI Financial Markets Watch:
    https://www.rbi.org.in/Scripts/financialmarketswatch.aspx
-   ECB Data API: https://data.ecb.europa.eu/help/api/data
-   FRED API: https://fred.stlouisfed.org/docs/api/fred/overview.html
-   ESMA EMIR reporting:
    https://www.esma.europa.eu/data-reporting/emir-reporting
-   NIST AI Risk Management Framework:
    https://www.nist.gov/itl/ai-risk-management-framework

------------------------------------------------------------------------

**Project positioning:** An explainable multi-agent decision-support
prototype for derivative portfolio risk, PFE, margin and collateral
monitoring, with deterministic Python calculations and LLM-assisted
orchestration and reporting.
