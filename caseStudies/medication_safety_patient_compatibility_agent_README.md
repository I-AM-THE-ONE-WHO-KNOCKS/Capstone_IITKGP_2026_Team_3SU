# Medication Safety & Patient Compatibility Verification Agent

### Multi-Agent, Evidence-Grounded Medication Screening System

## 1. Project Overview

I propose to build a multi-agent system that screens prescribed
medications against a patient's basic health profile using authoritative
drug references, approved prescribing information, and predefined
validation rules.

The system will consider relevant patient information such as:

-   Age and weight
-   Existing medical conditions
-   Known drug allergies
-   Current medications
-   Prescribed medication, dosage, and frequency
-   Documented contraindications, warnings, and precautions
-   Age-specific and population-specific prescribing information

The system will retrieve relevant evidence, run structured checks, and
produce a medication safety screening report that highlights potential
concerns and the evidence supporting them.

I will build the system using a hosted OpenAI-compatible inference API
backed by a model with no more than 8 billion parameters, alongside an
embeddings endpoint. I will not fine-tune the model or require access to
its weights or logits. The focus will be on retrieval, prompting, tool
design, orchestration, validation, and evaluation.

**Intended use:** This is a screening and decision-support prototype for
review by a qualified healthcare professional. It will not prescribe
medication, change treatment, or autonomously approve or reject
prescriptions.

------------------------------------------------------------------------

## 2. Problem Statement

Medication safety depends on patient-specific factors, including age,
weight, existing medical conditions, allergies, and other medications
being taken. Prescribing information also contains important details
about contraindications, warnings, precautions, interactions, and
dosage.

This information is distributed across drug labels and reference
sources. Reviewing it manually for every medication and patient profile
can be time-consuming and may make it difficult to identify all relevant
information consistently.

I aim to develop a multi-agent system that analyses a supplied
medication list against a structured patient profile, retrieves relevant
information from authoritative drug references, applies deterministic
validation rules where possible, and identifies potential
medication-related safety concerns with supporting evidence.

The system will clearly distinguish between a documented
contraindication, a warning or precaution, a possible interaction, a
dosage discrepancy, and a case where the available evidence is
insufficient.

------------------------------------------------------------------------

## 3. Project Objectives

I intend to build a system that can:

1.  Accept a structured patient profile and prescribed medication
    details.
2.  Identify and standardise medication names using a recognised drug
    terminology.
3.  Retrieve relevant prescribing information and drug-label sections.
4.  Check age-related restrictions and warnings.
5.  Identify documented contraindications relevant to the patient's
    conditions.
6.  Identify potential interactions among the supplied medications.
7.  Compare a prescribed dosage with applicable labelled dosing
    information when the required patient and indication details are
    available.
8.  Provide evidence and citations for every finding.
9.  Identify missing patient information or insufficient evidence rather
    than making unsupported assumptions.
10. Validate agent outputs using schemas, deterministic tools, and a
    critic/verification step.

------------------------------------------------------------------------

## 4. Supported Use Cases

### Use Case 1: Age-Related Medication Screening

**Example patient profile**

-   Age: 8 years
-   Prescribed medication: Medication A

**Example query**

Is Medication A labelled for use in an 8-year-old patient?

**Expected output**

-   Relevant age indication or restriction found in the drug label.
-   Any age-specific warning or dosage information.
-   Whether the available label supports use for the stated age and
    indication.
-   Source document and relevant section.
-   Any missing information that prevents a reliable check.

The system must not infer that a medication is safe simply because an
age restriction was not found in the retrieved text.

### Use Case 2: Medical Condition and Contraindication Screening

**Example patient profile**

-   Age: 65 years
-   Existing condition: Chronic kidney disease
-   Prescribed medication: Medication B

**Example query**

Does Medication B have any contraindications, warnings, or precautions
relevant to kidney disease?

**Expected output**

