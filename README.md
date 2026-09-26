# Regulated Life Sciences GraphRAG Platform

A governed hybrid GraphRAG platform for regulated life-sciences workflows, demonstrated with public BRUKINSA/zanubrutinib and ALPINE evidence.

> **Product north star:** This is not positioned as a generic pharma chatbot. The target is a governed scientific context layer that converts fragmented evidence into validated knowledge, traceable claims, and policy-controlled AI answers. See [docs/product_north_star.md](docs/product_north_star.md).

## Platform flow

```text
Source documents
    → document ingestion and chunking
    → entity and claim extraction
    → semantic vectors and knowledge graph
    → hybrid vector + lexical + multi-hop graph retrieval
    → role + purpose + market + audience + approval/validity policy evaluation
    → governed answer | evidence discovery | blocked request | abstention
    → source citations and graph lineage
    → SME evidence validation
    → evidence-bound claim composition
    → independent Medical claim review
    → MLR approval and governed-use activation
    → hash-linked audit trail and governance testing
```

## Capabilities

- Canonical pharma semantic layer with versioned master data, alias normalization and typed relationships
- Governed semantic change requests with independent approval, version supersession and audit lineage
- Hybrid lexical retrieval and graph traversal with one-hop semantic expansion
- Multi-hop GraphRAG with source, document, page, and chunk lineage
- Evidence Intelligence for Study → Population → Intervention → Comparator → Endpoint → Outcome → Safety
- Field-level SME evidence verification, correction and rejection
- Deterministic evidence-bound claim composition
- Independent Medical claim review
- Composed-claim MLR submission and approval activation
- Role-, purpose-, market-, audience-, approval-, and validity-aware runtime controls
- Exact approved-wording enforcement for promotional retrieval
- Governed answers, evidence discovery, policy blocks, and abstention
- Hash-linked audit records
- SQLite, GraphML, CSV, and packaged runtime artifacts

## Validation

| Layer | Result |
|---|---:|
| Core governance | CI regression suite |
| Semantic smoke test | Passing on feature branches |
| Evidence review | Field-level validation covered |
| Claim composition | Evidence-bound + separation-of-duties covered |
| MLR activation | Scope + validity + exact wording covered |
| Audience policy | HCP-vs-patient scope covered |

## Repository structure

- `app.py` — original governed GraphRAG Gradio interface
- `smoke_test.py` — deterministic role, purpose and market smoke tests
- `product_api/` — multi-tenant ingestion, semantic normalization, governed retrieval, evidence/claim governance, and audit API
- `product_api/semantic/` — canonical pharma concepts, aliases and typed relationships
- `product_api/mlr_activation.py` — MLR submission, independent review, activation and validity lifecycle for evidence-bound composed claims
- `product_api/audience_query.py` — audience-aware contextual governed query contract
- `docs/product_north_star.md` — business issue, root causes, product thesis, personas, governance, architecture, policy model, KPIs, roadmap, and implementation status
- `docs/semantic_layer.md` — Semantic Layer v1 architecture and extension strategy
- `docs/evidence_intelligence.md` — deterministic scientific evidence structuring
- `docs/evidence_review.md` — field-level SME evidence validation
- `docs/governed_claim_composition.md` — evidence-bound claim composition and independent Medical review
- `docs/mlr_activation.md` — composed-claim MLR and governed-use activation model
- `docs/audience_policy.md` — market/purpose/audience runtime policy and fail-closed behavior
- `product_ui.py` — original unified product workspace
- `product_journey_ui.py` — recommended end-to-end evidence → SME → Medical → MLR → governed-use demonstration workspace
- `platform_artifacts/` — database, graph, retrieval index, policies, audit logs, test results, and source documents
- `release/` — complete downloadable ZIP archive
- `requirements.txt` — Python dependencies for rebuilding the prototype

## Run locally or in Kaggle

```bash
pip install -r requirements.txt
python smoke_test.py
GRADIO_SHARE=true python app.py
```

Run the product API:

```bash
uvicorn product_api.app:app --reload --port 8000
```

The interactive API documentation is available at `http://127.0.0.1:8000/docs`.

### Recommended end-to-end product demo

```bash
export PLATFORM_ADMIN_KEY="configure-in-your-secret-manager"
GRADIO_SHARE=true python product_journey_ui.py
```

This workspace demonstrates the North Star lifecycle in one place: evidence ingestion → SME validation → claim composition → independent Medical validation → MLR → audience-aware governed consumption.

The original product workspace remains available with:

```bash
GRADIO_SHARE=true python product_ui.py
```

### Audience-aware governed query

Use `POST /v1/query/contextual` when market/purpose/audience policy must be explicit. The legacy `/v1/query` endpoint is retained for backward compatibility and fails closed for audience-specific promotional approvals when audience is not provided.

## Important notice

This is a technical prototype, not a validated production GxP system. It must not be used for patient care, clinical decisions, regulatory submissions, or promotional approval. SME and MLR decisions must be made only by genuinely authorized reviewers.

## Author

Om Prakash
