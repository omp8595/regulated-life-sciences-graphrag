from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PRODUCT_DATA_DIR", BASE_DIR / "product_data"))
DB_PATH = Path(os.getenv("PRODUCT_DB_PATH", DATA_DIR / "product.db"))
OBJECT_DIR = DATA_DIR / "objects"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
PLATFORM_ADMIN_KEY = os.getenv("PLATFORM_ADMIN_KEY", "")
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
            CREATE TABLE IF NOT EXISTS evidence_intelligence (
                structure_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                chunk_id TEXT NOT NULL REFERENCES document_chunks(chunk_id),
                study_json TEXT NOT NULL,
                population_json TEXT NOT NULL,
                intervention_json TEXT NOT NULL,
                comparator_json TEXT NOT NULL,
                endpoint_json TEXT NOT NULL,
                outcome_json TEXT NOT NULL,
                safety_json TEXT NOT NULL,
                semantic_relationships_json TEXT NOT NULL,
                extraction_method TEXT NOT NULL,
                review_status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, chunk_id)
            );
            CREATE TABLE IF NOT EXISTS evidence_intelligence_review_decisions (
                review_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                structure_id TEXT NOT NULL REFERENCES evidence_intelligence(structure_id),
                field_name TEXT NOT NULL,
                decision TEXT NOT NULL,
                original_value_json TEXT NOT NULL,
                reviewed_value_json TEXT,
                rationale TEXT NOT NULL,
                reviewer_actor_id TEXT NOT NULL,
                reviewer_role TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_intelligence_validations (
                validation_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                structure_id TEXT NOT NULL REFERENCES evidence_intelligence(structure_id),
                validated_payload_json TEXT NOT NULL,
                validated_by TEXT NOT NULL,
                validated_role TEXT NOT NULL,
                rationale TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, structure_id)
            );
            CREATE TABLE IF NOT EXISTS composed_claim_candidates (
                candidate_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                structure_id TEXT NOT NULL REFERENCES evidence_intelligence(structure_id),
                validation_id TEXT NOT NULL REFERENCES evidence_intelligence_validations(validation_id),
                claim_kind TEXT NOT NULL,
                claim_text TEXT NOT NULL,
                support_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_role TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS composed_claim_review_decisions (
                decision_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                candidate_id TEXT NOT NULL REFERENCES composed_claim_candidates(candidate_id),
                reviewer_actor_id TEXT NOT NULL,
                reviewer_role TEXT NOT NULL,
                decision TEXT NOT NULL,
                rationale TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS medical_validated_composed_claims (
                claim_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                candidate_id TEXT NOT NULL REFERENCES composed_claim_candidates(candidate_id),
                claim_kind TEXT NOT NULL,
                claim_text TEXT NOT NULL,
                support_json TEXT NOT NULL,
                status TEXT NOT NULL,
                approval_status TEXT NOT NULL,
                validated_by TEXT NOT NULL,
                validated_role TEXT NOT NULL,
                validated_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, candidate_id)
            );
            CREATE TABLE IF NOT EXISTS semantic_concepts (
                concept_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                concept_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                semantic_version TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                PRIMARY KEY (concept_id, version)
            );
            CREATE TABLE IF NOT EXISTS semantic_aliases (
                alias_id TEXT PRIMARY KEY,
                concept_id TEXT NOT NULL,
                concept_version INTEGER NOT NULL,
                alias TEXT NOT NULL,
                normalized_alias TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                FOREIGN KEY (concept_id, concept_version)
                    REFERENCES semantic_concepts(concept_id, version),
                UNIQUE(concept_id, concept_version, normalized_alias)
            );
            CREATE TABLE IF NOT EXISTS semantic_relationships (
                relationship_id TEXT PRIMARY KEY,
                source_concept_id TEXT NOT NULL,
                source_version INTEGER NOT NULL,
                relationship_type TEXT NOT NULL,
                target_concept_id TEXT NOT NULL,
                target_version INTEGER NOT NULL,
                semantic_version TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                FOREIGN KEY (source_concept_id, source_version)
                    REFERENCES semantic_concepts(concept_id, version),
                FOREIGN KEY (target_concept_id, target_version)
                    REFERENCES semantic_concepts(concept_id, version),
                UNIQUE(source_concept_id, source_version, relationship_type, target_concept_id, target_version)
            );
            CREATE TABLE IF NOT EXISTS semantic_external_mappings (
                mapping_id TEXT PRIMARY KEY,
                concept_id TEXT NOT NULL,
                concept_version INTEGER NOT NULL,
                system TEXT NOT NULL,
                identifier TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                FOREIGN KEY (concept_id, concept_version)
                    REFERENCES semantic_concepts(concept_id, version),
                UNIQUE(system, identifier, concept_id, concept_version)
            );
            CREATE TABLE IF NOT EXISTS semantic_change_requests (
                change_request_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                change_type TEXT NOT NULL,
                concept_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                rationale TEXT NOT NULL,
                status TEXT NOT NULL,
                proposed_by TEXT NOT NULL,
                proposed_role TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                decided_by TEXT,
                decided_role TEXT,
                decision_rationale TEXT,
                decided_at_utc TEXT,
                applied_concept_version INTEGER
            );
            CREATE TABLE IF NOT EXISTS semantic_change_decisions (
                decision_id TEXT PRIMARY KEY,
                change_request_id TEXT NOT NULL REFERENCES semantic_change_requests(change_request_id),
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                reviewer_actor_id TEXT NOT NULL,
                reviewer_role TEXT NOT NULL,
                decision TEXT NOT NULL,
                rationale TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
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
            CREATE TABLE IF NOT EXISTS api_principals (
                principal_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                actor_id TEXT NOT NULL,
                role TEXT NOT NULL,
                api_key_hash TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS candidate_claims (
                candidate_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                chunk_id TEXT NOT NULL REFERENCES document_chunks(chunk_id),
                proposed_text TEXT NOT NULL,
                claim_type TEXT NOT NULL,
                market TEXT NOT NULL,
                data_class TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, chunk_id)
            );
            CREATE TABLE IF NOT EXISTS sme_review_decisions (
                decision_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                candidate_id TEXT NOT NULL REFERENCES candidate_claims(candidate_id),
                reviewer_actor_id TEXT NOT NULL,
                reviewer_role TEXT NOT NULL,
                decision TEXT NOT NULL,
                rationale TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS governed_claims (
                claim_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                candidate_id TEXT NOT NULL REFERENCES candidate_claims(candidate_id),
                claim_text TEXT NOT NULL,
                claim_type TEXT NOT NULL,
                market TEXT NOT NULL,
                data_class TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL,
                approval_status TEXT NOT NULL,
                validated_by TEXT NOT NULL,
                validated_at_utc TEXT NOT NULL,
                effective_from_utc TEXT,
                expires_at_utc TEXT,
                conditions_of_use TEXT,
                UNIQUE(tenant_id, candidate_id, version)
            );
            CREATE TABLE IF NOT EXISTS governed_claim_evidence (
                claim_id TEXT NOT NULL REFERENCES governed_claims(claim_id),
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                chunk_id TEXT NOT NULL REFERENCES document_chunks(chunk_id),
                PRIMARY KEY (tenant_id, claim_id, chunk_id)
            );
            CREATE TABLE IF NOT EXISTS mlr_review_queue (
                review_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                claim_id TEXT NOT NULL REFERENCES governed_claims(claim_id),
                market TEXT NOT NULL,
                review_status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(tenant_id, claim_id)
            );
            CREATE TABLE IF NOT EXISTS mlr_review_decisions (
                mlr_decision_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                review_id TEXT NOT NULL REFERENCES mlr_review_queue(review_id),
                claim_id TEXT NOT NULL REFERENCES governed_claims(claim_id),
                reviewer_actor_id TEXT NOT NULL,
                reviewer_role TEXT NOT NULL,
                decision TEXT NOT NULL,
                rationale TEXT NOT NULL,
                approved_wording TEXT,
                conditions_of_use TEXT,
                effective_from_utc TEXT,
                expires_at_utc TEXT,
                created_at_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_documents_tenant
                ON documents(tenant_id, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_jobs_tenant
                ON ingestion_jobs(tenant_id, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_chunks_tenant_document
                ON document_chunks(tenant_id, document_id, chunk_sequence);
            CREATE INDEX IF NOT EXISTS idx_evidence_intelligence_tenant_document
                ON evidence_intelligence(tenant_id, document_id, chunk_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_reviews_structure
                ON evidence_intelligence_review_decisions(tenant_id, structure_id, field_name, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_evidence_validations_structure
                ON evidence_intelligence_validations(tenant_id, structure_id);
            CREATE INDEX IF NOT EXISTS idx_composed_candidates_tenant_status
                ON composed_claim_candidates(tenant_id, status, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_composed_reviews_candidate
                ON composed_claim_review_decisions(tenant_id, candidate_id, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_medical_validated_composed_claims
                ON medical_validated_composed_claims(tenant_id, approval_status, validated_at_utc);
            CREATE INDEX IF NOT EXISTS idx_graph_nodes_tenant
                ON graph_nodes(tenant_id, node_type, label);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_tenant
                ON graph_edges(tenant_id, source_node_id, target_node_id);
            CREATE INDEX IF NOT EXISTS idx_semantic_concepts_active
                ON semantic_concepts(concept_id, status, version);
            CREATE INDEX IF NOT EXISTS idx_semantic_alias_lookup
                ON semantic_aliases(normalized_alias, status);
            CREATE INDEX IF NOT EXISTS idx_semantic_relationships_active
                ON semantic_relationships(source_concept_id, target_concept_id, status);
            CREATE INDEX IF NOT EXISTS idx_semantic_external_mapping
                ON semantic_external_mappings(system, identifier, status);
            CREATE INDEX IF NOT EXISTS idx_semantic_changes_tenant_status
                ON semantic_change_requests(tenant_id, status, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_semantic_decisions_request
                ON semantic_change_decisions(change_request_id, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_audit_tenant
                ON audit_events(tenant_id, sequence_number);
            CREATE INDEX IF NOT EXISTS idx_principals_tenant
                ON api_principals(tenant_id, actor_id, role);
            CREATE INDEX IF NOT EXISTS idx_candidates_tenant_status
                ON candidate_claims(tenant_id, status, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_governed_claims_tenant
                ON governed_claims(tenant_id, status, market);
            CREATE INDEX IF NOT EXISTS idx_mlr_queue_tenant_status
                ON mlr_review_queue(tenant_id, review_status, created_at_utc);
            """
        )
        # Lightweight SQLite migration support for databases created by older prototype versions.
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(governed_claims)")}
        for column in ("effective_from_utc", "expires_at_utc", "conditions_of_use"):
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE governed_claims ADD COLUMN {column} TEXT")

        from product_api.semantic.store import seed_semantic_master
        seed_semantic_master(conn, utc_now())


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


class ApiKeyCreate(BaseModel):
    tenant_id: str
    actor_id: str = Field(min_length=2, max_length=120)
    role: str = Field(pattern=r"^ROLE_[A-Z_]+$")


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    purpose: str = Field(min_length=2, max_length=100)
    market: str = Field(min_length=2, max_length=100)
    top_k: int = Field(default=5, ge=1, le=20)


class SemanticChangeRequest(BaseModel):
    change_type: str = Field(pattern=r"^(ADD_ALIAS|ADD_EXTERNAL_MAPPING|UPDATE_CONCEPT)$")
    concept_id: str = Field(min_length=3, max_length=160)
    payload: dict
    rationale: str = Field(min_length=20, max_length=4000)


class SemanticChangeDecisionRequest(BaseModel):
    decision: str = Field(pattern=r"^(APPROVED|REJECTED)$")
    rationale: str = Field(min_length=20, max_length=4000)
    authorization_confirmed: bool


class EvidenceFieldReviewRequest(BaseModel):
    field_name: str = Field(
        pattern=r"^(study|population|intervention|comparator|endpoint|outcome|safety)$"
    )
    decision: str = Field(pattern=r"^(VERIFIED|CORRECTED|REJECTED)$")
    reviewed_value: Any | None = None
    rationale: str = Field(min_length=20, max_length=4000)


class EvidenceFinalizeRequest(BaseModel):
    rationale: str = Field(min_length=20, max_length=4000)
    authorization_confirmed: bool


class ClaimCompositionRequest(BaseModel):
    structure_id: str = Field(min_length=3, max_length=160)
    claim_kind: str = Field(pattern=r"^(EFFICACY_ENDPOINT|SAFETY)$")


class ComposedClaimDecisionRequest(BaseModel):
    decision: str = Field(pattern=r"^(VALIDATED|REJECTED|NEEDS_REVISION)$")
    rationale: str = Field(min_length=20, max_length=4000)
    authorization_confirmed: bool


class SmeDecisionRequest(BaseModel):
    decision: str = Field(pattern=r"^(VALIDATED|REJECTED|NEEDS_REVISION)$")
    rationale: str = Field(min_length=20, max_length=4000)
    authorization_confirmed: bool


class MlrDecisionRequest(BaseModel):
    decision: str = Field(pattern=r"^(APPROVED|APPROVED_WITH_CHANGES|REJECTED)$")
    rationale: str = Field(min_length=20, max_length=4000)
    authorization_confirmed: bool
    approved_wording: str | None = Field(default=None, max_length=8000)
    conditions_of_use: str | None = Field(default=None, max_length=2000)
    effective_from_utc: datetime | None = None
    expires_at_utc: datetime | None = None


bearer_scheme = HTTPBearer(auto_error=False)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def authenticated_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> TenantContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Bearer authentication is required")
    with connection() as conn:
        row = conn.execute(
            """SELECT tenant_id, actor_id, role FROM api_principals
               WHERE api_key_hash=? AND status='ACTIVE'""",
            (hash_api_key(credentials.credentials),),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid or inactive API key")
    return TenantContext(**dict(row))


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


@app.post("/v1/auth/api-keys", status_code=201)
def create_api_key(
    request: ApiKeyCreate,
    platform_admin_key: Annotated[str | None, Header(alias="X-Platform-Admin-Key")] = None,
) -> dict:
    if not PLATFORM_ADMIN_KEY:
        raise HTTPException(503, "API-key provisioning is disabled until PLATFORM_ADMIN_KEY is configured")
    if not platform_admin_key or not hmac.compare_digest(platform_admin_key, PLATFORM_ADMIN_KEY):
        raise HTTPException(403, "Valid platform administrator authorization is required")
    raw_key = f"rgp_{uuid.uuid4().hex}{uuid.uuid4().hex}"
    principal_id = f"PRN_{uuid.uuid4().hex[:12].upper()}"
    with connection() as conn:
        tenant = conn.execute("SELECT tenant_id FROM tenants WHERE tenant_id=?", (request.tenant_id,)).fetchone()
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        conn.execute(
            "INSERT INTO api_principals VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?)",
            (principal_id, request.tenant_id, request.actor_id, request.role, hash_api_key(raw_key), utc_now()),
        )
    return {
        "principal_id": principal_id,
        "tenant_id": request.tenant_id,
        "actor_id": request.actor_id,
        "role": request.role,
        "api_key": raw_key,
        "warning": "Store this key securely; it will not be shown again.",
    }


@app.get("/v1/semantic/concepts")
def semantic_concepts(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> list[dict]:
    from product_api.semantic.store import list_semantic_concepts

    with connection() as conn:
        concepts = list_semantic_concepts(conn)
    return concepts


@app.post("/v1/semantic/change-requests", status_code=201)
def propose_semantic_change(
    request: SemanticChangeRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    from product_api.semantic.store import validate_semantic_change

    if principal.role not in {"ROLE_MEDICAL", "ROLE_REGULATORY", "ROLE_SEMANTIC_STEWARD"}:
        raise HTTPException(403, "An authorized semantic contributor role is required")

    change_request_id = f"SEMCR_{uuid.uuid4().hex[:12].upper()}"
    with connection() as conn:
        try:
            validate_semantic_change(conn, request.change_type, request.concept_id, request.payload)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

        conn.execute(
            """INSERT INTO semantic_change_requests
               (change_request_id, tenant_id, change_type, concept_id, payload_json,
                rationale, status, proposed_by, proposed_role, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)""",
            (
                change_request_id,
                principal.tenant_id,
                request.change_type,
                request.concept_id,
                json.dumps(request.payload, sort_keys=True),
                request.rationale,
                principal.actor_id,
                principal.role,
                utc_now(),
            ),
        )
        audit_id = append_audit(
            conn,
            principal,
            "SEMANTIC_CHANGE_PROPOSED",
            change_request_id,
            {
                "change_type": request.change_type,
                "concept_id": request.concept_id,
            },
        )
    return {
        "change_request_id": change_request_id,
        "status": "PENDING",
        "change_type": request.change_type,
        "concept_id": request.concept_id,
        "audit_id": audit_id,
    }


@app.get("/v1/semantic/change-requests")
def list_semantic_change_requests(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> list[dict]:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_REGULATORY", "ROLE_SEMANTIC_STEWARD"}:
        raise HTTPException(403, "An authorized semantic governance role is required")
    with connection() as conn:
        rows = conn.execute(
            """SELECT change_request_id, change_type, concept_id, payload_json,
                      rationale, status, proposed_by, proposed_role, created_at_utc,
                      decided_by, decided_role, decision_rationale, decided_at_utc,
                      applied_concept_version
               FROM semantic_change_requests
               WHERE tenant_id=?
               ORDER BY created_at_utc DESC""",
            (principal.tenant_id,),
        ).fetchall()
    return [
        {
            **{key: row[key] for key in row.keys() if key != "payload_json"},
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


@app.post("/v1/semantic/change-requests/{change_request_id}/decisions")
def decide_semantic_change(
    change_request_id: str,
    request: SemanticChangeDecisionRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    from product_api.semantic.store import apply_semantic_change

    if principal.role not in {"ROLE_REGULATORY", "ROLE_SEMANTIC_STEWARD"}:
        raise HTTPException(403, "An authorized semantic reviewer role is required")
    if request.decision == "APPROVED" and not request.authorization_confirmed:
        raise HTTPException(403, "Explicit semantic-governance authorization confirmation is required")

    with connection() as conn:
        change = conn.execute(
            """SELECT * FROM semantic_change_requests
               WHERE tenant_id=? AND change_request_id=?""",
            (principal.tenant_id, change_request_id),
        ).fetchone()
        if not change:
            raise HTTPException(404, "Semantic change request not found")
        if change["status"] != "PENDING":
            raise HTTPException(409, "Semantic change request has already been decided")
        if change["proposed_by"] == principal.actor_id:
            raise HTTPException(403, "Semantic changes require independent reviewer approval")

        applied_version = None
        if request.decision == "APPROVED":
            try:
                applied_version = apply_semantic_change(
                    conn,
                    change["change_type"],
                    change["concept_id"],
                    json.loads(change["payload_json"]),
                    utc_now(),
                )
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            except (sqlite3.IntegrityError, TypeError, ValueError) as exc:
                raise HTTPException(422, str(exc)) from exc

        decision_id = f"SEMDEC_{uuid.uuid4().hex[:12].upper()}"
        decided_at = utc_now()
        conn.execute(
            """INSERT INTO semantic_change_decisions
               (decision_id, change_request_id, tenant_id, reviewer_actor_id,
                reviewer_role, decision, rationale, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                change_request_id,
                principal.tenant_id,
                principal.actor_id,
                principal.role,
                request.decision,
                request.rationale,
                decided_at,
            ),
        )
        conn.execute(
            """UPDATE semantic_change_requests
               SET status=?, decided_by=?, decided_role=?, decision_rationale=?,
                   decided_at_utc=?, applied_concept_version=?
               WHERE change_request_id=? AND tenant_id=?""",
            (
                request.decision,
                principal.actor_id,
                principal.role,
                request.rationale,
                decided_at,
                applied_version,
                change_request_id,
                principal.tenant_id,
            ),
        )
        audit_id = append_audit(
            conn,
            principal,
            "SEMANTIC_CHANGE_DECIDED",
            change_request_id,
            {
                "decision": request.decision,
                "decision_id": decision_id,
                "concept_id": change["concept_id"],
                "applied_concept_version": applied_version,
            },
        )

    return {
        "decision_id": decision_id,
        "change_request_id": change_request_id,
        "decision": request.decision,
        "concept_id": change["concept_id"],
        "applied_concept_version": applied_version,
        "audit_id": audit_id,
    }


@app.post("/v1/query")
def governed_query(
    request: QueryRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    from product_api.retrieval import hybrid_search

    result = hybrid_search(
        question=request.question,
        tenant_id=principal.tenant_id,
        role=principal.role,
        purpose=request.purpose,
        market=request.market,
        top_k=request.top_k,
    )
    with connection() as conn:
        audit_id = append_audit(
            conn,
            principal,
            "GOVERNED_QUERY_DECISION",
            f"QRY_{uuid.uuid4().hex[:12].upper()}",
            {
                "question_sha256": hashlib.sha256(request.question.encode()).hexdigest(),
                "purpose": request.purpose,
                "market": request.market,
                "decision_status": result["status"],
                "response_type": result["response_type"],
                "result_count": result["result_count"],
            },
        )
    result.update(
        {"audit_id": audit_id, "actor_id": principal.actor_id, "role": principal.role, "purpose": request.purpose}
    )
    return result


@app.get("/v1/evidence/intelligence")
def evidence_intelligence_catalog(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
    document_id: str | None = None,
) -> list[dict]:
    if principal.role not in {
        "ROLE_MEDICAL",
        "ROLE_CLINICAL",
        "ROLE_REGULATORY",
        "ROLE_MLR_REVIEWER",
    }:
        raise HTTPException(403, "An authorized evidence-review role is required")
    from product_api.evidence_intelligence import list_evidence_intelligence

    with connection() as conn:
        return list_evidence_intelligence(conn, principal.tenant_id, document_id)


@app.get("/v1/evidence/review-queue")
def evidence_review_queue(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> list[dict]:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized SME evidence-review role is required")
    from product_api.evidence_intelligence import list_evidence_intelligence

    with connection() as conn:
        records = list_evidence_intelligence(conn, principal.tenant_id)
    return [
        record for record in records
        if record["review_status"] != "SME_VALIDATED_EVIDENCE"
        and record["review_progress"]["required_fields"]
    ]


@app.get("/v1/evidence/intelligence/{structure_id}/reviews")
def evidence_review_state(
    structure_id: str,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized SME evidence-review role is required")
    from product_api.evidence_intelligence import get_review_state

    with connection() as conn:
        try:
            return get_review_state(conn, principal.tenant_id, structure_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.post("/v1/evidence/intelligence/{structure_id}/reviews", status_code=201)
def review_evidence_field(
    structure_id: str,
    request: EvidenceFieldReviewRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized SME evidence-review role is required")
    from product_api.evidence_intelligence import record_field_review

    with connection() as conn:
        try:
            result = record_field_review(
                conn,
                principal.tenant_id,
                structure_id,
                request.field_name,
                request.decision,
                request.reviewed_value,
                request.rationale,
                principal.actor_id,
                principal.role,
                utc_now(),
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit_id = append_audit(
            conn,
            principal,
            "EVIDENCE_FIELD_REVIEWED",
            structure_id,
            {
                "review_id": result["review_id"],
                "field_name": request.field_name,
                "decision": request.decision,
                "remaining_fields": result["remaining_fields"],
            },
        )
    return {**result, "audit_id": audit_id}


@app.post("/v1/evidence/intelligence/{structure_id}/finalize")
def finalize_evidence_validation(
    structure_id: str,
    request: EvidenceFinalizeRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized SME evidence-review role is required")
    if not request.authorization_confirmed:
        raise HTTPException(403, "Explicit SME evidence-validation confirmation is required")
    from product_api.evidence_intelligence import finalize_evidence_review

    with connection() as conn:
        try:
            result = finalize_evidence_review(
                conn,
                principal.tenant_id,
                structure_id,
                principal.actor_id,
                principal.role,
                request.rationale,
                utc_now(),
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        audit_id = append_audit(
            conn,
            principal,
            "EVIDENCE_SME_VALIDATED",
            structure_id,
            {
                "validation_id": result["validation_id"],
                "review_status": result["review_status"],
            },
        )
    return {**result, "audit_id": audit_id}


@app.get("/v1/claims/composition-sources")
def claim_composition_sources(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> list[dict]:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized Medical evidence role is required")
    from product_api.claim_composition import list_composition_sources

    with connection() as conn:
        return list_composition_sources(conn, principal.tenant_id)


@app.post("/v1/claims/compose", status_code=201)
def compose_governed_claim_candidate(
    request: ClaimCompositionRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized Medical evidence role is required")
    from product_api.claim_composition import compose_claim_candidate

    with connection() as conn:
        try:
            result = compose_claim_candidate(
                conn,
                principal.tenant_id,
                request.structure_id,
                request.claim_kind,
                principal.actor_id,
                principal.role,
                utc_now(),
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        audit_id = append_audit(
            conn,
            principal,
            "COMPOSED_CLAIM_CREATED",
            result["candidate_id"],
            {
                "structure_id": request.structure_id,
                "validation_id": result["validation_id"],
                "claim_kind": request.claim_kind,
            },
        )
    return {**result, "audit_id": audit_id}


@app.get("/v1/claims/composed-candidates")
def composed_claim_candidates(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
    status: str | None = None,
) -> list[dict]:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized Medical claim-review role is required")
    from product_api.claim_composition import list_composed_claim_candidates

    with connection() as conn:
        return list_composed_claim_candidates(conn, principal.tenant_id, status)


@app.post("/v1/claims/composed-candidates/{candidate_id}/decisions")
def review_composed_claim(
    candidate_id: str,
    request: ComposedClaimDecisionRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized Medical claim-review role is required")
    if request.decision == "VALIDATED" and not request.authorization_confirmed:
        raise HTTPException(403, "Explicit Medical claim-validation confirmation is required")
    from product_api.claim_composition import review_composed_claim_candidate

    with connection() as conn:
        try:
            result = review_composed_claim_candidate(
                conn,
                principal.tenant_id,
                candidate_id,
                request.decision,
                request.rationale,
                principal.actor_id,
                principal.role,
                utc_now(),
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            message = str(exc)
            status_code = 403 if "independent Medical review" in message else 409
            raise HTTPException(status_code, message) from exc
        audit_id = append_audit(
            conn,
            principal,
            "COMPOSED_CLAIM_REVIEWED",
            candidate_id,
            {
                "decision_id": result["decision_id"],
                "decision": result["decision"],
                "claim_id": result["claim_id"],
                "approval_status": result["approval_status"],
            },
        )
    return {**result, "audit_id": audit_id}


@app.get("/v1/claims/medical-validated")
def medical_validated_composed_claims(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> list[dict]:
    if principal.role not in {
        "ROLE_MEDICAL",
        "ROLE_CLINICAL",
        "ROLE_REGULATORY",
        "ROLE_MLR_REVIEWER",
    }:
        raise HTTPException(403, "An authorized claim-review role is required")
    from product_api.claim_composition import list_medical_validated_composed_claims

    with connection() as conn:
        return list_medical_validated_composed_claims(conn, principal.tenant_id)


@app.get("/v1/sme/candidates")
def list_sme_candidates(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> list[dict]:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized SME role is required")
    with connection() as conn:
        rows = conn.execute(
            """SELECT candidate_id, document_id, chunk_id, proposed_text, claim_type,
                      market, data_class, status, created_at_utc
               FROM candidate_claims WHERE tenant_id=? AND status='PENDING'
               ORDER BY created_at_utc""",
            (principal.tenant_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/v1/sme/candidates/{candidate_id}/decisions")
def record_sme_decision(
    candidate_id: str,
    request: SmeDecisionRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    if principal.role not in {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}:
        raise HTTPException(403, "An authorized SME role is required")
    if request.decision == "VALIDATED" and not request.authorization_confirmed:
        raise HTTPException(403, "Explicit SME authorization confirmation is required")
    with connection() as conn:
        candidate = conn.execute(
            "SELECT * FROM candidate_claims WHERE tenant_id=? AND candidate_id=?",
            (principal.tenant_id, candidate_id),
        ).fetchone()
        if not candidate:
            raise HTTPException(404, "Candidate not found")
        if candidate["status"] != "PENDING":
            raise HTTPException(409, "Candidate has already been reviewed")
        decision_id = f"SME_{uuid.uuid4().hex[:12].upper()}"
        conn.execute(
            "INSERT INTO sme_review_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id, principal.tenant_id, candidate_id, principal.actor_id,
                principal.role, request.decision, request.rationale, utc_now(),
            ),
        )
        claim_id = None
        if request.decision == "VALIDATED":
            claim_id = f"CLM_{uuid.uuid4().hex[:12].upper()}"
            conn.execute(
                """INSERT INTO governed_claims
                   (claim_id, tenant_id, candidate_id, claim_text, claim_type, market,
                    data_class, version, status, approval_status, validated_by, validated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'ACTIVE', 'NOT_MLR_REVIEWED', ?, ?)""",
                (
                    claim_id, principal.tenant_id, candidate_id, candidate["proposed_text"],
                    candidate["claim_type"], candidate["market"], candidate["data_class"],
                    principal.actor_id, utc_now(),
                ),
            )
            conn.execute(
                "INSERT INTO governed_claim_evidence VALUES (?, ?, ?, ?)",
                (claim_id, principal.tenant_id, candidate["document_id"], candidate["chunk_id"]),
            )
            conn.execute(
                "INSERT INTO mlr_review_queue VALUES (?, ?, ?, ?, 'PENDING', ?)",
                (f"MLRQ_{uuid.uuid4().hex[:12].upper()}", principal.tenant_id, claim_id, candidate["market"], utc_now()),
            )
        conn.execute(
            "UPDATE candidate_claims SET status=? WHERE tenant_id=? AND candidate_id=?",
            (request.decision, principal.tenant_id, candidate_id),
        )
        audit_id = append_audit(
            conn, principal, "SME_REVIEW_DECISION", candidate_id,
            {"decision": request.decision, "decision_id": decision_id, "claim_id": claim_id},
        )
    return {
        "decision_id": decision_id,
        "candidate_id": candidate_id,
        "decision": request.decision,
        "claim_id": claim_id,
        "approval_status": "NOT_MLR_REVIEWED" if claim_id else None,
        "audit_id": audit_id,
    }


@app.get("/v1/mlr/reviews")
def list_mlr_reviews(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> list[dict]:
    if principal.role not in {"ROLE_MLR_REVIEWER", "ROLE_REGULATORY", "ROLE_LEGAL"}:
        raise HTTPException(403, "An authorized MLR reviewer role is required")
    with connection() as conn:
        rows = conn.execute(
            """SELECT q.review_id, q.claim_id, q.market, q.review_status,
                      g.claim_text, g.claim_type, g.data_class, g.approval_status
               FROM mlr_review_queue q JOIN governed_claims g
                 ON g.claim_id=q.claim_id AND g.tenant_id=q.tenant_id
               WHERE q.tenant_id=? AND q.review_status='PENDING'
               ORDER BY q.created_at_utc""",
            (principal.tenant_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/v1/mlr/reviews/{review_id}/decisions")
def record_mlr_decision(
    review_id: str,
    request: MlrDecisionRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    if principal.role not in {"ROLE_MLR_REVIEWER", "ROLE_REGULATORY", "ROLE_LEGAL"}:
        raise HTTPException(403, "An authorized MLR reviewer role is required")
    if request.decision in {"APPROVED", "APPROVED_WITH_CHANGES"} and not request.authorization_confirmed:
        raise HTTPException(403, "Explicit MLR authorization confirmation is required")
    if request.decision == "APPROVED_WITH_CHANGES" and not (request.approved_wording or "").strip():
        raise HTTPException(422, "Approved wording is required for APPROVED_WITH_CHANGES")
    if request.decision in {"APPROVED", "APPROVED_WITH_CHANGES"}:
        if request.effective_from_utc is None or request.expires_at_utc is None:
            raise HTTPException(422, "Effective and expiry timestamps are required for approval")
        if request.expires_at_utc <= request.effective_from_utc:
            raise HTTPException(422, "Expiry must be later than the effective timestamp")
    with connection() as conn:
        review = conn.execute(
            "SELECT * FROM mlr_review_queue WHERE tenant_id=? AND review_id=?",
            (principal.tenant_id, review_id),
        ).fetchone()
        if not review:
            raise HTTPException(404, "MLR review not found")
        if review["review_status"] != "PENDING":
            raise HTTPException(409, "MLR review has already been decided")
        decision_id = f"MLRD_{uuid.uuid4().hex[:12].upper()}"
        effective = request.effective_from_utc.isoformat() if request.effective_from_utc else None
        expires = request.expires_at_utc.isoformat() if request.expires_at_utc else None
        conn.execute(
            "INSERT INTO mlr_review_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id, principal.tenant_id, review_id, review["claim_id"],
                principal.actor_id, principal.role, request.decision, request.rationale,
                request.approved_wording, request.conditions_of_use, effective, expires, utc_now(),
            ),
        )
        approval_status = "MLR_REJECTED"
        claim_status = "REJECTED"
        if request.decision in {"APPROVED", "APPROVED_WITH_CHANGES"}:
            approval_status = "MLR_APPROVED"
            claim_status = "ACTIVE"
            if request.decision == "APPROVED_WITH_CHANGES":
                conn.execute(
                    "UPDATE governed_claims SET claim_text=? WHERE tenant_id=? AND claim_id=?",
                    (request.approved_wording.strip(), principal.tenant_id, review["claim_id"]),
                )
        conn.execute(
            """UPDATE governed_claims SET approval_status=?, status=?, effective_from_utc=?,
                      expires_at_utc=?, conditions_of_use=?
               WHERE tenant_id=? AND claim_id=?""",
            (
                approval_status, claim_status, effective, expires, request.conditions_of_use,
                principal.tenant_id, review["claim_id"],
            ),
        )
        conn.execute(
            "UPDATE mlr_review_queue SET review_status=? WHERE tenant_id=? AND review_id=?",
            (request.decision, principal.tenant_id, review_id),
        )
        audit_id = append_audit(
            conn, principal, "MLR_REVIEW_DECISION", review["claim_id"],
            {"review_id": review_id, "decision_id": decision_id, "decision": request.decision, "approval_status": approval_status},
        )
    return {
        "mlr_decision_id": decision_id,
        "review_id": review_id,
        "claim_id": review["claim_id"],
        "decision": request.decision,
        "approval_status": approval_status,
        "effective_from_utc": effective,
        "expires_at_utc": expires,
        "audit_id": audit_id,
    }


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
