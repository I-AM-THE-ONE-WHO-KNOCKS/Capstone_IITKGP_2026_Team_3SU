# Multi-Agent Counterparty Risk & Collateral Management System

## 1. Problem Statement

Institutional banks face significant challenges in monitoring counterparty credit exposure and managing collateral requirements for Over-the-Counter (OTC) derivative portfolios. These activities involve complex financial calculations, interpretation of legal agreements, continuous monitoring of market movements, and compliance with regulatory requirements.

Risk analysts often need to work across multiple data sources, financial models, legal agreements, and regulatory documents to assess counterparty exposure and determine collateral obligations. This creates operational complexity, increases manual effort, and makes it difficult to obtain timely and consistent risk assessments.

The objective is to develop an **agentic AI system with multiple specialized agents** that collaborate to automate counterparty risk analysis, collateral calculations, and legal agreement interpretation.

### Specialized Agents

#### 1. Risk Analysis Agent
- Analyse counterparty portfolios and derivative positions.
- Calculate Current Exposure (Mark-to-Market) and Potential Future Exposure (PFE).
- Identify potential credit limit breaches.
- Explain exposure changes based on market movements.

#### 2. Margin Management Agent
- Calculate collateral requirements based on counterparty exposure.
- Apply CSA parameters such as Threshold and Minimum Transfer Amount (MTA).
- Calculate collateral delivery and return amounts.
- Apply regulatory haircuts to eligible collateral.

#### 3. Legal Knowledge Agent
- Retrieve relevant clauses from ISDA and Credit Support Annex (CSA) documents.
- Answer questions about collateral eligibility, thresholds, MTA, and margin requirements.
- Retrieve relevant regulatory guidelines.
- Provide source references for retrieved information.

#### 4. Orchestrator Agent
- Understand user requests and identify the required operations.
- Delegate tasks to appropriate specialized agents.
- Coordinate execution when multiple agents are required.
- Consolidate agent responses into a unified answer.
- Ensure results are validated before presenting them to users.

### Expected Outcome

A conversational, multi-agent system that enables banking risk analysts to analyse counterparty exposure, calculate collateral obligations, and retrieve relevant legal and regulatory information through natural-language interactions.

Financial calculations will be performed using deterministic Python tools rather than relying on LLM-generated calculations. Responses will include calculation details, supporting document references, and appropriate human approval requirements.

The system will operate as a decision-support platform. It will not independently execute financial transactions, release collateral, or modify credit limits.

---

## 2. Proposed Data Sources

The system will use a combination of publicly available financial documents, regulatory publications, historical market data, and synthetic counterparty portfolios.

### A. Legal & Contractual Documents

| Data Source | Data Required | Purpose |
|---|---|---|
| SEC EDGAR | Public company 8-K filings containing ISDA/CSA exhibits | Build a searchable repository of legal agreements. |
| ISDA | CSA templates, collateral schedules, and operational guidelines | Understand contractual terms and collateral mechanics. |

- SEC EDGAR: <https://www.sec.gov/edgar/search/>
- ISDA: <https://www.isda.org/>

### B. Regulatory Documents

| Data Source | Data Required | Purpose |
|---|---|---|
| BCBS-IOSCO | Margin requirements for non-centrally cleared derivatives | Understand regulatory margin requirements and collateral eligibility. |
| BIS Basel Framework | Standardised supervisory haircut schedules | Implement regulatory haircut calculations. |

- BCBS-IOSCO Framework: <https://www.bis.org/bcbs/publ/d317.htm>
- BIS Basel Framework: <https://www.bis.org/basel_framework/>

### C. Financial Market Data

| Data Source | Data Required | Purpose |
|---|---|---|
| Yahoo Finance | Historical daily prices for selected financial instruments | Calculate historical volatility and covariance. |
| `yfinance` Python library | Historical market data retrieval | Supply market data to the risk calculation engine. |

Suggested instruments:

| Symbol | Instrument | Purpose |
|---|---|---|
| SPY | S&P 500 ETF | Equity market exposure |
| SHY | Short-term Treasury ETF | Short-duration bond proxy |
| LQD | Investment-grade corporate bond ETF | Corporate bond proxy |
| EURUSD=X | EUR/USD exchange rate | Currency exposure |

- Yahoo Finance: <https://finance.yahoo.com/>
- `yfinance`: <https://github.com/ranaroussi/yfinance>

### D. Counterparty Portfolio Data

For the initial implementation, use synthetic portfolio data rather than confidential banking data.

Create sample datasets containing:

