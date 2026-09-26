from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone

from product_api.app import connection, initialize_database
from product_api.evidence_intelligence import get_evidence_intelligence
from product_api.mlr_activation import (
    ensure_mlr_activation_schema,
    list_eligible_composed_activations,
    list_medical_composed_claims,
)
from product_api.semantic.store import concept_labels, resolve_mentions, semantic_match


WORD = re.compile(r"[a-zA-Z0-9]+")
MEDICAL_CLASSES = {"MEDICAL_SCIENTIFIC_EVIDENCE", "CLINICAL_RESTRICTED", "PATIENT_LEVEL_DATA"}
MEDICAL_GOVERNED_ROLES = {
    "ROLE_MEDICAL",
    "ROLE_CLINICAL",
    "ROLE_REGULATORY",
    "ROLE_MLR_REVIEWER",
}


def tokens(text: str) -> list[str]:
    return [token.lower() for token in WORD.findall(text) if len(token) > 1]


def cosine(left: Counter, right: Counter) -> float:
    numerator = sum(value * right.get(term, 0) for term, value in left.items())
    denominator = math.sqrt(sum(v * v for v in left.values())) * math.sqrt(sum(v * v for v in right.values()))
    return numerator / denominator if denominator else 0.0


def policy_decision(role: str, purpose: str, data_class: str) -> tuple[str, str]:
    if role == "ROLE_COMMERCIAL" and data_class in {"CLINICAL_RESTRICTED", "PATIENT_LEVEL_DATA"}:
        return "DENY", "NO_COMMERCIAL_ACCESS"
    if purpose == "PROMOTIONAL_CONTENT" and data_class in MEDICAL_CLASSES:
        return "BLOCKED", "MLR_APPROVAL_REQUIRED"
    if role == "ROLE_COMMERCIAL" and data_class == "MEDICAL_SCIENTIFIC_EVIDENCE":
        return "ALLOW", "READ_ONLY_NON_PROMOTIONAL"
    if role == "ROLE_MEDICAL":
        return "ALLOW", "AUTHORIZED_MEDICAL_USE_WITH_CITATION"
    return "ALLOW", "CITATION_REQUIRED"


def _score_text(conn, question: str, query_tokens: Counter, text: str) -> tuple[float, float, dict]:
    lexical = cosine(query_tokens, Counter(tokens(text)))
    semantic = semantic_match(conn, question, text)
    score = (0.75 * lexical) + (0.25 * semantic["score"])
    return score, lexical, semantic


def _composed_claim_result(
    conn,
    tenant_id: str,
    question: str,
    query_tokens: Counter,
    claim: dict,
    text: str,
    usage_condition: str,
    activation: dict | None = None,
) -> dict | None:
    score, lexical, semantic = _score_text(conn, question, query_tokens, text)
    if score <= 0:
        return None
    support = claim["support"]
    result = {
        "claim_id": claim["claim_id"],
        "claim_text": text,
        "claim_type": claim["claim_kind"],
        "version": 1,
        "market": activation["market"] if activation else "Validated medical evidence",
        "approval_status": claim["approval_status"],
        "effective_from_utc": activation["effective_from_utc"] if activation else None,
        "expires_at_utc": activation["expires_at_utc"] if activation else None,
        "document_id": support["document_id"],
        "chunk_id": support["chunk_id"],
        "file_name": support["file_name"],
        "score": round(score, 4),
        "lexical_score": round(lexical, 4),
        "semantic_score": semantic["score"],
        "semantic_direct_matches": concept_labels(conn, semantic["direct_matches"]),
        "semantic_related_matches": concept_labels(conn, semantic["related_matches"]),
        "usage_condition": usage_condition,
        "evidence_validation_id": support["validation_id"],
        "evidence_structure_id": support["structure_id"],
        "evidence_intelligence": get_evidence_intelligence(conn, tenant_id, support["chunk_id"]),
        "claim_source": "EVIDENCE_BOUND_COMPOSED_CLAIM",
    }
    if activation:
        result.update(
            {
                "activation_id": activation["activation_id"],
                "purpose_scope": activation["purpose"],
                "audience_scope": activation["audience"],
                "conditions_of_use": activation["conditions_of_use"],
                "approved_wording_only": True,
            }
        )
    return result