-   Relevant contraindications, if explicitly documented.
-   Kidney-related warnings and precautions.
-   Labelled renal dose adjustment information, if available.
-   Evidence and source references.
-   A clear indication when the patient's condition or clinical details
    are insufficient for assessment.

### Use Case 3: Drug Interaction Screening

**Example patient profile**

-   Medication 1: Medication A
-   Medication 2: Medication B
-   Medication 3: Medication C

**Example query**

Identify documented or recognised potential interactions among these
medications.

**Expected output**

-   Medication pairs for which an interaction was identified.
-   Interaction description and severity classification, if supplied by
    the source.
-   Relevant management or monitoring information from the source, where
    available.
-   Supporting references.
-   An explicit indication when no interaction information could be
    verified.

### Use Case 4: Allergy-Related Screening

**Example patient profile**

-   Known allergy: Penicillin
-   Prescribed medication: Medication C

**Example query**

Does Medication C contain an ingredient associated with the patient's
recorded allergy, or does its label document a relevant allergy warning?

**Expected output**

-   Relevant active and inactive ingredient information, where
    available.
-   Labelled allergy warnings or contraindications.
-   Documented cross-reactivity information, if available.
-   Supporting evidence.
-   A clear statement when the available information is insufficient to
    establish compatibility.

The system must not independently infer cross-reactivity without
supporting evidence.

### Use Case 5: Dosage Verification

**Example patient profile**

-   Age: 12 years
-   Weight: 40 kg
-   Prescribed medication: Medication D
-   Prescribed dosage: 500 mg twice daily
-   Indication: A specified condition

**Example query**

Does the supplied prescription fall within the applicable labelled
dosage information for this patient profile and indication?

**Expected output**

-   Applicable labelled dosage information, if available.
-   Supplied prescribed dosage and frequency.
-   A deterministic comparison against the applicable label criteria.
-   Any discrepancy or missing information.
-   Supporting source and section.

The system will only perform this check when the required
information---such as indication, formulation, route, age, and
weight---is available. It will not independently recommend or calculate
a new prescription.

------------------------------------------------------------------------

## 5. Result Classification

I will avoid using a single generic medication safety grade. A/B/C/D are
historical FDA pregnancy risk categories, not general-purpose medication
suitability grades. The FDA replaced these letter categories with
narrative pregnancy and lactation labelling under the Pregnancy and
Lactation Labeling Rule.

Instead, the system will use the following screening outcomes:

  -----------------------------------------------------------------------
  Outcome                             Meaning
  ----------------------------------- -----------------------------------
  Contraindication identified         An authoritative source explicitly
                                      identifies a contraindication
                                      relevant to the supplied patient
                                      profile.

  Warning or precaution identified    A relevant warning or precaution is
                                      documented and requires
                                      professional review.

  Potential interaction identified    A source identifies a possible
                                      interaction between supplied
                                      medications.

  Dosage discrepancy identified       The supplied dosage appears
                                      inconsistent with the applicable
                                      labelled criteria checked by the
                                      system.

  No issue identified in checked      The defined checks found no
  sources                             relevant issue in the sources
                                      reviewed. This does not establish
                                      that the medication is safe.

  Insufficient information            Required patient, medication,
                                      indication, or source information
                                      is missing or inconclusive.

  Conflicting evidence                Sources provide inconsistent
                                      information that requires
                                      professional review.
  -----------------------------------------------------------------------

These outcomes are screening findings, not clinical decisions. The
system will preserve the distinction between "not found" and "confirmed
absent."

------------------------------------------------------------------------

## 6. Proposed Multi-Agent Architecture

I plan to implement a planner → executor → critic workflow using a
multi-agent orchestration framework such as LangGraph.

