# Product North Star — Governed Pharma Evidence & Context Platform

## Purpose

This document is the durable business and product blueprint for the platform.

The platform is **not positioned as a pharma chatbot or generic GraphRAG application**. Its purpose is to create a governed scientific context layer that turns fragmented scientific content into validated evidence, traceable claims, and policy-controlled AI answers.

> **North-star positioning:** A governed scientific context layer that converts fragmented pharma evidence into validated knowledge, traceable claims, and policy-controlled AI answers.

---

## 1. Business issue

Pharma organizations hold large volumes of scientific, clinical, regulatory, and medical content, but teams still spend significant time finding the right evidence, interpreting what it means, determining whether it has been scientifically validated, and checking whether it can be used for a specific purpose, audience, and market.

The business problem is therefore not simply poor search.

> **Evidence exists, but organizations cannot reliably convert distributed scientific evidence into trusted, reusable, governed knowledge fast enough.**

Typical consequences include:

- slow evidence discovery;
- repeated manual interpretation of the same evidence;
- duplicated SME and Medical review;
- weak reuse of previously validated knowledge;
- inconsistent evidence-to-claim lineage;
- MLR rework;
- difficulty distinguishing scientific validity from permitted use;
- compliance risk when AI systems retrieve or generate content without approval-state awareness.

---

## 2. Root causes

| Root cause | Current-state effect |
|---|---|
| Fragmented evidence | Publications, trials, labels, regulatory documents, and internal content live across disconnected repositories |
| Terminology inconsistency | Brand, molecule, trial, indication, endpoint, and disease concepts appear under different names |
| Document-centric knowledge | Organizations store files instead of reusable scientific evidence objects |
| Retrieval is not understanding | Keyword/vector retrieval can find text but does not reliably represent scientific relationships |
| Extraction is not validation | Machine-extracted evidence is not automatically scientifically trustworthy |
| Scientific validation is not promotional approval | A scientifically correct statement may still be impermissible for a specific market or use |
| Governance is detached from retrieval | Role, purpose, market, validity, and approval are often checked downstream |
| Weak lineage | Users may receive an answer without a clear path back to the exact evidence and reviewer decisions |
| Manual hand-offs | Evidence discovery, SME review, Medical review, and MLR repeatedly reconstruct context |

The core root-cause statement is:

> **The enterprise lacks a governed semantic evidence layer connecting scientific evidence, meaning, validation, claims, approvals, and permitted use.**

---

## 3. Strategic options

### Option A — Improve enterprise search / RAG

Improve chunking, embeddings, reranking, and conversational search.

**Strength:** faster discovery.

**Limitation:** solves findability, not scientific trust, claim lineage, or permitted use.

### Option B — Add governance around conventional RAG

Keep a document-centric RAG architecture but add RBAC, citations, audit, and approval metadata.

**Strength:** stronger control around retrieval.

**Limitation:** the system still lacks a first-class scientific evidence model and reusable evidence-to-claim relationships.

### Option C — Build a governed semantic evidence platform

Normalize scientific concepts, structure evidence, validate evidence, compose evidence-bound claims, apply Medical/MLR governance, and enforce those decisions at query time.

**Recommendation:** pursue Option C incrementally.

Target transformation:

```text
Documents → Search → LLM → Answer
```

becomes:

```text
Scientific sources
        ↓
Semantic normalization
        ↓
Structured scientific evidence
        ↓
SME validation
        ↓
Evidence-bound claims
        ↓
Medical / MLR governance
        ↓
Role + purpose + market policy
        ↓
Governed answer
```

---

## 4. Product objective

Create a governed context layer that:

1. understands canonical scientific meaning;
2. converts source passages into reusable scientific evidence structures;
3. separates machine extraction from human validation;
4. ensures claims are derived from validated evidence;
5. separates Medical validity from MLR approval;
6. enforces market, purpose, audience, validity, and role restrictions at runtime;
7. preserves complete source and decision lineage.

---

## 5. Evidence-to-use business value chain