- Counterparty ID and name.
- Derivative contract ID.
- Instrument type.
- Notional amount.
- Trade currency.
- Trade maturity.
- Current market value.
- Underlying market instrument.
- Existing collateral amount.
- Approved credit/PFE limit.

Suggested sample counterparties:

| Counterparty | Portfolio Type | Example |
|---|---|---|
| Meridian Capital | Equity and FX derivatives | SPY, EUR/USD |
| Apex Financial | Bond and interest rate exposure | SHY, LQD |

Store the data in CSV files for the initial implementation.

---

## 3. Agent Data Access and Tools

| Agent | Primary Data Sources | Tools |
|---|---|---|
| Risk Analysis Agent | Synthetic portfolio CSV, Yahoo Finance | Monte Carlo simulation, MtM calculation, PFE calculation |
| Margin Management Agent | Portfolio data, CSA parameters, BCBS-IOSCO | Margin calculation, MTA, haircut calculation |
| Legal Knowledge Agent | SEC EDGAR, ISDA, BIS documents | Hybrid RAG, document retrieval, citation generation |
| Orchestrator Agent | Outputs from all specialized agents | Intent classification, task delegation, response composition |

---

## 4. Initial Implementation Boundaries

To make the project achievable within four weeks:

- Support 2–3 synthetic counterparties.
- Use predefined derivative portfolio datasets.
- Implement a basic Monte Carlo PFE calculation.
- Support a limited set of CSA parameters.
- Use a predefined regulatory haircut schedule.
- Index a limited collection of CSA and regulatory documents.
- Implement three specialized agents and one orchestrator.
- Provide a simple conversational interface.
- Require human approval for sensitive financial operations.

Actual collateral transfers, live banking integrations, and production-grade regulatory certification are outside the initial scope.

---

## 5. Suggested Data Repository Structure

```text
data/
├── portfolio/
│   ├── counterparties.csv
│   ├── derivative_positions.csv
│   ├── collateral_positions.csv
│   └── credit_limits.csv
│
├── agreements/
│   ├── csa/
│   │   ├── meridian_csa.pdf
│   │   └── apex_csa.pdf
│   └── isda/
│       └── isda_reference.pdf
│
├── regulatory/
│   ├── bcbs_iosco_margin.pdf
│   └── haircut_schedule.csv
│
├── market_data/
│   ├── SPY.csv
│   ├── SHY.csv
│   ├── LQD.csv
│   └── EURUSD.csv
│
└── evaluation/
    └── test_queries.json
```

---

## 6. Data-Source Considerations

- **SEC EDGAR:** Provides filed agreements and exhibits, but not every filing contains a complete or directly usable CSA.
- **ISDA:** Provides templates and operational guidance. These can be used to create sample agreements for the initial system.
- **BCBS-IOSCO:** Use official regulatory publications for margin requirements and applicable haircut guidance. Haircuts depend on factors such as collateral category and maturity.
- **Yahoo Finance:** Provides market-price history, not counterparty exposure or actual derivative valuations.
- **Synthetic portfolios:** Generate sample derivative positions, CSA parameters, collateral balances, and credit limits for testing.

Keep contractual information and regulatory information separate in the RAG knowledge base. The Legal Knowledge Agent should distinguish between the terms of a specific counterparty's CSA and the applicable regulatory framework.

---

## 7. Four-Week Implementation Plan

| Week | Deliverable |
|---|---|
| Week 1 | Data preparation, document ingestion, basic orchestrator and agent routing |
| Week 2 | Risk Analysis Agent and Margin Management Agent |
| Week 3 | Legal Knowledge Agent, RAG, citations, and multi-agent coordination |
| Week 4 | Validation, evaluation dataset, user interface, testing, and final demonstration |

### Suggested Technology Stack

- **Language:** Python
- **Agent orchestration:** LangGraph
- **LLM:** An 8B-class instruction-tuned model
- **RAG / Vector database:** ChromaDB
- **Keyword retrieval:** BM25
- **Embeddings and reranking:** Sentence Transformers
- **Financial computation:** NumPy and Pandas
- **Market data:** `yfinance`
- **User interface:** Streamlit

### Four-Week Deliverable

A working multi-agent prototype that can accept natural-language questions, route them to specialized agents, calculate sample counterparty exposure and collateral requirements, retrieve relevant agreement and regulatory clauses, and return a consolidated answer with calculation details and source citations.

The prototype is for decision support and demonstration only. It is not a production banking risk system or a substitute for legal, regulatory, or independent model validation.
