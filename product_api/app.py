from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PRODUCT_DATA_DIR", BASE_DIR / "product_data"))
DB_PATH = Path(os.getenv("PRODUCT_DB_PATH", DATA_DIR / "product.db"))
OBJECT_DIR = DATA_DIR / "objects"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".json"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OBJECT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize_database() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                file_name TEXT NOT NULL,
                content_type TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                market TEXT NOT NULL,
                data_class TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                status TEXT NOT NULL,
                object_path TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, sha256)
            );
            CREATE TABLE IF NOT EXISTS ingestion_jobs (
                job_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS document_chunks (
                chunk_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                chunk_sequence INTEGER NOT NULL,
                page_number INTEGER,
                chunk_text TEXT NOT NULL,
                character_count INTEGER NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, document_id, chunk_sequence)
            );
            CREATE TABLE IF NOT EXISTS document_findings (
                finding_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                finding_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                finding_value TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS graph_nodes (
                node_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, node_type, label)
            );
            CREATE TABLE IF NOT EXISTS graph_edges (
                edge_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                source_node_id TEXT NOT NULL REFERENCES graph_nodes(node_id),
                target_node_id TEXT NOT NULL REFERENCES graph_nodes(node_id),
                relationship_type TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, source_node_id, target_node_id, relationship_type)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id TEXT UNIQUE NOT NULL,
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                record_hash TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_documents_tenant
                ON documents(tenant_id, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_jobs_tenant
                ON ingestion_jobs(tenant_id, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_chunks_tenant_document
                ON document_chunks(tenant_id, document_id, chunk_sequence);
            CREATE INDEX IF NOT EXISTS idx_graph_nodes_tenant
                ON graph_nodes(tenant_id, node_type, label);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_tenant
                ON graph_edges(tenant_id, source_node_id, target_node_id);
            CREATE INDEX IF NOT EXISTS idx_audit_tenant
                ON audit_events(tenant_id, sequence_number);
            """
        )


class TenantContext(BaseModel):
    tenant_id: str
    actor_id: str
    role: str


def tenant_context(
    tenant_id: Annotated[str, Header(alias="X-Tenant-ID")],
    actor_id: Annotated[str, Header(alias="X-Actor-ID")],
    role: Annotated[str, Header(alias="X-Role")],
) -> TenantContext:
    if not tenant_id.strip() or not actor_id.strip() or not role.strip():
        raise HTTPException(401, "Tenant, actor and role headers are required")
    return TenantContext(tenant_id=tenant_id, actor_id=actor_id, role=role)


def append_audit(
    conn: sqlite3.Connection,
    context: TenantContext,
    event_type: str,
    resource_id: str,
    payload: dict,
) -> str:
    previous = conn.execute(
        "SELECT record_hash FROM audit_events WHERE tenant_id=? ORDER BY sequence_number DESC LIMIT 1",
        (context.tenant_id,),
    ).fetchone()
    previous_hash = previous["record_hash"] if previous else "GENESIS"
    audit_id = f"AUD_{uuid.uuid4().hex[:12].upper()}"
    created_at = utc_now()
    canonical = json.dumps(
        {
            "audit_id": audit_id,
            "tenant_id": context.tenant_id,
            "actor_id": context.actor_id,
            "event_type": event_type,
            "resource_id": resource_id,
            "payload": payload,
            "previous_hash": previous_hash,
            "created_at_utc": created_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    record_hash = hashlib.sha256(canonical.encode()).hexdigest()
    conn.execute(
        """INSERT INTO audit_events
        (audit_id, tenant_id, actor_id, event_type, resource_id, payload_json,
         previous_hash, record_hash, created_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            audit_id,
            context.tenant_id,
            context.actor_id,
            event_type,
            resource_id,
            json.dumps(payload, sort_keys=True),
            previous_hash,
            record_hash,
            created_at,
        ),
    )
    return audit_id


class TenantCreate(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,64}$")
    name: str = Field(min_length=2, max_length=120)


app = FastAPI(
    title="Regulated Life Sciences GraphRAG API",
    version="0.1.0",
    description="Tenant-isolated ingestion and governance foundation.",
)


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "governed-graphrag-api"}


