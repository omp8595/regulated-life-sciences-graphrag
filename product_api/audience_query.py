from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field

from product_api.app import (
    TenantContext,
    append_audit,
    authenticated_principal,
    connection,
)
from product_api.mlr_activation import (
    ensure_mlr_activation_schema,
    list_eligible_composed_activations,
    list_medical_composed_claims,
)
from product_api.retrieval import (
    _composed_claim_result,
    _score_text,
    hybrid_search,
    tokens,
)
from product_api.semantic.store import resolve_mentions


router = APIRouter(prefix="/v1/query", tags=["Audience-aware Governed Query"])


class ContextualQueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    purpose: str = Field(min_length=2, max_length=100)
    market: str = Field(min_length=2, max_length=100)
    audience: str = Field(default="ALL", min_length=2, max_length=100)
    top_k: int = Field(default=5, ge=1, le=20)


def _scope_block_reason(row, purpose: str, market: str, audience: str, now: datetime) -> str:
    if row["purpose"] != purpose:
        return "MLR_PURPOSE_MISMATCH"
    if row["market"] not in {market, "Global"}:
        return "MLR_MARKET_MISMATCH"
    if row["audience"] not in {audience, "ALL"}:
        return "MLR_AUDIENCE_MISMATCH"
    effective = datetime.fromisoformat(row["effective_from_utc"])
    expires = datetime.fromisoformat(row["expires_at_utc"])
    if now < effective or now >= expires:
        return "MLR_APPROVAL_NOT_CURRENT"
    if row["status"] != "ACTIVE_FOR_GOVERNED_USE":
        return "MLR_APPROVAL_NOT_ACTIVE"
    return "MLR_APPROVAL_SCOPE_OR_VALIDITY_MISMATCH"


def contextual_hybrid_search(
    question: str,
    tenant_id: str,
    role: str,
    purpose: str,
    market: str,
    audience: str,
    top_k: int = 5,
) -> dict:
    """Audience-aware runtime policy path.

    Non-promotional requests retain the existing governed retrieval behavior.
    Promotional requests may use an evidence-bound composed claim only when a
    currently effective MLR activation matches market, purpose and audience.
    """
    audience = audience.strip()
    if purpose != "PROMOTIONAL_CONTENT":
        result = hybrid_search(
            question=question,
            tenant_id=tenant_id,
            role=role,
            purpose=purpose,
            market=market,
            top_k=top_k,
        )
        result["audience"] = audience
        return result

    ensure_mlr_activation_schema()
    query_tokens = Counter(tokens(question))
    with connection() as conn:
        anchors = sorted({mention.canonical_name for mention in resolve_mentions(conn, question)})
        matches: list[dict] = []
        activations = list_eligible_composed_activations(
            conn,
            tenant_id,
            purpose,
            market,
            audience=audience,
        )
        for activation in activations:
            item = _composed_claim_result(
                conn,
                tenant_id,
                question,
                query_tokens,
                activation,
                activation["approved_wording"],
                activation["conditions_of_use"] or "MLR_APPROVED_WORDING_ONLY",
                activation=activation,
            )
            if item:
                matches.append(item)

        matches.sort(key=lambda item: item["score"], reverse=True)
        if matches:
            claims = matches[: max(1, min(top_k, 20))]
            return {
                "status": "ANSWERED",
                "response_type": "GOVERNED_ANSWER",
                "tenant_id": tenant_id,
                "market": market,
                "audience": audience,
                "resolved_entities": anchors,
                "answer": " ".join(claim["claim_text"] for claim in claims),
                "governed_claims": claims,
                "result_count": len(claims),
                "results": [],
                "governance_message": (
                    "The response uses currently effective MLR-approved wording "
                    "matching market, purpose and audience."
                ),
            }

        # Fail closed when a scientifically relevant Medical-validated composed
        # claim exists but its MLR scope does not match this request.
        block_reasons: list[str] = []
        now = datetime.now(timezone.utc)
        for claim in list_medical_composed_claims(conn, tenant_id):
            score, _, _ = _score_text(conn, question, query_tokens, claim["claim_text"])
            if score <= 0:
                continue
            if claim["approval_status"] != "MLR_APPROVED":
                block_reasons.append("MLR_APPROVAL_REQUIRED")
                continue
            rows = conn.execute(
                """SELECT status, market, purpose, audience,
                          effective_from_utc, expires_at_utc
                   FROM composed_claim_activations
                   WHERE tenant_id=? AND claim_id=?
                   ORDER BY created_at_utc DESC""",
                (tenant_id, claim["claim_id"]),
            ).fetchall()
            if not rows:
                block_reasons.append("MLR_APPROVAL_REQUIRED")
                continue
            block_reasons.extend(
                _scope_block_reason(row, purpose, market, audience, now) for row in rows
            )

        if block_reasons:
            return {
                "status": "BLOCKED",
                "response_type": "POLICY_BLOCK",
                "tenant_id": tenant_id,
                "market": market,
                "audience": audience,
                "resolved_entities": anchors,
                "result_count": 0,
                "results": [],
                "governed_claims": [],
                "block_reasons": sorted(set(block_reasons)),
                "governance_message": (
                    "Relevant knowledge exists, but no currently effective MLR approval "
                    "matches the requested market, purpose and audience."
                ),
            }

    # No relevant evidence-bound composed claim exists. Preserve legacy behavior
    # for older governed claims and evidence discovery.
    result = hybrid_search(
        question=question,
        tenant_id=tenant_id,
        role=role,
        purpose=purpose,
        market=market,
        top_k=top_k,
    )
    result["audience"] = audience
    return result


@router.post("/contextual")
def governed_contextual_query(
    request: ContextualQueryRequest,
    principal: Annotated[TenantContext, Depends(authenticated_principal)],
) -> dict:
    result = contextual_hybrid_search(
        question=request.question,
        tenant_id=principal.tenant_id,
        role=principal.role,
        purpose=request.purpose,
        market=request.market,
        audience=request.audience,
        top_k=request.top_k,
    )
    with connection() as conn:
        audit_id = append_audit(
            conn,
            principal,
            "GOVERNED_CONTEXTUAL_QUERY_DECISION",
            f"QCTX_{uuid.uuid4().hex[:12].upper()}",
            {
                "question_sha256": hashlib.sha256(request.question.encode()).hexdigest(),
                "purpose": request.purpose,
                "market": request.market,
                "audience": request.audience,
                "decision_status": result["status"],
                "response_type": result["response_type"],
                "result_count": result["result_count"],
            },
        )
    result.update(
        {
            "audit_id": audit_id,
            "actor_id": principal.actor_id,
            "role": principal.role,
            "purpose": request.purpose,
            "audience": request.audience,
        }
    )
    return result


def register_audience_query_routes(app: FastAPI) -> None:
    if getattr(app.state, "audience_query_routes_registered", False):
        return
    app.include_router(router)
    app.state.audience_query_routes_registered = True