| Stage | Business activity | Current pain | Platform role |
|---|---|---|---|
| Evidence acquisition | Publications, trial results, labels, regulatory and internal content enter the enterprise | Evidence is scattered | Ingest, classify, preserve provenance |
| Evidence discovery | Medical/Regulatory users search for relevant information | Keyword/RAG retrieval misses terminology relationships | Semantic + lexical + graph retrieval |
| Scientific interpretation | SME determines what the evidence says | Same evidence repeatedly interpreted | Structure Study → Population → Intervention → Comparator → Endpoint → Outcome → Safety |
| Evidence validation | SME confirms extraction accuracy | Machine extraction cannot be trusted automatically | Field-level verify/correct/reject |
| Claim formulation | Teams convert evidence into usable statements | Manual drafting can lose traceability | Evidence-bound deterministic claim composition |
| Medical review | Reviewer confirms scientific fidelity | Reviewers reconstruct source support | Candidate + exact validated support package |
| MLR review | Medical/Legal/Regulatory determine permitted wording/use | Approval disconnected from AI use | Market/purpose/audience/validity approval object |
| Governed reuse | Medical, Regulatory, Commercial, and AI consume knowledge | Wrong or stale content can be reused | Query-time policy enforcement |
| Audit & monitoring | Compliance reconstructs decisions | Expensive manual reconstruction | Hash-linked decision and evidence lineage |

Target process:

```text
Evidence
   ↓
Canonical scientific knowledge
   ↓
Structured evidence
   ↓
SME-validated evidence
   ↓
Evidence-bound claim
   ↓
Medical validation
   ↓
MLR approval
   ↓
Governed reusable knowledge
   ↓
Medical / Regulatory / Commercial / AI workflows
```

---

## 6. Primary personas

### Medical Information / Medical Affairs

**Question:** What evidence supports this scientific question?

Needs:

- fast evidence discovery;
- semantic resolution;
- validated scientific structure;
- source citations;
- evidence lineage;
- governance state.

Primary view:

```text
Answer → Evidence → Study → Endpoint → Outcome → Source → Validation
```

### Scientific / Medical SME

**Question:** Is the machine interpretation scientifically accurate?

Reviews:

- Study
- Population
- Intervention
- Comparator
- Endpoint
- Outcome
- Safety

Decisions:

- `VERIFIED`
- `CORRECTED`
- `REJECTED`

The source, machine extraction, and SME decision must remain distinguishable.

### Medical Claim Reviewer

**Question:** Does this proposed statement faithfully represent validated evidence?

Reviews:

- proposed claim;
- supporting validated fields;
- exact outcome/safety sentence;
- document/chunk/page;
- evidence validation ID.

Decisions:

- `VALIDATED`
- `REJECTED`
- `NEEDS_REVISION`

The composer cannot validate the same claim.

### MLR Reviewer

**Question:** Can this scientifically valid claim be used in this wording, market, audience, and purpose?

Reviews:

- wording;
- evidence support;
- indication;
- audience;
- purpose;
- market;
- conditions of use;
- effective and expiry dates.

Potential decisions:

- `APPROVED`
- `APPROVED_WITH_CHANGES`
- `REJECTED`

---

## 7. Hero use case

### Question

> What evidence supports BRUKINSA in relapsed/refractory CLL?

### Target journey

```text
Question
   ↓
BRUKINSA resolved to canonical concept
   ↓
BRUKINSA —BRAND_OF→ zanubrutinib
   ↓
Relevant ALPINE evidence retrieved
   ↓
Evidence Intelligence structures the passage
   ↓
SME validates scientific fields
   ↓
Evidence-bound claim composed
   ↓
Independent Medical review
   ↓
MLR approval where required
   ↓
Policy evaluates user + purpose + market + validity
   ↓
Governed answer / evidence-only / block / abstain
```

This demonstrates the core distinction:

```text
Relevant evidence
      ≠
Scientifically validated evidence
      ≠
Medically validated claim
      ≠
MLR-approved permitted wording
```

---

## 8. Logical data model

```text
Source Document
      ↓
Document Chunk
      ↓
Semantic Entity / Relationship
      ↓
Evidence Object
      ↓
Evidence Validation
      ↓
Claim Candidate
      ↓
Medical Claim
      ↓
MLR Approval
      ↓
Policy
      ↓
Governed Answer
      ↓
Audit Event
```

### Core objects