def hybrid_search(
    question: str,
    tenant_id: str,
    role: str,
    purpose: str,
    market: str,
    top_k: int = 5,
) -> dict:
    initialize_database()
    ensure_mlr_activation_schema()
    query_tokens = Counter(tokens(question))
    with connection() as conn:
        anchors = sorted({mention.canonical_name for mention in resolve_mentions(conn, question)})
        governed_blocked: list[str] = []

        # New evidence-bound claim path. Promotional use can only return the exact
        # currently effective MLR-approved wording. Because the current QueryRequest
        # does not yet carry audience, only approvals scoped to ALL are eligible.
        if purpose == "PROMOTIONAL_CONTENT":
            composed_matches = []
            activations = list_eligible_composed_activations(
                conn,
                tenant_id,
                purpose,
                market,
                audience=None,
            )
            for activation in activations:
                result = _composed_claim_result(
                    conn,
                    tenant_id,
                    question,
                    query_tokens,
                    activation,
                    activation["approved_wording"],
                    activation["conditions_of_use"] or "MLR_APPROVED_WORDING_ONLY",
                    activation=activation,
                )
                if result:
                    composed_matches.append(result)
            composed_matches.sort(key=lambda item: item["score"], reverse=True)
            if composed_matches:
                claims = composed_matches[: max(1, min(top_k, 20))]
                return {
                    "status": "ANSWERED",
                    "response_type": "GOVERNED_ANSWER",
                    "tenant_id": tenant_id,
                    "market": market,
                    "resolved_entities": anchors,
                    "answer": " ".join(claim["claim_text"] for claim in claims),
                    "governed_claims": claims,
                    "result_count": len(claims),
                    "results": [],
                    "governance_message": (
                        "The response uses currently effective MLR-approved wording "
                        "from evidence-bound composed claims."
                    ),
                }

            # If a relevant Medical-validated composed claim exists but there is no
            # applicable activation, preserve BLOCK rather than falling back to raw
            # evidence or unconstrained generation.
            for claim in list_medical_composed_claims(conn, tenant_id):
                score, _, _ = _score_text(conn, question, query_tokens, claim["claim_text"])
                if score > 0:
                    if claim["approval_status"] == "MLR_APPROVED":
                        governed_blocked.append("MLR_APPROVAL_SCOPE_OR_VALIDITY_MISMATCH")
                    else:
                        governed_blocked.append("MLR_APPROVAL_REQUIRED")

        # Medical/Regulatory use can consume independently Medical-validated
        # evidence-bound claims before MLR. These claims remain non-promotional.
        elif role in MEDICAL_GOVERNED_ROLES:
            composed_medical_matches = []
            for claim in list_medical_composed_claims(conn, tenant_id):
                result = _composed_claim_result(
                    conn,
                    tenant_id,
                    question,
                    query_tokens,
                    claim,
                    claim["claim_text"],
                    "MEDICAL_VALIDATED_NON_PROMOTIONAL",
                )
                if result:
                    composed_medical_matches.append(result)
            composed_medical_matches.sort(key=lambda item: item["score"], reverse=True)
            if composed_medical_matches:
                claims = composed_medical_matches[: max(1, min(top_k, 20))]
                return {
                    "status": "ANSWERED",
                    "response_type": "GOVERNED_ANSWER",
                    "tenant_id": tenant_id,
                    "market": market,
                    "resolved_entities": anchors,
                    "answer": " ".join(claim["claim_text"] for claim in claims),
                    "governed_claims": claims,
                    "result_count": len(claims),
                    "results": [],
                    "governance_message": (
                        "The response uses independently Medical-validated, evidence-bound "
                        "claims. Promotional use still requires an applicable MLR approval."
                    ),
                }

        # Legacy governed-claim path retained for backward compatibility with the
        # original prototype workflow.
        claim_rows = conn.execute(
            """SELECT g.*, e.document_id, e.chunk_id, d.file_name
               FROM governed_claims g
               JOIN governed_claim_evidence e
                 ON e.claim_id=g.claim_id AND e.tenant_id=g.tenant_id
               JOIN documents d
                 ON d.document_id=e.document_id AND d.tenant_id=e.tenant_id
               WHERE g.tenant_id=? AND g.status='ACTIVE'
                 AND (g.market=? OR g.market='Global')""",
            (tenant_id, market),
        ).fetchall()
        governed_matches = []
        for claim in claim_rows:
            if purpose == "PROMOTIONAL_CONTENT":
                if claim["approval_status"] != "MLR_APPROVED":
                    governed_blocked.append("MLR_APPROVAL_REQUIRED")
                    continue
                now = datetime.now(timezone.utc)
                effective = datetime.fromisoformat(claim["effective_from_utc"]) if claim["effective_from_utc"] else None
                expires = datetime.fromisoformat(claim["expires_at_utc"]) if claim["expires_at_utc"] else None
                if not effective or not expires or now < effective or now >= expires:
                    governed_blocked.append("MLR_APPROVAL_NOT_CURRENT")
                    continue
                condition = claim["conditions_of_use"] or "MLR_APPROVED_WORDING_ONLY"
            else:
                decision, condition = policy_decision(role, purpose, claim["data_class"])
                if decision != "ALLOW":
                    governed_blocked.append(condition)
                    continue
            lexical = cosine(query_tokens, Counter(tokens(claim["claim_text"])))
            semantic = semantic_match(conn, question, claim["claim_text"])
            score = (0.75 * lexical) + (0.25 * semantic["score"])
            if score > 0:
                governed_matches.append(
                    {
                        "claim_id": claim["claim_id"],
                        "claim_text": claim["claim_text"],
                        "claim_type": claim["claim_type"],
                        "version": claim["version"],
                        "market": claim["market"],
                        "approval_status": claim["approval_status"],
                        "effective_from_utc": claim["effective_from_utc"],
                        "expires_at_utc": claim["expires_at_utc"],
                        "document_id": claim["document_id"],
                        "chunk_id": claim["chunk_id"],
                        "file_name": claim["file_name"],
                        "score": round(score, 4),
                        "lexical_score": round(lexical, 4),
                        "semantic_score": semantic["score"],
                        "semantic_direct_matches": concept_labels(conn, semantic["direct_matches"]),
                        "semantic_related_matches": concept_labels(conn, semantic["related_matches"]),
                        "usage_condition": condition,
                        "evidence_intelligence": get_evidence_intelligence(
                            conn, tenant_id, claim["chunk_id"]
                        ),
                    }
                )
        governed_matches.sort(key=lambda item: item["score"], reverse=True)
        if governed_matches:
            claims = governed_matches[: max(1, min(top_k, 20))]
            return {
                "status": "ANSWERED",
                "response_type": "GOVERNED_ANSWER",
                "tenant_id": tenant_id,
                "market": market,
                "resolved_entities": anchors,
                "answer": " ".join(claim["claim_text"] for claim in claims),
                "governed_claims": claims,
                "result_count": len(claims),
                "results": [],
                "governance_message": "The response is supported by SME-validated governed claims.",
            }

        rows = conn.execute(
            """SELECT c.chunk_id, c.chunk_text, c.page_number, c.chunk_sequence,
                      d.document_id, d.file_name, d.market, d.data_class,
                      d.sensitivity, d.status
               FROM document_chunks c
               JOIN documents d ON d.document_id=c.document_id AND d.tenant_id=c.tenant_id
               WHERE c.tenant_id=?
                 AND d.status='READY_FOR_SME_REVIEW'
                 AND (d.market=? OR d.market='Global')""",
            (tenant_id, market),
        ).fetchall()

        allowed = []
        blocked_conditions = list(governed_blocked)
        for row in rows:
            decision, condition = policy_decision(role, purpose, row["data_class"])
            if decision != "ALLOW":
                blocked_conditions.append(condition)
                continue
            lexical = cosine(query_tokens, Counter(tokens(row["chunk_text"])))
            semantic = semantic_match(conn, question, row["chunk_text"])
            graph_score = semantic["score"]
            hybrid_score = (0.75 * lexical) + (0.25 * graph_score)
            if hybrid_score > 0:
                allowed.append(
                    {
                        "chunk_id": row["chunk_id"],
                        "document_id": row["document_id"],
                        "file_name": row["file_name"],
                        "page_number": row["page_number"],
                        "market": row["market"],
                        "data_class": row["data_class"],
                        "text": row["chunk_text"],
                        "lexical_score": round(lexical, 4),
                        "graph_score": round(graph_score, 4),
                        "hybrid_score": round(hybrid_score, 4),
                        "graph_anchors": concept_labels(
                            conn, semantic["direct_matches"] + semantic["related_matches"]
                        ),
                        "semantic_direct_matches": concept_labels(conn, semantic["direct_matches"]),
                        "semantic_related_matches": concept_labels(conn, semantic["related_matches"]),
                        "usage_condition": condition,
                        "evidence_intelligence": get_evidence_intelligence(
                            conn, tenant_id, row["chunk_id"]
                        ),
                    }
                )

    allowed.sort(key=lambda result: result["hybrid_score"], reverse=True)
    results = allowed[: max(1, min(top_k, 20))]
    if results:
        return {
            "status": "EVIDENCE_ONLY",
            "response_type": "EVIDENCE_DISCOVERY",
            "tenant_id": tenant_id,
            "market": market,
            "resolved_entities": anchors,
            "result_count": len(results),
            "results": results,
            "governance_message": "Permitted evidence was found; it is not an SME-validated governed answer.",
        }
    if blocked_conditions:
        return {
            "status": "BLOCKED",
            "response_type": "POLICY_BLOCK",
            "tenant_id": tenant_id,
            "market": market,
            "result_count": 0,
            "results": [],
            "block_reasons": sorted(set(blocked_conditions)),
            "governance_message": "Evidence exists but is not permitted for this role, purpose, market, or approval state.",
        }
    return {
        "status": "ABSTAIN",
        "response_type": "NO_SUPPORT",
        "tenant_id": tenant_id,
        "market": market,
        "result_count": 0,
        "results": [],
        "governance_message": "No authoritative permitted evidence supports this request.",
    }
