# Product API — Sprint 1

This service introduces tenant-isolated document ingestion and hash-linked audit events.

## Run

```bash
pip install -r requirements.txt
uvicorn product_api.app:app --reload --port 8000
```

Open `http://127.0.0.1:8000/docs` for the API interface.

Required headers on governed endpoints:

- `X-Tenant-ID`
- `X-Actor-ID`
- `X-Role`

Uploaded documents enter `QUARANTINED` status and an ingestion job begins at
`SECURITY_SCAN`. Uploading does not make a document retrievable or governed.

## Run one queued ingestion job

```python
from product_api.worker import process_next_job
print(process_next_job())
```

The worker validates the file, extracts text, detects sensitive data and prompt
injection, creates tenant-scoped chunks, and finishes at `READY_FOR_SME_REVIEW`.
It never creates an SME-validated or MLR-approved claim automatically.

## Tenant-scoped hybrid retrieval

```python
from product_api.retrieval import hybrid_search

result = hybrid_search(
    question="How did zanubrutinib compare with ibrutinib in ALPINE?",
    tenant_id="demo_pharma",
    role="ROLE_MEDICAL",
    purpose="MEDICAL_RESPONSE",
    market="Global",
)
```

Retrieval filters tenant, market, document state, role and purpose before
returning evidence. Results remain `EVIDENCE_ONLY` until a genuine SME review
creates a governed claim.
