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

