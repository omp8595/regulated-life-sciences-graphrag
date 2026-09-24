from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone

from product_api.app import connection, initialize_database
from product_api.semantic import get_concept, resolve_mentions, semantic_match


WORD = re.compile(r"[a-zA-Z0-9]+")
MEDICAL_CLASSES = {"MEDICAL_SCIENTIFIC_EVIDENCE", "CLINICAL_RESTRICTED", "PATIENT_LEVEL_DATA"}


def tokens(text: str) -> list[str]:
    return [token.lower() for token in WORD.findall(text) if len(token) > 1]


def cosine(left: Counter, right: Counter) -> float:
    numerator = sum(value * right.get(term, 0) for term, value in left.items())
    denominator = math.sqrt(sum(v * v for v in left.values())) * math.sqrt(sum(v * v for v in right.values()))
    return numerator / denominator if denominator else 0.0


def detected_entities(text: str) -> list[str]:
    return sorted({mention.canonical_name for mention in resolve_mentions(text)})


def concept_labels(concept_ids: list[str]) -> list[str]:
    labels = []
    for concept_id in concept_ids:
        concept = get_concept(concept_id)
        labels.append(concept.canonical_name if concept else concept_id)
    return sorted(labels)


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


def hybrid_search(
    question: str,
    tenant_id: str,
    role: str,
    purpose: str,
    market: str,
    top_k: int = 5,
) -> dict:
    initialize_database()
    query_tokens = Counter(tokens(question))
    anchors = detected_entities(question)
    with connection() as conn:
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
        governed_blocked = []
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
            semantic = semantic_match(question, claim["claim_text"])
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
                        "semantic_direct_matches": concept_labels(semantic["direct_matches"]),
                        "semantic_related_matches": concept_labels(semantic["related_matches"]),
                        "usage_condition": condition,
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
            semantic = semantic_match(question, row["chunk_text"])
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
                            semantic["direct_matches"] + semantic["related_matches"]
                        ),
                        "semantic_direct_matches": concept_labels(semantic["direct_matches"]),
                        "semantic_related_matches": concept_labels(semantic["related_matches"]),
                        "usage_condition": condition,
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
            "governance_message": "Evidence exists but is not permitted for this role and purpose.",
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
