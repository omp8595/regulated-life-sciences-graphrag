from __future__ import annotations

import json
import re
import sqlite3
import uuid

from product_api.semantic.store import resolve_mentions


REVIEWABLE_FIELDS = (
    "study",
    "population",
    "intervention",
    "comparator",
    "endpoint",
    "outcome",
    "safety",
)


def _decoded_structure_row(row: sqlite3.Row) -> dict:
    return {
        "structure_id": row["structure_id"],
        "study": json.loads(row["study_json"]),
        "population": json.loads(row["population_json"]),
        "intervention": json.loads(row["intervention_json"]),
        "comparator": json.loads(row["comparator_json"]),
        "endpoint": json.loads(row["endpoint_json"]),
        "outcome": json.loads(row["outcome_json"]),
        "safety": json.loads(row["safety_json"]),
        "semantic_relationships": json.loads(row["semantic_relationships_json"]),
        "extraction_method": row["extraction_method"],
        "review_status": row["review_status"],
    }

def _has_material_value(value) -> bool:
    if isinstance(value, dict):
        return any(_has_material_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_material_value(item) for item in value)
    return value not in (None, "")


def _empty_like(value):
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    return None


def _latest_field_reviews(
    conn: sqlite3.Connection,
    tenant_id: str,
    structure_id: str,
) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT * FROM evidence_intelligence_review_decisions
           WHERE tenant_id=? AND structure_id=?
           ORDER BY created_at_utc, review_id""",
        (tenant_id, structure_id),
    ).fetchall()
    latest: dict[str, dict] = {}
    for row in rows:
        latest[row["field_name"]] = {
            "review_id": row["review_id"],
            "field_name": row["field_name"],
            "decision": row["decision"],
            "original_value": json.loads(row["original_value_json"]),
            "reviewed_value": (
                json.loads(row["reviewed_value_json"])
                if row["reviewed_value_json"] is not None
                else None
            ),
            "rationale": row["rationale"],
            "reviewer_actor_id": row["reviewer_actor_id"],
            "reviewer_role": row["reviewer_role"],
            "created_at_utc": row["created_at_utc"],
        }
    return latest


def get_review_state(
    conn: sqlite3.Connection,
    tenant_id: str,
    structure_id: str,
) -> dict:
    row = conn.execute(
        """SELECT * FROM evidence_intelligence
           WHERE tenant_id=? AND structure_id=?""",
        (tenant_id, structure_id),
    ).fetchone()
    if not row:
        raise LookupError("Evidence intelligence record not found")

    structure = _decoded_structure_row(row)
    latest = _latest_field_reviews(conn, tenant_id, structure_id)
    required_fields = [
        field for field in REVIEWABLE_FIELDS if _has_material_value(structure[field])
    ]
    reviewed_fields = [field for field in required_fields if field in latest]
    missing_fields = [field for field in required_fields if field not in latest]

    validated_row = conn.execute(
        """SELECT * FROM evidence_intelligence_validations
           WHERE tenant_id=? AND structure_id=?""",
        (tenant_id, structure_id),
    ).fetchone()

    return {
        "structure": structure,
        "required_fields": required_fields,
        "reviewed_fields": reviewed_fields,
        "missing_fields": missing_fields,
        "field_reviews": latest,
        "can_finalize": bool(required_fields) and not missing_fields,
        "validated_payload": (
            json.loads(validated_row["validated_payload_json"]) if validated_row else None
        ),
        "validation_id": validated_row["validation_id"] if validated_row else None,
    }


def record_field_review(
    conn: sqlite3.Connection,
    tenant_id: str,
    structure_id: str,
    field_name: str,
    decision: str,
    reviewed_value,
    rationale: str,
    reviewer_actor_id: str,
    reviewer_role: str,
    created_at_utc: str,
) -> dict:
    if field_name not in REVIEWABLE_FIELDS:
        raise ValueError(f"Unsupported evidence field: {field_name}")
    if decision not in {"VERIFIED", "CORRECTED", "REJECTED"}:
        raise ValueError(f"Unsupported evidence review decision: {decision}")

    row = conn.execute(
        """SELECT * FROM evidence_intelligence
           WHERE tenant_id=? AND structure_id=?""",
        (tenant_id, structure_id),
    ).fetchone()
    if not row:
        raise LookupError("Evidence intelligence record not found")
    if row["review_status"] == "SME_VALIDATED_EVIDENCE":
        raise ValueError("Validated evidence must be reopened before additional field review")

    structure = _decoded_structure_row(row)
    original_value = structure[field_name]
    if not _has_material_value(original_value):
        raise ValueError("Only populated extracted fields require evidence review")
    if decision == "CORRECTED" and not _has_material_value(reviewed_value):
        raise ValueError("A corrected value is required for CORRECTED")
    if decision == "CORRECTED" and type(reviewed_value) is not type(original_value):
        raise ValueError("Corrected value must preserve the extracted field data shape")
    if decision != "CORRECTED":
        reviewed_value = None

    review_id = f"EVR_{uuid.uuid4().hex[:12].upper()}"
    conn.execute(
        """INSERT INTO evidence_intelligence_review_decisions
           (review_id, tenant_id, structure_id, field_name, decision,
            original_value_json, reviewed_value_json, rationale,
            reviewer_actor_id, reviewer_role, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            review_id,
            tenant_id,
            structure_id,
            field_name,
            decision,
            json.dumps(original_value, sort_keys=True),
            json.dumps(reviewed_value, sort_keys=True) if reviewed_value is not None else None,
            rationale,
            reviewer_actor_id,
            reviewer_role,
            created_at_utc,
        ),
    )
    conn.execute(
        """UPDATE evidence_intelligence
           SET review_status='PARTIALLY_REVIEWED'
           WHERE tenant_id=? AND structure_id=?""",
        (tenant_id, structure_id),
    )
    state = get_review_state(conn, tenant_id, structure_id)
    return {
        "review_id": review_id,
        "structure_id": structure_id,
        "field_name": field_name,
        "decision": decision,
        "review_status": "PARTIALLY_REVIEWED",
        "remaining_fields": state["missing_fields"],
    }