@app.post("/v1/tenants", status_code=201)
def create_tenant(request: TenantCreate) -> dict:
    with connection() as conn:
        try:
            conn.execute(
                "INSERT INTO tenants VALUES (?, ?, ?)",
                (request.tenant_id, request.name, utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Tenant already exists") from exc
    return request.model_dump()


@app.post("/v1/documents", status_code=202)
async def upload_document(
    context: Annotated[TenantContext, Depends(tenant_context)],
    file: Annotated[UploadFile, File()],
    market: Annotated[str, Form()] = "Global",
    data_class: Annotated[str, Form()] = "UNCLASSIFIED",
    sensitivity: Annotated[str, Form()] = "CONTROLLED",
) -> dict:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported file type: {extension or 'unknown'}")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(400, "Empty documents are not accepted")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Document exceeds upload limit")

    digest = hashlib.sha256(content).hexdigest()
    document_id = f"DOC_{uuid.uuid4().hex[:12].upper()}"
    job_id = f"JOB_{uuid.uuid4().hex[:12].upper()}"
    tenant_object_dir = OBJECT_DIR / context.tenant_id
    tenant_object_dir.mkdir(parents=True, exist_ok=True)
    object_path = tenant_object_dir / f"{document_id}{extension}"

    with connection() as conn:
        tenant = conn.execute(
            "SELECT tenant_id FROM tenants WHERE tenant_id=?", (context.tenant_id,)
        ).fetchone()
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        existing = conn.execute(
            "SELECT document_id FROM documents WHERE tenant_id=? AND sha256=?",
            (context.tenant_id, digest),
        ).fetchone()
        if existing:
            raise HTTPException(409, f"Duplicate document: {existing['document_id']}")

        object_path.write_bytes(content)
        created_at = utc_now()
        conn.execute(
            """INSERT INTO documents VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                document_id,
                context.tenant_id,
                file.filename,
                file.content_type or "application/octet-stream",
                digest,
                len(content),
                market,
                data_class,
                sensitivity,
                "QUARANTINED",
                str(object_path),
                created_at,
            ),
        )
        conn.execute(
            "INSERT INTO ingestion_jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, context.tenant_id, document_id, "QUEUED", "SECURITY_SCAN", created_at, created_at),
        )
        audit_id = append_audit(
            conn,
            context,
            "DOCUMENT_UPLOADED",
            document_id,
            {"job_id": job_id, "sha256": digest, "market": market, "status": "QUARANTINED"},
        )
    return {
        "document_id": document_id,
        "job_id": job_id,
        "status": "QUEUED",
        "stage": "SECURITY_SCAN",
        "audit_id": audit_id,
    }


@app.get("/v1/documents")
def list_documents(
    context: Annotated[TenantContext, Depends(tenant_context)],
) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """SELECT document_id, file_name, market, data_class, sensitivity,
                      status, sha256, byte_size, created_at_utc
               FROM documents WHERE tenant_id=? ORDER BY created_at_utc DESC""",
            (context.tenant_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/v1/ingestion-jobs/{job_id}")
def get_job(
    job_id: str,
    context: Annotated[TenantContext, Depends(tenant_context)],
) -> dict:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM ingestion_jobs WHERE tenant_id=? AND job_id=?",
            (context.tenant_id, job_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Ingestion job not found")
    return dict(row)


@app.get("/v1/audit/verify")
def verify_audit_chain(
    context: Annotated[TenantContext, Depends(tenant_context)],
) -> dict:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE tenant_id=? ORDER BY sequence_number",
            (context.tenant_id,),
        ).fetchall()
    expected_previous = "GENESIS"
    invalid: list[str] = []
    for row in rows:
        canonical = json.dumps(
            {
                "audit_id": row["audit_id"],
                "tenant_id": row["tenant_id"],
                "actor_id": row["actor_id"],
                "event_type": row["event_type"],
                "resource_id": row["resource_id"],
                "payload": json.loads(row["payload_json"]),
                "previous_hash": row["previous_hash"],
                "created_at_utc": row["created_at_utc"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        calculated = hashlib.sha256(canonical.encode()).hexdigest()
        if row["previous_hash"] != expected_previous or row["record_hash"] != calculated:
            invalid.append(row["audit_id"])
        expected_previous = row["record_hash"]
    return {"valid": not invalid, "records": len(rows), "invalid_audit_ids": invalid}
