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

## Authenticated query API

Configure an administrator provisioning secret before starting the API:

```bash
export PLATFORM_ADMIN_KEY="replace-with-a-secret-from-your-secret-manager"
uvicorn product_api.app:app --port 8000
```

Provision `/v1/auth/api-keys` with `X-Platform-Admin-Key`, then call `/v1/query`
with the returned key as `Authorization: Bearer <key>`. The key is stored only as
a SHA-256 hash, its tenant and role cannot be overridden by the query body, and
each query decision is written to the hash-linked audit chain.

## SME validation

Authorized `ROLE_MEDICAL`, `ROLE_CLINICAL`, and `ROLE_REGULATORY` principals can
review pending candidates through `/v1/sme/candidates`. Validation requires an
explicit authorization confirmation and a substantive rationale. A validated
candidate becomes a versioned governed claim linked to its source chunk, while
its approval state remains `NOT_MLR_REVIEWED`. SME validation never implies
promotional approval.

## MLR approval

Every SME-validated claim enters `/v1/mlr/reviews`. Only authorized MLR,
Regulatory, or Legal roles can decide a review. Approval requires explicit
authorization confirmation, rationale, effective and expiry timestamps, and
recorded conditions of use. `APPROVED_WITH_CHANGES` also requires the approved
wording. Only a current `MLR_APPROVED` claim can support promotional use.