| Object | Meaning |
|---|---|
| Source Document | Publication, trial document, label, regulatory file, internal content |
| Document Chunk | Exact source passage used for retrieval and lineage |
| Semantic Entity | Canonical drug, brand, trial, indication, endpoint, target, market |
| Semantic Relationship | Governed typed relationship such as `BRAND_OF`, `EVALUATES`, `COMPARES_WITH`, `STUDIES_INDICATION` |
| Evidence Object | Structured scientific interpretation of source evidence |
| Evidence Validation | SME-reviewed snapshot of that scientific structure |
| Claim Candidate | Proposed statement derived only from validated evidence |
| Medical Claim | Independently validated claim wording |
| MLR Approval | Approved wording + scope + validity |
| Policy Rule | Role/purpose/market/audience/use restriction |
| Governed Answer | Runtime output under policy |
| Audit Event | Who did what, when, why, and to which object |

---

## 9. Target architecture

```text
┌──────────────────────────────────────────────────────────────┐
│ EXPERIENCE                                                   │
│ Medical | Medical Info | Regulatory | MLR | Commercial | AI │
│ Q&A | Evidence Review | Claim Review | Governance Dashboard │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ GOVERNED RETRIEVAL & REASONING                              │
│ Hybrid Retrieval | Graph Traversal | Reranking | Answering  │
│ Role | Purpose | Market | Approval | Validity Enforcement   │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ GOVERNANCE / TRUST                                            │
│ SME | Claim Review | MLR | Policy | Audit | Versioning      │
│ Expiry | Supersession | Separation of Duties                │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ EVIDENCE INTELLIGENCE                                        │
│ Study | Population | Intervention | Comparator | Endpoint   │
│ Outcome | Safety | Evidence Lineage                          │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ SEMANTIC LAYER                                                │
│ Canonical Entities | Aliases | Relationships | External IDs │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ KNOWLEDGE / RETRIEVAL STORES                                 │
│ Canonical DB | Knowledge Graph | Vector Index | Metadata    │
│ Validated Evidence | Claims | Approvals                      │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ INGESTION & PROCESSING                                       │
│ Parse | Chunk | Classify | Security Scan | Extract | Index  │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ SOURCES                                                      │
│ Publications | Trials | Labels | Regulatory | Internal      │
└──────────────────────────────────────────────────────────────┘
```

Architectural principle:

> **RAG is a consumption capability near the top of the stack; governed scientific context is the durable asset underneath it.**

---

## 10. Governance state model

```text
SOURCE
  ↓
INGESTED
  ↓
STRUCTURED
  ↓
UNVALIDATED_EXTRACTION
  ↓
SME_VALIDATED_EVIDENCE
  ↓
CLAIM_CANDIDATE
  ↓
MEDICAL_VALIDATED
  ↓
NOT_MLR_REVIEWED
  ↓
MLR_APPROVED
  ↓
ACTIVE_FOR_GOVERNED_USE
```

Lifecycle states must also support:

- `REJECTED`
- `NEEDS_REVISION`
- `SUPERSEDED`
- `EXPIRED`
- `WITHDRAWN`
- `UNDER_REVIEW`

No state change should silently overwrite the historical machine extraction or reviewer decisions.

---

## 11. MLR model

Scientific validity and permitted communication are separate decisions.

Proposed MLR lifecycle:

```text
MEDICAL_VALIDATED
      ↓
NOT_MLR_REVIEWED
      ↓
SUBMITTED_FOR_MLR
      ↓
UNDER_MLR_REVIEW
      ├──→ REJECTED
      ├──→ NEEDS_CHANGES → RESUBMITTED
      └──→ APPROVED
                ↓
         ACTIVE_FOR_USE
                ↓
       EXPIRED / SUPERSEDED / WITHDRAWN
```

An approval belongs to a **specific claim version** and should carry:

- claim ID and version;
- approved wording;
- market;
- purpose;
- audience;
- effective-from date;
- expiry date;
- conditions of use;
- decision;
- reviewer;
- rationale;
- evidence validation ID;
- source lineage.

For promotional use, the system should prefer approved wording rather than unconstrained LLM paraphrasing.

---

## 12. Policy decision engine

### Runtime outcomes

- `ANSWER` — trusted content exists and policy permits its use.
- `EVIDENCE_ONLY` — relevant evidence exists but should not be elevated into a governed claim/answer.
- `ABSTAIN` — insufficient trusted evidence exists.
- `BLOCK` — relevant knowledge exists, but the requested use is not permitted.

Key distinction:

