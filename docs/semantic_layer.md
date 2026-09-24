# Pharma Semantic Layer v1

## Objective

Turn the regulated GraphRAG prototype from keyword-aware retrieval into a
canonical pharma knowledge layer that can normalize different surface forms,
represent controlled domain relationships, and improve retrieval without
weakening the existing governance model.

The semantic layer is deliberately separate from SME validation and MLR
approval. It can normalize what an entity *is* and how curated concepts relate;
it cannot decide that a scientific statement is true, approved, or promotional.

## v3 semantic governance

Changes to the platform semantic master now follow a governed lifecycle rather
than direct database edits.

```text
Authorized contributor
  -> semantic change request
  -> PENDING
  -> independent Regulatory / Semantic Steward review
  -> APPROVED | REJECTED
  -> transactional master-data application
  -> previous concept version SUPERSEDED
  -> new concept version ACTIVE
  -> hash-linked audit event
```

Supported governed changes in v3:

- `ADD_ALIAS`
- `ADD_EXTERNAL_MAPPING`
- `UPDATE_CONCEPT`

Controls:

- Contributors require Medical, Regulatory, or Semantic Steward authorization.
- Approval requires Regulatory or Semantic Steward authorization.
- A proposer cannot approve their own change.
- Approval requires explicit authorization confirmation and reviewer rationale.
- Approved changes create a new semantic concept version.
- Existing aliases, external mappings, and typed relationships are carried
  forward to the new version.
- The previous concept version and its version-specific semantic records are
  retained as `SUPERSEDED`.
- Application is transactional: a failed semantic update does not leave a
  partially superseded master.
- Proposal and decision events are recorded in the existing tenant audit chain.

API endpoints:

- `POST /v1/semantic/change-requests`
- `GET /v1/semantic/change-requests`
- `POST /v1/semantic/change-requests/{change_request_id}/decisions`

The semantic master remains platform-level shared metadata. Governance requests
are tenant-attributed for accountability and audit lineage; approved platform
master changes become visible to governed retrieval across tenants.

## v2 semantic master

Semantic concepts are now persisted as platform master data rather than used directly from Python constants. The bootstrap registry seeds the database idempotently, after which ingestion and retrieval read the active semantic master.

The master uses four versioned tables:

- `semantic_concepts` — stable concept ID + version, type, canonical name and lifecycle status.
- `semantic_aliases` — normalized aliases with source and confidence, tied to a specific concept version.
- `semantic_relationships` — typed relationships between specific concept versions.
- `semantic_external_mappings` — links canonical concepts to authoritative external identifiers.

The initial external mappings include ALPINE → ClinicalTrials.gov `NCT03734016`, zanubrutinib → RxNorm `2262435`, and CLL → MeSH `D015451`.

An authenticated `GET /v1/semantic/concepts` endpoint exposes the current active catalog for inspection without granting write access.

## v1 architecture

```text
Source document
  -> chunking
  -> curated mention resolution
  -> canonical concept IDs
  -> typed semantic edges
  -> tenant-scoped knowledge graph
  -> lexical + semantic retrieval
  -> policy evaluation
  -> evidence only | governed answer | block | abstain
```

Implementation:

- `product_api/semantic/registry.py` owns canonical concepts, aliases and
  curated relationships.
- `product_api/worker.py` resolves mentions during ingestion and indexes
  canonical concept nodes plus typed edges.
- `product_api/retrieval.py` combines lexical similarity with direct concept
  overlap and one-hop relationship expansion.
- Governance, tenant isolation, SME validation and MLR approval remain
  authoritative after semantic retrieval.

## Canonical concepts in the initial slice

| Canonical ID | Type | Canonical name | Example aliases |
|---|---|---|---|
| `DRUG:ZANUBRUTINIB` | Drug | zanubrutinib | zanubrutinib |
| `BRAND:BRUKINSA` | Brand | BRUKINSA | brukinsa |
| `DRUG:IBRUTINIB` | Drug | ibrutinib | ibrutinib |
| `TRIAL:ALPINE` | Clinical trial | ALPINE | ALPINE study, ALPINE trial |
| `INDICATION:CLL` | Indication | chronic lymphocytic leukemia | CLL |
| `INDICATION:SLL` | Indication | small lymphocytic lymphoma | SLL |
| `TARGET:BTK` | Molecular target | Bruton's tyrosine kinase | BTK |

The seed set is intentionally narrow. It exists to prove the architecture
against the current BRUKINSA/ALPINE demo before expanding the ontology.

## Typed relationships

The v1 registry includes:

- `BRAND_OF`
- `EVALUATES`
- `COMPARES_WITH`
- `STUDIES_INDICATION`
- `TARGETS`

These are controlled semantic relationships, not generated conclusions.
Document ingestion creates relationship edges only when both endpoint concepts
are present in the evidence chunk. This keeps semantic enrichment traceable to
the ingested context.

## Retrieval behavior

Semantic matching has two levels:

1. **Direct match** — query and evidence resolve to the same canonical concept.
2. **Related match** — an evidence concept is one curated semantic hop from the
   query concept.

Direct matches receive full semantic weight. One-hop related matches receive a
lower weight. The semantic score is then combined with lexical similarity in
the existing hybrid retrieval path.

Example:

```text
Query:    BRUKINSA
Evidence: zanubrutinib
          |
          +-- BRAND:BRUKINSA --BRAND_OF--> DRUG:ZANUBRUTINIB
```

The evidence can now be discovered semantically even when exact lexical overlap
is absent. Policy checks still determine whether that evidence can be returned.

## Governance boundaries

Semantic resolution must never be treated as:

- proof that a scientific claim is true;
- an approved indication;
- MLR approval;
- promotional eligibility;
- clinical advice;
- a substitute for authoritative terminology or product-master governance.

The semantic layer proposes canonical identity and curated relationships. Human
review and governed claims remain separate lifecycle stages.

## Next expansion

After v1 is stable, extend in this order:

1. Add controlled semantic-master change requests with reviewer approval, supersession and audit events.
2. Add tenant overlay mappings for enterprise-local product, brand and study identifiers without copying the global master.
3. Add endpoint, biomarker, mechanism, population, organization and publication
   concept types.
4. Add document-level provenance to semantic relationship edges.
5. Support multi-hop expansion with explicit hop caps and policy-aware edge
   classes.
6. Add semantic coverage, ambiguity and mapping-quality metrics to governance
   tests.
7. Introduce reviewer workflows for semantic-master changes so production
   mappings are versioned and auditable.