def finalize_evidence_review(
    conn: sqlite3.Connection,
    tenant_id: str,
    structure_id: str,
    reviewer_actor_id: str,
    reviewer_role: str,
    rationale: str,
    created_at_utc: str,
) -> dict:
    state = get_review_state(conn, tenant_id, structure_id)
    if state["structure"]["review_status"] == "SME_VALIDATED_EVIDENCE":
        raise ValueError("Evidence intelligence record is already validated")
    if not state["required_fields"]:
        raise ValueError("There are no populated scientific fields to validate")
    if state["missing_fields"]:
        raise ValueError(
            "All populated evidence fields must be reviewed before finalization: "
            + ", ".join(state["missing_fields"])
        )

    validated = {}
    for field in REVIEWABLE_FIELDS:
        original = state["structure"][field]
        if field not in state["required_fields"]:
            validated[field] = original
            continue
        review = state["field_reviews"][field]
        if review["decision"] == "VERIFIED":
            validated[field] = original
        elif review["decision"] == "CORRECTED":
            validated[field] = review["reviewed_value"]
        else:
            validated[field] = _empty_like(original)

    validated["semantic_relationships"] = state["structure"]["semantic_relationships"]
    validated["evidence_status"] = "SME_VALIDATED_EVIDENCE"

    validation_id = f"EVV_{uuid.uuid4().hex[:12].upper()}"
    conn.execute(
        """INSERT INTO evidence_intelligence_validations
           (validation_id, tenant_id, structure_id, validated_payload_json,
            validated_by, validated_role, rationale, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            validation_id,
            tenant_id,
            structure_id,
            json.dumps(validated, sort_keys=True),
            reviewer_actor_id,
            reviewer_role,
            rationale,
            created_at_utc,
        ),
    )
    conn.execute(
        """UPDATE evidence_intelligence
           SET review_status='SME_VALIDATED_EVIDENCE'
           WHERE tenant_id=? AND structure_id=?""",
        (tenant_id, structure_id),
    )
    return {
        "validation_id": validation_id,
        "structure_id": structure_id,
        "review_status": "SME_VALIDATED_EVIDENCE",
        "validated_payload": validated,
    }


SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
ENDPOINT_PATTERNS = (
    ("PFS", re.compile(r"\b(?:progression[- ]free survival|PFS)\b", re.I)),
    ("OS", re.compile(r"\b(?:overall survival|OS)\b", re.I)),
    ("ORR", re.compile(r"\b(?:overall response rate|objective response rate|ORR)\b", re.I)),
    ("CR", re.compile(r"\b(?:complete response|CR)\b", re.I)),
    ("DOR", re.compile(r"\b(?:duration of response|DOR)\b", re.I)),
)
SAFETY_PATTERN = re.compile(
    r"\b(?:safety|adverse events?|treatment[- ]emergent adverse events?|"
    r"serious adverse events?|atrial fibrillation|bleeding|discontinuation|toxicity)\b",
    re.I,
)
OUTCOME_SIGNAL = re.compile(
    r"(?:\b\d+(?:\.\d+)?%|\bHR\b|\bhazard ratio\b|\bCI\b|\bp\s*[<=>]\s*0?\.\d+|\bmonths?\b)",
    re.I,
)
POPULATION_SIGNAL = re.compile(
    r"\b(?:patients?|participants?|subjects?)\b",
    re.I,
)


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    return [item.strip() for item in SENTENCE_SPLIT.split(normalized) if item.strip()]


def _active_relationships(
    conn: sqlite3.Connection,
    concept_ids: set[str],
) -> list[sqlite3.Row]:
    if not concept_ids:
        return []
    placeholders = ",".join("?" for _ in concept_ids)
    ids = sorted(concept_ids)
    return conn.execute(
        f"""SELECT r.source_concept_id, r.relationship_type, r.target_concept_id,
                   s.canonical_name AS source_name, s.concept_type AS source_type,
                   t.canonical_name AS target_name, t.concept_type AS target_type
            FROM semantic_relationships r
            JOIN semantic_concepts s
              ON s.concept_id=r.source_concept_id AND s.version=r.source_version
            JOIN semantic_concepts t
              ON t.concept_id=r.target_concept_id AND t.version=r.target_version
            WHERE r.status='ACTIVE'
              AND r.source_concept_id IN ({placeholders})
              AND r.target_concept_id IN ({placeholders})""",
        (*ids, *ids),
    ).fetchall()


def extract_evidence_intelligence(
    conn: sqlite3.Connection,
    text: str,
) -> dict:
    mentions = resolve_mentions(conn, text)
    by_id = {item.concept_id: item for item in mentions}
    concept_ids = set(by_id)
    relationships = _active_relationships(conn, concept_ids)

    studies = sorted(
        {item.canonical_name for item in mentions if item.concept_type == "CLINICAL_TRIAL"}
    )
    indications = sorted(
        {item.canonical_name for item in mentions if item.concept_type == "INDICATION"}
    )
    drugs = sorted(
        {item.canonical_name for item in mentions if item.concept_type == "DRUG"}
    )
    brands = sorted(
        {item.canonical_name for item in mentions if item.concept_type == "BRAND"}
    )

    interventions: set[str] = set()
    comparators: set[str] = set()
    studied_indications: set[str] = set(indications)
    relationship_paths: list[str] = []

    for row in relationships:
        relationship_paths.append(
            f"{row['source_name']} —{row['relationship_type']}→ {row['target_name']}"
        )
        if row["relationship_type"] == "EVALUATES":
            interventions.add(row["target_name"])
        elif row["relationship_type"] == "COMPARES_WITH":
            comparators.add(row["target_name"])
        elif row["relationship_type"] == "STUDIES_INDICATION":
            studied_indications.add(row["target_name"])

    # If no typed trial relation is available, keep explicit drug mentions as
    # unclassified treatments rather than guessing intervention/comparator roles.
    unclassified_treatments = sorted(set(drugs + brands) - interventions - comparators)

    sentences = _sentences(text)
    population_context = [
        sentence for sentence in sentences if POPULATION_SIGNAL.search(sentence)
    ]

    endpoints: set[str] = set()
    outcome_sentences: list[str] = []
    safety_sentences: list[str] = []
    for sentence in sentences:
        matched_endpoint = False
        for endpoint_name, pattern in ENDPOINT_PATTERNS:
            if pattern.search(sentence):
                endpoints.add(endpoint_name)
                matched_endpoint = True
        if matched_endpoint and OUTCOME_SIGNAL.search(sentence):
            outcome_sentences.append(sentence)
        if SAFETY_PATTERN.search(sentence):
            safety_sentences.append(sentence)

    return {
        "studies": studies,
        "population_indications": sorted(studied_indications),
        "population_context": population_context,
        "interventions": sorted(interventions),
        "comparators": sorted(comparators),
        "unclassified_treatments": unclassified_treatments,
        "endpoints": sorted(endpoints),
        "outcome_evidence": outcome_sentences,
        "safety_evidence": safety_sentences,
        "semantic_relationships": sorted(set(relationship_paths)),
        "extraction_method": "DETERMINISTIC_EVIDENCE_V1",
        "review_status": "UNVALIDATED_EXTRACTION",
    }


def persist_evidence_intelligence(
    conn: sqlite3.Connection,
    tenant_id: str,
    document_id: str,
    chunk_id: str,
    text: str,
    created_at_utc: str,
) -> dict:
    structured = extract_evidence_intelligence(conn, text)
    structure_id = f"EVI_{uuid.uuid4().hex[:12].upper()}"
    conn.execute(
        """INSERT OR REPLACE INTO evidence_intelligence
           (structure_id, tenant_id, document_id, chunk_id, study_json,
            population_json, intervention_json, comparator_json, endpoint_json,
            outcome_json, safety_json, semantic_relationships_json,
            extraction_method, review_status, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            structure_id,
            tenant_id,
            document_id,
            chunk_id,
            json.dumps(structured["studies"], sort_keys=True),
            json.dumps(
                {
                    "indications": structured["population_indications"],
                    "context": structured["population_context"],
                },
                sort_keys=True,
            ),
            json.dumps(
                {
                    "interventions": structured["interventions"],
                    "unclassified_treatments": structured["unclassified_treatments"],
                },
                sort_keys=True,
            ),
            json.dumps(structured["comparators"], sort_keys=True),
            json.dumps(structured["endpoints"], sort_keys=True),
            json.dumps(structured["outcome_evidence"], sort_keys=True),
            json.dumps(structured["safety_evidence"], sort_keys=True),
            json.dumps(structured["semantic_relationships"], sort_keys=True),
            structured["extraction_method"],
            structured["review_status"],
            created_at_utc,
        ),
    )
    return {"structure_id": structure_id, **structured}