> **ABSTAIN = insufficient trusted knowledge.  
> BLOCK = sufficient knowledge, insufficient permission.**

### Policy inputs

Query context:

- tenant;
- actor;
- role;
- purpose;
- market;
- audience;
- question;
- current date/time.

Knowledge state:

- evidence status;
- claim status;
- MLR status;
- approved market;
- approved purpose;
- approved audience;
- effective date;
- expiry date;
- supersession/withdrawal state;
- source lineage.

### Policy matrix

| Knowledge state | Medical response | Regulatory research | Promotional content |
|---|---|---|---|
| No evidence | ABSTAIN | ABSTAIN | ABSTAIN |
| Raw evidence | EVIDENCE_ONLY | EVIDENCE_ONLY | BLOCK |
| Unvalidated structured evidence | EVIDENCE_ONLY | EVIDENCE_ONLY | BLOCK |
| SME-validated evidence | ANSWER | ANSWER | BLOCK |
| Medical-validated claim | ANSWER | ANSWER | BLOCK |
| Active applicable MLR-approved claim | ANSWER | ANSWER | ANSWER |
| Expired MLR claim | Historical/medical evidence may remain visible | Review context | BLOCK |
| Withdrawn/superseded claim | Restricted history | Review context | BLOCK |

### Evaluation order

```text
Tenant isolation
   ↓
Role permission
   ↓
Purpose permission
   ↓
Market / audience applicability
   ↓
Evidence validation state
   ↓
Claim approval state
   ↓
Effective / expiry dates
   ↓
Supersession / withdrawal
   ↓
Answer-generation mode
```

> **Policy decides what the model is allowed to know and say; the model does not decide policy.**

---

## 13. Runtime query flow

```text
QUESTION
   ↓
Resolve user / role / purpose / market
   ↓
Semantic normalization
   ↓
Governed lexical + vector + graph retrieval
   ↓
Evidence-state evaluation
   ↓
Policy decision
   ├── ABSTAIN
   ├── BLOCK
   ├── EVIDENCE_ONLY
   └── ANSWER
          ↓
     assemble permitted evidence package
          ↓
     constrained generation
          ↓
     post-generation evidence verification
          ↓
     traceable governed response
```

Generation should operate only over the permitted evidence package and should not invent unsupported medical conclusions.

---

## 14. Operating model

| Capability | Primary owner | Responsibility |
|---|---|---|
| Source ingestion | Data / Platform | Connect, ingest, classify, preserve lineage |
| Semantic master | Semantic Steward / Data Governance | Canonical concepts, aliases, relationships |
| Evidence extraction | AI Platform | Structured machine extraction |
| Scientific validation | Medical / Scientific SME | Verify, correct, reject evidence |
| Claim composition | Medical Content / Medical Affairs | Create candidate from validated evidence |
| Claim validation | Independent Medical reviewer | Confirm scientific fidelity of wording |
| MLR approval | Medical / Legal / Regulatory | Approve scope, wording, market, validity |
| Consumption policy | Compliance / Governance | Define access/use policies |
| Platform operations | Engineering / MLOps | Reliability, security, observability |
| Audit oversight | Quality / Compliance | Review lineage and decision history |

Separation of duties must be explicit. The same actor should not be able to create and independently approve the same governed object where independence is required.

---

## 15. Business value and KPIs

### Value pillars

**Productivity**

Reduce search, interpretation, review, and evidence reconstruction effort.

**Knowledge leverage**

Convert one-time SME validation into reusable enterprise scientific knowledge.

**Risk and control**

Prevent use of unvalidated, expired, wrong-market, or unapproved content.

### North-star KPI

> **Time from scientific question to trusted, traceable answer.**

### Supporting measures

- evidence discovery time;
- SME minutes per evidence object;
- extraction correction rate;
- percentage of questions answered from previously validated evidence;
- evidence-to-claim cycle time;
- Medical review turnaround time;
- first-pass Medical review rate;
- MLR cycle time;
- first-pass MLR approval rate;
- citation coverage;
- governed-answer rate;
- policy block rate;
- approval-expiry violations prevented;
- time required to reconstruct lineage for audit.

ROI claims in portfolio material should be presented as transparent scenario assumptions unless measured with real users.

---

## 16. Build-vs-buy strategy

### Buy / reuse commodity infrastructure