### Agents and Responsibilities

  -----------------------------------------------------------------------
  Agent                               Responsibility
  ----------------------------------- -----------------------------------
  Patient Profile Agent               Extracts and validates patient age,
                                      weight, conditions, allergies, and
                                      current medications.

  Drug Information Agent              Identifies medications and
                                      retrieves relevant drug labels and
                                      reference information.

  Contraindication Agent              Checks age-related restrictions,
                                      documented contraindications,
                                      warnings, and precautions.

  Interaction and Dosage Agent        Checks medication interactions and
                                      performs deterministic dosage
                                      comparisons when applicable.

  Safety Verification Agent           Validates findings against source
                                      evidence, checks consistency, and
                                      prepares the final screening
                                      report.
  -----------------------------------------------------------------------

### High-Level Workflow

1.  I receive a patient profile and medication screening request.
2.  The Planner Agent identifies which checks are relevant and creates
    an execution plan.
3.  The Patient Profile Agent validates and structures the supplied
    patient information.
4.  The Drug Information Agent resolves medication identities and
    retrieves relevant references.
5.  The Contraindication Agent and Interaction and Dosage Agent perform
    their assigned checks.
6.  The Safety Verification Agent checks whether each finding is
    supported by retrieved evidence and valid tool outputs.
7.  The system generates a structured report with findings, citations,
    missing information, and a healthcare-professional review boundary.

The agents will share structured state. The system will include
timeouts, retry limits, JSON schema validation, tool-failure handling,
and loop limits to prevent unbounded execution.

------------------------------------------------------------------------

## 7. Data Sources

I will prioritise authoritative, publicly accessible sources and use
them according to their documented access and usage terms.

  -----------------------------------------------------------------------
  Source                  Information             Planned usage
  ----------------------- ----------------------- -----------------------
  DailyMed (NLM)          Structured and          Primary document corpus
                          full-text medication    for RAG
                          labelling               

  openFDA Drug Label API  Machine-readable        Drug information
                          drug-label sections     retrieval and
                                                  structured checks

  RxNorm / RxNav          Standardised medication Medication name
                          concepts and            normalisation and
                          identifiers             identity resolution

  FDA drug labelling      Labelling guidance and  Background and
  resources               reference material      interpretation of label
                                                  structure
  -----------------------------------------------------------------------

### 7.1 DailyMed --- National Library of Medicine

https://dailymed.nlm.nih.gov/dailymed/

DailyMed provides access to medication labelling submitted to the FDA
and other labelling information. I will use it as a primary source for
retrieving prescribing information, including:

-   Indications and usage
-   Dosage and administration
-   Contraindications
-   Warnings and precautions
-   Drug interactions
-   Use in specific populations

### 7.2 openFDA Drug Label API

https://open.fda.gov/apis/drug/label/

I will use the openFDA drug-label API to retrieve machine-readable label
data. Relevant sections include:

-   `indications_and_usage`
-   `dosage_and_administration`
-   `contraindications`
-   `warnings_and_cautions`
-   `drug_interactions`
-   `use_in_specific_populations`

I will retain source identifiers and relevant label metadata so that
findings can be traced back to the original information.

### 7.3 RxNorm and RxNav

https://lhncbc.nlm.nih.gov/RxNav/APIs/

I will use RxNorm/RxNav to help resolve brand and generic medication
names and map them to standardised medication concepts. This is
important because the same active ingredient may appear under multiple
brand names or formulations.

### Data Collection Strategy

For the initial implementation, I will:

-   Restrict the corpus to a manageable set of approximately 100--200
    medications.
-   Collect relevant labels and sections for those medications.
-   Prefer structured API responses where available.
-   Preserve the original source, label version or update information,
    and retrieval date.
-   Store extracted text with section-level metadata.
-   Build a small, controlled set of synthetic patient scenarios for
    testing.

I will verify API access, available fields, rate limits, and data-use
conditions before finalising the ingestion pipeline.

------------------------------------------------------------------------

## 8. Patient and Medication Data Schema

I will use a predefined schema to keep patient information and
medication details consistent across agents.

### Example Patient Profile

