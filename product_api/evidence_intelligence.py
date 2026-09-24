from __future__ import annotations

import json
import re
import sqlite3
import uuid

from product_api.semantic.store import resolve_mentions


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
            }
        )
    return result
