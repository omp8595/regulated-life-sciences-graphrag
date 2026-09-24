from __future__ import annotations

import math
import re
from collections import Counter

from product_api.app import connection, initialize_database
from product_api.worker import KNOWN_ENTITIES


WORD = re.compile(r"[a-zA-Z0-9]+")
MEDICAL_CLASSES = {"MEDICAL_SCIENTIFIC_EVIDENCE", "CLINICAL_RESTRICTED", "PATIENT_LEVEL_DATA"}


def tokens(text: str) -> list[str]:
    return [token.lower() for token in WORD.findall(text) if len(token) > 1]


def cosine(left: Counter, right: Counter) -> float:
    numerator = sum(value * right.get(term, 0) for term, value in left.items())
    denominator = math.sqrt(sum(v * v for v in left.values())) * math.sqrt(sum(v * v for v in right.values()))
    return numerator / denominator if denominator else 0.0


def detected_entities(text: str) -> list[str]:
    lowered = text.lower()
    return sorted({label for term, (_, label) in KNOWN_ENTITIES.items() if re.search(rf"\b{re.escape(term)}\b", lowered)})


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
        blocked_conditions = []
        for row in rows:
            decision, condition = policy_decision(role, purpose, row["data_class"])
            if decision != "ALLOW":
                blocked_conditions.append(condition)
                continue
            lexical = cosine(query_tokens, Counter(tokens(row["chunk_text"])))
            chunk_entities = detected_entities(row["chunk_text"])
            shared_entities = sorted(set(anchors) & set(chunk_entities))
            graph_score = len(shared_entities) / max(len(anchors), 1)
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
                        "graph_anchors": shared_entities,
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