``` json
{
  "patient_id": "patient_001",
  "age_years": 65,
  "weight_kg": 70,
  "conditions": [
    "chronic kidney disease"
  ],
  "allergies": [
    "penicillin"
  ],
  "current_medications": [
    {
      "name": "Medication A",
      "strength": "example strength",
      "formulation": "tablet"
    }
  ],
  "prescribed_medications": [
    {
      "name": "Medication B",
      "strength": "example strength",
      "dose": "example dose",
      "frequency": "example frequency",
      "route": "oral",
      "indication": "example indication"
    }
  ]
}
```

The example is illustrative and does not represent a real patient or
prescription.

### Example Screening Finding

``` json
{
  "medication": "Medication B",
  "check_type": "condition_contraindication",
  "outcome": "warning_or_precaution_identified",
  "finding": "A relevant warning was found in the retrieved label.",
  "patient_factor": "chronic kidney disease",
  "evidence": {
    "source": "DailyMed",
    "document_id": "example-label-id",
    "section": "Warnings and Precautions",
    "text_reference": "Relevant source passage"
  },
  "requires_professional_review": true
}
```

The implementation will use strict JSON schemas and validation. It will
also support unknown or unavailable fields rather than forcing agents to
invent values.

------------------------------------------------------------------------

## 9. RAG Implementation

I will build a retrieval-augmented generation pipeline over the
collected drug-label corpus.

### Document Ingestion

-   Retrieve drug-label data from the selected sources.
-   Extract relevant label sections.
-   Preserve medication identifiers and source metadata.
-   Store section names and source references with each document chunk.

### Chunking Experiments

I will compare:

1.  Fixed-size chunking.
2.  Section-based chunking.
3.  Section-based chunking with limited overlap.

The goal is to preserve the context of individual label sections,
particularly contraindications, warnings, dosage, and drug interactions.

### Hybrid Retrieval

I will combine:

-   BM25 retrieval for exact medication names, ingredients, and
    technical terms.
-   Dense retrieval for semantic queries about patient conditions and
    medication warnings.
-   Hybrid result fusion to combine lexical and semantic matches.

### Reranking

A reranker will prioritise the most relevant medication-label sections
for the current check.

### Evidence-Grounded Responses

-   Each finding must reference retrieved evidence.
-   Medication identity and formulation must be considered when
    selecting evidence.
-   The system must not treat an absent search result as proof that no
    risk exists.
-   Conflicting or incomplete sources must be reported.
-   Citations should identify the source document and relevant section.

I will measure retrieval recall separately from the accuracy of the
final screening report.

------------------------------------------------------------------------

## 10. Tools

I will expose deterministic tools to the agents rather than relying on
the language model to perform every operation.

  -----------------------------------------------------------------------
  Tool                                Responsibility
  ----------------------------------- -----------------------------------
  Medication Lookup Tool              Resolves a supplied medication name
                                      using the selected drug terminology
                                      service.

  Drug Label Retrieval Tool           Retrieves relevant label sections
                                      for a medication.

  Patient Profile Validation Tool     Checks required fields, data types,
                                      and consistency in the patient
                                      profile.

  Contraindication Matching Tool      Matches structured patient factors
                                      against documented contraindication
                                      and warning information.

  Drug Interaction Lookup Tool        Retrieves and checks interaction
                                      information from the selected
                                      reference source.

  Dosage Comparison Tool              Compares supplied dosage
                                      information against applicable
                                      structured label criteria when
                                      available.
  -----------------------------------------------------------------------

Tool outputs will use predefined JSON schemas. The system will validate
arguments and results, retry recoverable failures within a fixed limit,
and record tool execution traces.

**Important implementation boundary:** A tool may report that a source
contains a warning or that a supplied value differs from a labelled
criterion. It must not independently decide what treatment the patient
should receive.

------------------------------------------------------------------------

## 11. Evaluation Strategy

I will create an evaluation dataset containing approximately 60--100
synthetic patient scenarios with expected findings based on
authoritative drug-label evidence.

The expected answers will be prepared before implementation and checked
against the source references. Where possible, a qualified pharmacist or
clinician should review the test cases and expected interpretations.

