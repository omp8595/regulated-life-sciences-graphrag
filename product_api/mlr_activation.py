from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from product_api.app import (
    TenantContext,
    append_audit,
    authenticated_principal,
    connection,
    utc_now,
)


MLR_ROLES = {"ROLE_MLR_REVIEWER", "ROLE_REGULATORY", "ROLE_LEGAL"}
SUBMITTER_ROLES = {"ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"}
APPROVAL_DECISIONS = {"APPROVED", "APPROVED_WITH_CHANGES"}

router = APIRouter(prefix="/v1/claims", tags=["Composed Claim MLR"])


class ComposedMlrSubmissionRequest(BaseModel):
    market: str = Field(min_length=2, max_length=100)
    purpose: str = Field(min_length=2, max_length=100)
    audience: str = Field(default="ALL", min_length=2, max_length=100)
    rationale: str = Field(min_length=20, max_length=4000)


class ComposedMlrDecisionRequest(BaseModel):
    decision: str = Field(pattern=r"^(APPROVED|APPROVED_WITH_CHANGES|REJECTED)$")
    rationale: str = Field(min_length=20, max_length=4000)
    authorization_confirmed: bool
    approved_wording: str | None = Field(default=None, max_length=8000)
    conditions_of_use: str | None = Field(default=None, max_length=2000)
    effective_from_utc: datetime | None = None
    expires_at_utc: datetime | None = None


