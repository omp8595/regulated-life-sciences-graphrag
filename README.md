# Regulated Life Sciences GraphRAG Platform

A governed hybrid GraphRAG platform for regulated life-sciences workflows, demonstrated with public BRUKINSA/zanubrutinib and ALPINE evidence.

## Platform flow

```text
Source documents
    → document ingestion and chunking
    → entity and claim extraction
    → semantic vectors and knowledge graph
    → hybrid vector + lexical + multi-hop graph retrieval
    → role, purpose, market and data-class policy evaluation
    → governed answer | evidence discovery | blocked request | abstention
    → source citations and graph lineage
    → SME validation and MLR review
    → hash-linked audit trail and governance testing
```

## Capabilities

- Hybrid lexical retrieval and graph traversal
- Multi-hop GraphRAG with source, document, page, and chunk lineage
- Role-, purpose-, market-, and data-class-aware access controls
- Governed answers, evidence discovery, policy blocks, and abstention
- SME candidate review and MLR workflow artifacts
- Hash-linked audit records
- SQLite, GraphML, CSV, and packaged runtime artifacts

## Validation

| Layer | Result |
|---|---:|
| Core governance | 21/21 |
| GraphRAG governance | 14/14 |
| Indexed evidence chunks | 221 |
| Graph nodes | 244 |
| Graph edges | 521 |

## Repository structure

- `app.py` — runnable governed GraphRAG Gradio interface
- `smoke_test.py` — deterministic role, purpose and market smoke tests
- `product_api/` — multi-tenant ingestion and audit API foundation
- `product_ui.py` — unified ingestion, query, SME, MLR and governance workspace
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

Run the unified product interface:

```bash
export PLATFORM_ADMIN_KEY="configure-in-your-secret-manager"
GRADIO_SHARE=true python product_ui.py
```

The smoke suite verifies governed US dosage, promotional blocking, medical evidence discovery and India-market abstention.

## Important notice

This is a technical prototype, not a validated production GxP system. It must not be used for patient care, clinical decisions, regulatory submissions, or promotional approval. SME and MLR decisions must be made only by genuinely authorized reviewers.

## Author

Om Prakash