### Evaluation Categories

  Category                     Metric
  ---------------------------- -----------------------------------
  Medication identification    Exact match accuracy
  Age-related checks           Precision, recall, and F1
  Contraindication detection   Precision, recall, and F1
  Interaction detection        Precision, recall, and F1
  Dosage comparison            Deterministic comparison accuracy
  Evidence retrieval           Recall@K
  Citation validation          Citation correctness
  Outcome classification       Classification accuracy
  End-to-end execution         Task completion rate

### Example Evaluation Scenarios

  -----------------------------------------------------------------------
  Scenario                            Expected system behaviour
  ----------------------------------- -----------------------------------
  Patient is below an explicitly      Identify the relevant age
  labelled minimum age                restriction and cite the label.

  Patient has a condition explicitly  Flag the contraindication and cite
  listed in a medication              the relevant section.
  contraindication                    

  Two supplied medications have a     Identify the medication pair and
  documented interaction in the       cite the interaction information.
  selected source                     

  A relevant dosage value exceeds an  Flag a dosage discrepancy, provided
  applicable labelled limit           all required context is available.

  Patient allergy is associated with  Flag the relevant information and
  a documented ingredient or warning  cite the source.

  Required patient information is     Return insufficient information
  missing                             rather than guessing.

  Retrieved sources conflict          Report conflicting evidence and
                                      require professional review.
  -----------------------------------------------------------------------

### Failure Taxonomy

I will classify failures into:

1.  Retrieval miss --- relevant drug-label evidence was not retrieved.
2.  Medication identification error --- the wrong drug, ingredient,
    strength, or formulation was resolved.
3.  Extraction error --- the relevant label information was extracted
    incorrectly.
4.  Matching error --- patient factors were matched to the wrong warning
    or contraindication.
5.  Reasoning error --- the evidence was retrieved correctly but
    interpreted incorrectly.
6.  Tool-call error --- malformed arguments, invalid JSON, or failed
    tool execution.
7.  Hallucination --- a finding was produced without supporting
    evidence.
8.  Citation error --- a citation was missing or did not support the
    finding.

------------------------------------------------------------------------

## 12. Responsible AI and Safety Boundaries

Because this project concerns medication information, I will apply
stricter safeguards than I would for a general research assistant.

-   The system will be presented as a medication safety screening
    prototype, not a diagnostic or prescribing system.
-   It will not recommend starting, stopping, replacing, or changing
    medication.
-   It will not autonomously approve or reject a prescription.
-   It will not infer a diagnosis from a patient profile.
-   It will distinguish contraindications from warnings and precautions.
-   It will distinguish "no issue found in checked sources" from
    "confirmed safe."
-   It will identify missing information and conflicting evidence.
-   It will provide traceable citations for every safety finding.
-   It will treat retrieved documents as untrusted input and test for
    prompt injection.
-   It will not follow instructions embedded in retrieved drug labels or
    documents.
-   It will avoid using identifiable real patient information in the
    evaluation dataset.
-   It will require healthcare-professional review for consequential
    decisions.

I will also document the limitations of the selected sources. Public
drug labels may not contain every clinically relevant interaction,
patient-specific consideration, or current clinical guideline.

------------------------------------------------------------------------

## 13. Proposed Technology Stack

  Component              Proposed technology
  ---------------------- ----------------------------------------------------
  Programming language   Python
  LLM                    Hosted OpenAI-compatible inference API (≤8B model)
  Embeddings             Hosted embeddings API
  Agent orchestration    LangGraph
  RAG framework          LangChain or custom implementation
  Vector database        ChromaDB or FAISS
  Keyword retrieval      BM25
  Reranking              Cross-encoder reranker
  Drug terminology       RxNorm/RxNav
  Drug information       DailyMed and openFDA
  Backend                FastAPI
  Frontend               Streamlit
  Database               SQLite or PostgreSQL
  Evaluation             Custom evaluation framework
  Observability          Langfuse, LangSmith, or OpenTelemetry
  Deployment             Docker