def get_evidence_intelligence(
    conn: sqlite3.Connection,
    tenant_id: str,
    chunk_id: str,
) -> dict | None:
    row = conn.execute(
        """SELECT * FROM evidence_intelligence
           WHERE tenant_id=? AND chunk_id=?""",
        (tenant_id, chunk_id),
    ).fetchone()
    if not row:
        return None
    decoded = _decoded_structure_row(row)
    state = get_review_state(conn, tenant_id, row["structure_id"])
    decoded["review_progress"] = {
        "required_fields": state["required_fields"],
        "reviewed_fields": state["reviewed_fields"],
        "missing_fields": state["missing_fields"],
        "can_finalize": state["can_finalize"],
    }
    decoded["validated_payload"] = state["validated_payload"]
    return decoded

def list_evidence_intelligence(
    conn: sqlite3.Connection,
    tenant_id: str,
    document_id: str | None = None,
) -> list[dict]:
    params: list[str] = [tenant_id]
    where = "WHERE e.tenant_id=?"
    if document_id:
        where += " AND e.document_id=?"
        params.append(document_id)
    rows = conn.execute(
        f"""SELECT e.*, d.file_name, c.page_number, c.chunk_sequence, c.chunk_text
            FROM evidence_intelligence e
            JOIN documents d
              ON d.document_id=e.document_id AND d.tenant_id=e.tenant_id
            JOIN document_chunks c
              ON c.chunk_id=e.chunk_id AND c.tenant_id=e.tenant_id
            {where}
            ORDER BY d.created_at_utc DESC, c.chunk_sequence""",
        tuple(params),
    ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "structure_id": row["structure_id"],
                "document_id": row["document_id"],
                "chunk_id": row["chunk_id"],
                "file_name": row["file_name"],
                "page_number": row["page_number"],
                "chunk_sequence": row["chunk_sequence"],
                "source_text": row["chunk_text"],
                "study": json.loads(row["study_json"]),
                "population": json.loads(row["population_json"]),
                "intervention": json.loads(row["intervention_json"]),
                "comparator": json.loads(row["comparator_json"]),
                "endpoint": json.loads(row["endpoint_json"]),
                "outcome": json.loads(row["outcome_json"]),
                "safety": json.loads(row["safety_json"]),
                "semantic_relationships": json.loads(row["semantic_relationships_json"]),
                "extraction_method": row["extraction_method"],
                "review_status": row["review_status"],
                "review_progress": {
                    "required_fields": get_review_state(
                        conn, tenant_id, row["structure_id"]
                    )["required_fields"],
                    "reviewed_fields": get_review_state(
                        conn, tenant_id, row["structure_id"]
                    )["reviewed_fields"],
                    "missing_fields": get_review_state(
                        conn, tenant_id, row["structure_id"]
                    )["missing_fields"],
                    "can_finalize": get_review_state(
                        conn, tenant_id, row["structure_id"]
                    )["can_finalize"],
                },
                "validated_payload": get_review_state(
                    conn, tenant_id, row["structure_id"]
                )["validated_payload"],
            }
        )
    return result