- cloud infrastructure;
- object storage;
- relational database;
- vector database;
- graph database;
- identity provider;
- LLM;
- embedding/reranking models;
- OCR / parsing libraries;
- observability stack.

### Integrate enterprise systems

- document management;
- Veeva or enterprise MLR systems where applicable;
- SharePoint;
- publication and trial repositories;
- regulatory repositories;
- identity and access management.

### Build differentiated IP

- Semantic Master and governed semantic versioning;
- pharma-specific entity resolution;
- Evidence Intelligence;
- field-level Evidence Review;
- evidence validation state;
- evidence-to-claim lineage;
- claim composition;
- independent claim governance;
- policy-aware retrieval;
- impact analysis and revalidation.

Principle:

> **The platform should be model-agnostic and database-agnostic, but context- and governance-specific.**

The durable asset is the accumulated network of entities, relationships, validated evidence, human decisions, claims, approvals, policies, and lineage.

---

## 17. Product roadmap

### Phase 1 — Evidence Trust

- semantic retrieval;
- Semantic Master;
- Evidence Intelligence;
- field-level SME validation;
- deterministic claim composition;
- independent Medical validation;
- audit trail.

### Phase 2 — Permitted Use

- MLR lifecycle;
- approved wording;
- market / purpose / audience;
- effective and expiry dates;
- claim activation;
- governed promotional retrieval;
- block reasons.

### Phase 3 — Knowledge Lifecycle

- evidence and claim versioning;
- supersession;
- source withdrawal;
- impact analysis;
- revalidation queues;
- dependency propagation.

### Phase 4 — AI Scale

- LLM-assisted extraction behind validation controls;
- cross-study synthesis;
- contradiction detection;
- evidence-gap analysis;
- literature surveillance;
- agentic workflow automation.

### Explicitly deferred

Do not expand prematurely into:

- every therapeutic area;
- full pharma ontology;
- patient-level clinical decision support;
- autonomous regulatory submissions;
- unrestricted promotional generation;
- custom foundation models;
- dozens of connectors;
- complex multi-agent orchestration before the trust layer is mature.

---

## 18. Current prototype implementation status

### Implemented

- tenant-isolated ingestion;
- provenance-preserving document chunks;
- semantic master and alias normalization;
- typed semantic relationships;
- governed semantic change workflow;
- hybrid lexical + semantic/graph retrieval;
- policy-aware evidence discovery;
- Evidence Intelligence;
- field-level SME evidence review;
- immutable `SME_VALIDATED_EVIDENCE` snapshot;
- deterministic evidence-bound claim composition;
- independent Medical claim review;
- `NOT_MLR_REVIEWED` Medical-validated claim state;
- hash-linked audit;
- Gradio workspaces for evidence discovery, evidence review, and claim composition.

### Current intentional boundary

Medical-validated composed claims are **not** eligible for promotional retrieval.

Promotional use must remain blocked until an applicable MLR approval exists.

### Next major implementation

```text
NOT_MLR_REVIEWED
      ↓
MLR review
      ↓
APPROVED / APPROVED_WITH_CHANGES / REJECTED
      ↓
Market + purpose + audience + validity + conditions
      ↓
ACTIVE_FOR_GOVERNED_USE
      ↓
Eligible governed promotional retrieval
```

---

## 19. Product principles

1. **Do not equate retrieval with truth.**
2. **Do not equate machine extraction with scientific validation.**
3. **Do not equate scientific validity with permitted promotional use.**
4. **Preserve exact source and decision lineage.**
5. **Keep policy outside the LLM.**
6. **Use humans as explicit authorities for governed state transitions.**
7. **Prefer abstention or blocking over unsupported generation.**
8. **Treat approved wording as a governed asset.**
9. **Keep infrastructure replaceable; keep context and governance durable.**
10. **Build one complete evidence-to-use workflow before expanding breadth.**

---

## 20. Executive summary

The platform's value does not come from having a knowledge graph, vector database, or LLM individually.

Its value comes from connecting:

```text
Source
 + Semantic meaning
 + Structured evidence
 + Human validation
 + Claims
 + Approval scope
 + Policy
 + Audit lineage
```

and using that governed context to control downstream AI behavior.

> **Sources provide facts → semantics provide meaning → Evidence Intelligence provides scientific structure → governance provides trust → policy provides permitted use → AI provides the experience.**