I will keep the architecture modular so that the drug information
provider, embedding model, and hosted LLM can be changed without
redesigning the entire application.

------------------------------------------------------------------------

## 14. Four-Week Implementation Plan

### Week 1 --- Problem Definition and Baseline

**Deliverables**

-   Finalise the screening scope and safety boundaries.
-   Define patient and medication schemas.
-   Select the initial medication set.
-   Retrieve an initial collection of drug labels.
-   Create 60--100 evaluation scenarios and expected findings.
-   Implement a single-agent prompting baseline.
-   Compare at least three prompting strategies.
-   Document small-model limitations and baseline results.

### Week 2 --- RAG and Drug Information Retrieval

**Deliverables**

-   Build the drug-label ingestion pipeline.
-   Integrate medication name resolution.
-   Extract relevant label sections.
-   Implement chunking experiments.
-   Implement BM25 and dense retrieval.
-   Add hybrid retrieval and reranking.
-   Generate evidence-grounded outputs.
-   Measure retrieval recall@K separately.

### Week 3 --- Multi-Agent Orchestration

**Deliverables**

-   Implement the Planner Agent.
-   Implement the Patient Profile Agent.
-   Implement the Drug Information Agent.
-   Implement contraindication, interaction, and dosage checks.
-   Implement the Safety Verification Agent.
-   Integrate deterministic tools.
-   Add persistent state and execution tracing.
-   Implement JSON schema validation, retries, timeouts, and loop
    limits.

### Week 4 --- Evaluation and Deployment

**Deliverables**

-   Run the complete evaluation dataset.
-   Measure end-to-end accuracy and task completion.
-   Analyse retrieval, extraction, reasoning, and tool failures.
-   Measure average tool calls, latency, and cost per query.
-   Implement the Streamlit interface.
-   Containerise the application using Docker.
-   Add caching and rate limiting.
-   Run prompt-injection and responsible-AI tests.
-   Prepare the final technical report and demonstration.

------------------------------------------------------------------------

## 15. Expected Final Deliverables

At the end of the capstone, I expect to deliver:

1.  A working multi-agent medication safety screening application.
2.  A structured patient and medication input schema.
3.  A curated drug-label corpus with source metadata.
4.  A medication identification and normalisation component.
5.  A hybrid RAG pipeline with reranking.
6.  At least three integrated deterministic tools.
7.  Planner → executor → critic orchestration.
8.  Persistent state and robust tool-error handling.
9.  An evaluation dataset of 60--100 scenarios.
10. Retrieval and end-to-end evaluation results.
11. Failure taxonomy and observability traces.
12. A Streamlit frontend and Docker deployment.
13. A responsible-AI and prompt-injection testing report.

------------------------------------------------------------------------

## 16. Project Success Criteria

I will consider the project successful when it can:

-   Accept a structured patient profile and medication list.
-   Resolve medication identities against a recognised terminology.
-   Retrieve relevant drug-label information.
-   Identify documented age-related restrictions, contraindications,
    warnings, and interactions.
-   Perform supported dosage comparisons using deterministic tools.
-   Produce evidence-backed findings with traceable citations.
-   Identify missing or conflicting information without guessing.
-   Handle malformed tool calls, retrieval failures, and model-output
    errors.
-   Demonstrate measurable performance against a predefined evaluation
    dataset.
-   Operate within the limitations of a hosted model with no more than 8
    billion parameters.

### Core Engineering Objective

**I aim to demonstrate how a reliable, evidence-grounded medication
safety screening system can be built around a small hosted language
model by combining RAG, specialised agents, deterministic validation
tools, structured outputs, orchestration, and systematic evaluation.**

The language model will coordinate the workflow and interpret retrieved
information. Deterministic components will handle structured validation
and calculations. Authoritative drug references will provide the
evidence, and healthcare professionals will remain responsible for
clinical decisions.