def ensure_mlr_activation_schema() -> None:
    """Create the composed-claim MLR schema lazily after the base app schema exists."""
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS composed_claim_mlr_reviews (
                review_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                claim_id TEXT NOT NULL REFERENCES medical_validated_composed_claims(claim_id)
                    ON DELETE CASCADE,
                market TEXT NOT NULL,
                purpose TEXT NOT NULL,
                audience TEXT NOT NULL,
                review_status TEXT NOT NULL,
                rationale TEXT NOT NULL,
                submitted_by TEXT NOT NULL,
                submitted_role TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS composed_claim_mlr_decisions (
                decision_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                review_id TEXT NOT NULL REFERENCES composed_claim_mlr_reviews(review_id)
                    ON DELETE CASCADE,
                claim_id TEXT NOT NULL REFERENCES medical_validated_composed_claims(claim_id)
                    ON DELETE CASCADE,
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
            CREATE TABLE IF NOT EXISTS composed_claim_activations (
                activation_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                claim_id TEXT NOT NULL REFERENCES medical_validated_composed_claims(claim_id)
                    ON DELETE CASCADE,
                review_id TEXT NOT NULL REFERENCES composed_claim_mlr_reviews(review_id)
                    ON DELETE CASCADE,
                decision_id TEXT NOT NULL REFERENCES composed_claim_mlr_decisions(decision_id)
                    ON DELETE CASCADE,
                market TEXT NOT NULL,
                purpose TEXT NOT NULL,
                audience TEXT NOT NULL,
                approved_wording TEXT NOT NULL,
                conditions_of_use TEXT,
                effective_from_utc TEXT NOT NULL,
                expires_at_utc TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                deactivated_at_utc TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_composed_mlr_reviews_status
                ON composed_claim_mlr_reviews(tenant_id, review_status, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_composed_mlr_decisions_claim
                ON composed_claim_mlr_decisions(tenant_id, claim_id, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_composed_activations_scope
                ON composed_claim_activations(
                    tenant_id, status, purpose, market, audience,
                    effective_from_utc, expires_at_utc
                );
            """
        )


def _normalize_scope(value: str) -> str:
    return value.strip()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _runtime_status(row: sqlite3.Row, now: datetime | None = None) -> str:
    if row["status"] != "ACTIVE_FOR_GOVERNED_USE":
        return row["status"]
    current = now or datetime.now(timezone.utc)
    effective = datetime.fromisoformat(row["effective_from_utc"])
    expires = datetime.fromisoformat(row["expires_at_utc"])
    if current < effective:
        return "PENDING_EFFECTIVE"
    if current >= expires:
        return "EXPIRED"
    return "ACTIVE_FOR_GOVERNED_USE"


def list_eligible_composed_activations(
    conn: sqlite3.Connection,
    tenant_id: str,
    purpose: str,
    market: str,
    audience: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Return only currently active MLR approvals matching runtime scope.

    The existing query API does not yet carry audience. When audience is absent,
    only approvals scoped to ALL are eligible; this intentionally blocks narrower
    audience approvals rather than guessing the user's intended audience.
    """
    current = now or datetime.now(timezone.utc)
    audience_clause = "a.audience='ALL'"
    params: list[str] = [tenant_id, purpose, market]
    if audience:
        audience_clause = "(a.audience=? OR a.audience='ALL')"
        params.append(audience)

    rows = conn.execute(
        f"""SELECT a.*, c.claim_kind, c.claim_text, c.support_json,
                    c.approval_status, c.validated_by, c.validated_role
             FROM composed_claim_activations a
             JOIN medical_validated_composed_claims c
               ON c.claim_id=a.claim_id AND c.tenant_id=a.tenant_id
             WHERE a.tenant_id=?
               AND a.status='ACTIVE_FOR_GOVERNED_USE'
               AND a.purpose=?
               AND (a.market=? OR a.market='Global')
               AND {audience_clause}
             ORDER BY a.created_at_utc DESC""",
        tuple(params),
    ).fetchall()

    result: list[dict] = []
    for row in rows:
        if _runtime_status(row, current) != "ACTIVE_FOR_GOVERNED_USE":
            continue
        result.append(
            {
                "activation_id": row["activation_id"],
                "claim_id": row["claim_id"],
                "claim_kind": row["claim_kind"],
                "medical_claim_text": row["claim_text"],
                "approved_wording": row["approved_wording"],
                "support": json.loads(row["support_json"]),
                "approval_status": row["approval_status"],
                "market": row["market"],
                "purpose": row["purpose"],
                "audience": row["audience"],
                "conditions_of_use": row["conditions_of_use"],
                "effective_from_utc": row["effective_from_utc"],
                "expires_at_utc": row["expires_at_utc"],
                "validated_by": row["validated_by"],
                "validated_role": row["validated_role"],
            }
        )
    return result


def list_medical_composed_claims(
    conn: sqlite3.Connection,
    tenant_id: str,
) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM medical_validated_composed_claims
           WHERE tenant_id=? AND status='ACTIVE'
           ORDER BY validated_at_utc DESC""",
        (tenant_id,),
    ).fetchall()
    return [
        {
            "claim_id": row["claim_id"],
            "claim_kind": row["claim_kind"],
            "claim_text": row["claim_text"],
            "support": json.loads(row["support_json"]),
            "approval_status": row["approval_status"],
            "validated_by": row["validated_by"],
            "validated_role": row["validated_role"],
            "validated_at_utc": row["validated_at_utc"],
        }
        for row in rows
    ]


@router.post("/medical-validated/{claim_id}/mlr-submit", status_code=201)
def submit_composed_claim_to_mlr(
    claim_id: str,
    request: ComposedMlrSubmissionRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    if principal.role not in SUBMITTER_ROLES:
        raise HTTPException(403, "An authorized Medical or Regulatory role is required")
    ensure_mlr_activation_schema()
    with connection() as conn:
        claim = conn.execute(
            """SELECT * FROM medical_validated_composed_claims
               WHERE tenant_id=? AND claim_id=?""",
            (principal.tenant_id, claim_id),
        ).fetchone()
        if not claim:
            raise HTTPException(404, "Medical-validated composed claim not found")
        if claim["status"] != "ACTIVE":
            raise HTTPException(409, "Only active Medical-validated claims can enter MLR")
        pending = conn.execute(
            """SELECT review_id FROM composed_claim_mlr_reviews
               WHERE tenant_id=? AND claim_id=?
                 AND review_status IN ('SUBMITTED_FOR_MLR','UNDER_MLR_REVIEW')""",
            (principal.tenant_id, claim_id),
        ).fetchone()
        if pending:
            raise HTTPException(409, "An MLR review is already pending for this claim")

        review_id = f"CMLR_{uuid.uuid4().hex[:12].upper()}"
        created_at = utc_now()
        market = _normalize_scope(request.market)
        purpose = _normalize_scope(request.purpose)
        audience = _normalize_scope(request.audience)
        conn.execute(
            """INSERT INTO composed_claim_mlr_reviews
               (review_id, tenant_id, claim_id, market, purpose, audience,
                review_status, rationale, submitted_by, submitted_role, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'SUBMITTED_FOR_MLR', ?, ?, ?, ?)""",
            (
                review_id,
                principal.tenant_id,
                claim_id,
                market,
                purpose,
                audience,
                request.rationale,
                principal.actor_id,
                principal.role,
                created_at,
            ),
        )
        conn.execute(
            """UPDATE medical_validated_composed_claims
               SET approval_status='SUBMITTED_FOR_MLR'
               WHERE tenant_id=? AND claim_id=?""",
            (principal.tenant_id, claim_id),
        )
        audit_id = append_audit(
            conn,
            principal,
            "COMPOSED_CLAIM_SUBMITTED_MLR",
            claim_id,
            {
                "review_id": review_id,
                "market": market,
                "purpose": purpose,
                "audience": audience,
            },
        )
    return {
        "review_id": review_id,
        "claim_id": claim_id,
        "review_status": "SUBMITTED_FOR_MLR",
        "market": market,
        "purpose": purpose,
        "audience": audience,
        "audit_id": audit_id,
    }


@router.get("/mlr/reviews")
def list_composed_mlr_reviews(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> list[dict]:
    if principal.role not in MLR_ROLES:
        raise HTTPException(403, "An authorized MLR reviewer role is required")
    ensure_mlr_activation_schema()
    with connection() as conn:
        rows = conn.execute(
            """SELECT r.*, c.claim_kind, c.claim_text, c.support_json,
                      c.approval_status, c.validated_by, c.validated_role
               FROM composed_claim_mlr_reviews r
               JOIN medical_validated_composed_claims c
                 ON c.claim_id=r.claim_id AND c.tenant_id=r.tenant_id
               WHERE r.tenant_id=?
                 AND r.review_status IN ('SUBMITTED_FOR_MLR','UNDER_MLR_REVIEW')
               ORDER BY r.created_at_utc""",
            (principal.tenant_id,),
        ).fetchall()
    return [
        {
            "review_id": row["review_id"],
            "claim_id": row["claim_id"],
            "claim_kind": row["claim_kind"],
            "claim_text": row["claim_text"],
            "support": json.loads(row["support_json"]),
            "approval_status": row["approval_status"],
            "market": row["market"],
            "purpose": row["purpose"],
            "audience": row["audience"],
            "review_status": row["review_status"],
            "submission_rationale": row["rationale"],
            "submitted_by": row["submitted_by"],
            "validated_by": row["validated_by"],
            "validated_role": row["validated_role"],
        }
        for row in rows
    ]


@router.post("/mlr/reviews/{review_id}/decisions")
def decide_composed_mlr_review(
    review_id: str,
    request: ComposedMlrDecisionRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    if principal.role not in MLR_ROLES:
        raise HTTPException(403, "An authorized MLR reviewer role is required")
    if request.decision in APPROVAL_DECISIONS and not request.authorization_confirmed:
        raise HTTPException(403, "Explicit MLR authorization confirmation is required")
    if request.decision == "APPROVED_WITH_CHANGES" and not (request.approved_wording or "").strip():
        raise HTTPException(422, "Approved wording is required for APPROVED_WITH_CHANGES")
    if request.decision in APPROVAL_DECISIONS:
        if request.effective_from_utc is None or request.expires_at_utc is None:
            raise HTTPException(422, "Effective and expiry timestamps are required for approval")
        if request.expires_at_utc <= request.effective_from_utc:
            raise HTTPException(422, "Expiry must be later than the effective timestamp")

    ensure_mlr_activation_schema()
    with connection() as conn:
        row = conn.execute(
            """SELECT r.*, c.claim_text, c.validated_by
               FROM composed_claim_mlr_reviews r
               JOIN medical_validated_composed_claims c
                 ON c.claim_id=r.claim_id AND c.tenant_id=r.tenant_id
               WHERE r.tenant_id=? AND r.review_id=?""",
            (principal.tenant_id, review_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Composed-claim MLR review not found")
        if row["review_status"] not in {"SUBMITTED_FOR_MLR", "UNDER_MLR_REVIEW"}:
            raise HTTPException(409, "MLR review has already been decided")
        if row["validated_by"] == principal.actor_id:
            raise HTTPException(403, "MLR approval requires an independent reviewer")

        decision_id = f"CMLRD_{uuid.uuid4().hex[:12].upper()}"
        effective = _iso(request.effective_from_utc)
        expires = _iso(request.expires_at_utc)
        approved_wording = None
        if request.decision == "APPROVED":
            approved_wording = row["claim_text"]
        elif request.decision == "APPROVED_WITH_CHANGES":
            approved_wording = request.approved_wording.strip()

        conn.execute(
            """INSERT INTO composed_claim_mlr_decisions
               (decision_id, tenant_id, review_id, claim_id, reviewer_actor_id,
                reviewer_role, decision, rationale, approved_wording,
                conditions_of_use, effective_from_utc, expires_at_utc, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                principal.tenant_id,
                review_id,
                row["claim_id"],
                principal.actor_id,
                principal.role,
                request.decision,
                request.rationale,
                approved_wording,
                request.conditions_of_use,
                effective,
                expires,
                utc_now(),
            ),
        )
        conn.execute(
            """UPDATE composed_claim_mlr_reviews SET review_status=?
               WHERE tenant_id=? AND review_id=?""",
            (request.decision, principal.tenant_id, review_id),
        )

        activation_id = None
        approval_status = "MLR_REJECTED"
        if request.decision in APPROVAL_DECISIONS:
            approval_status = "MLR_APPROVED"
            deactivated_at = utc_now()
            conn.execute(
                """UPDATE composed_claim_activations
                   SET status='SUPERSEDED', deactivated_at_utc=?
                   WHERE tenant_id=? AND claim_id=? AND market=? AND purpose=?
                     AND audience=? AND status='ACTIVE_FOR_GOVERNED_USE'""",
                (
                    deactivated_at,
                    principal.tenant_id,
                    row["claim_id"],
                    row["market"],
                    row["purpose"],
                    row["audience"],
                ),
            )
            activation_id = f"CACT_{uuid.uuid4().hex[:12].upper()}"
            conn.execute(
                """INSERT INTO composed_claim_activations
                   (activation_id, tenant_id, claim_id, review_id, decision_id,
                    market, purpose, audience, approved_wording, conditions_of_use,
                    effective_from_utc, expires_at_utc, status, created_at_utc,
                    deactivated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'ACTIVE_FOR_GOVERNED_USE', ?, NULL)""",
                (
                    activation_id,
                    principal.tenant_id,
                    row["claim_id"],
                    review_id,
                    decision_id,
                    row["market"],
                    row["purpose"],
                    row["audience"],
                    approved_wording,
                    request.conditions_of_use,
                    effective,
                    expires,
                    utc_now(),
                ),
            )
        else:
            active = conn.execute(
                """SELECT 1 FROM composed_claim_activations
                   WHERE tenant_id=? AND claim_id=?
                     AND status='ACTIVE_FOR_GOVERNED_USE' LIMIT 1""",
                (principal.tenant_id, row["claim_id"]),
            ).fetchone()
            if active:
                approval_status = "MLR_APPROVED"

        conn.execute(
            """UPDATE medical_validated_composed_claims SET approval_status=?
               WHERE tenant_id=? AND claim_id=?""",
            (approval_status, principal.tenant_id, row["claim_id"]),
        )
        audit_id = append_audit(
            conn,
            principal,
            "COMPOSED_CLAIM_MLR_DECIDED",
            row["claim_id"],
            {
                "review_id": review_id,
                "decision_id": decision_id,
                "decision": request.decision,
                "activation_id": activation_id,
                "market": row["market"],
                "purpose": row["purpose"],
                "audience": row["audience"],
                "approval_status": approval_status,
            },
        )

    return {
        "decision_id": decision_id,
        "review_id": review_id,
        "claim_id": row["claim_id"],
        "decision": request.decision,
        "approval_status": approval_status,
        "activation_id": activation_id,
        "approved_wording": approved_wording,
        "market": row["market"],
        "purpose": row["purpose"],
        "audience": row["audience"],
        "effective_from_utc": effective,
        "expires_at_utc": expires,
        "audit_id": audit_id,
    }


@router.get("/activations")
def list_composed_claim_activations(
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> list[dict]:
    if principal.role not in {
        "ROLE_MEDICAL",
        "ROLE_CLINICAL",
        "ROLE_REGULATORY",
        "ROLE_MLR_REVIEWER",
        "ROLE_LEGAL",
    }:
        raise HTTPException(403, "An authorized claim-governance role is required")
    ensure_mlr_activation_schema()
    with connection() as conn:
        rows = conn.execute(
            """SELECT a.*, c.claim_kind, c.claim_text, c.support_json,
                      c.approval_status
               FROM composed_claim_activations a
               JOIN medical_validated_composed_claims c
                 ON c.claim_id=a.claim_id AND c.tenant_id=a.tenant_id
               WHERE a.tenant_id=?
               ORDER BY a.created_at_utc DESC""",
            (principal.tenant_id,),
        ).fetchall()
    return [
        {
            "activation_id": row["activation_id"],
            "claim_id": row["claim_id"],
            "claim_kind": row["claim_kind"],
            "medical_claim_text": row["claim_text"],
            "approved_wording": row["approved_wording"],
            "support": json.loads(row["support_json"]),
            "approval_status": row["approval_status"],
            "market": row["market"],
            "purpose": row["purpose"],
            "audience": row["audience"],
            "conditions_of_use": row["conditions_of_use"],
            "effective_from_utc": row["effective_from_utc"],
            "expires_at_utc": row["expires_at_utc"],
            "stored_status": row["status"],
            "runtime_status": _runtime_status(row),
            "created_at_utc": row["created_at_utc"],
        }
        for row in rows
    ]


def register_mlr_activation_routes(app: FastAPI) -> None:
    if getattr(app.state, "composed_mlr_routes_registered", False):
        return
    app.include_router(router)
    app.state.composed_mlr_routes_registered = True
