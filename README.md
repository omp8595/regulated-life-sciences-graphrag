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

- Hybrid semantic, lexical and graph retrieval
- Multi-hop GraphRAG with source, document, page and chunk lineage
- Role-, purpose-, market- and data-class-aware access controls
- Governed answers, evidence discovery, policy blocks and abstention
- SME candidate review and controlled MLR workflow artifacts
- Hash-linked audit records
- SQLite, GraphML, CSV and packaged runtime artifacts

## Validation

| Layer | Result |
|---|---:|
| Core governance | 21/21 |
| GraphRAG governance | 14/14 |
| Indexed evidence chunks | 221 |
| Graph nodes | 244 |
| Graph edges | 521 |

## Repository structure

- `platform_artifacts/` — database, graph, retrieval index, policies, audit logs, test results and source documents
- `release/` — complete downloadable ZIP archive
- `requirements.txt` — Python dependencies for rebuilding the prototype

## Important notice

This is a technical prototype, not a validated production GxP system. It must not be used for patient care, clinical decisions, regulatory submissions or promotional approval. SME and MLR decisions must be made only by genuinely authorized reviewers.

## Author

Om Prakash
