from __future__ import annotations

import json
import sqlite3
import uuid


CLAIM_KINDS = {"EFFICACY_ENDPOINT", "SAFETY"}


def _join(values: list[str]) -> str:
    return ", ".join(value for value in values if value)


def _population_text(population: dict) -> str:
    indications = population.get("indications") or []
    if indications:
        return _join(indications)
    context = population.get("context") or []
    return context[0] if context else "the validated study population"


def _claim_support(
    conn: sqlite3.Connection,
    tenant_id: str,
    structure_id: str,
    validation_id: str,
    validated: dict,
    claim_kind: str,
) -> dict:
    row = conn.execute(
        """SELECT e.document_id, e.chunk_id, d.file_name, c.page_number, c.chunk_text
           FROM evidence_intelligence e
           JOIN documents d
             ON d.document_id=e.document_id AND d.tenant_id=e.tenant_id
           JOIN document_chunks c
             ON c.chunk_id=e.chunk_id AND c.tenant_id=e.tenant_id
           WHERE e.tenant_id=? AND e.structure_id=?""",
        (tenant_id, structure_id),
    ).fetchone()
    if not row:
        raise LookupError("Evidence source lineage not found")

    cited_fields = {
        "study": validated.get("study") or [],
        "population": validated.get("population") or {},
        "intervention": validated.get("intervention") or {},
        "comparator": validated.get("comparator") or [],
    }
    if claim_kind == "EFFICACY_ENDPOINT":
        cited_fields["endpoint"] = validated.get("endpoint") or []
        cited_fields["outcome"] = validated.get("outcome") or []
    elif claim_kind == "SAFETY":
        cited_fields["safety"] = validated.get("safety") or []

    return {
        "structure_id": structure_id,
        "validation_id": validation_id,
        "document_id": row["document_id"],
        "chunk_id": row["chunk_id"],
        "file_name": row["file_name"],
        "page_number": row["page_number"],
        "source_text": row["chunk_text"],
        "claim_kind": claim_kind,
        "validated_fields": cited_fields,
    }


def _compose_text(validated: dict, claim_kind: str) -> str:
    studies = validated.get("study") or []
    interventions = (validated.get("intervention") or {}).get("interventions") or []
    comparators = validated.get("comparator") or []
    population = _population_text(validated.get("population") or {})

    if not studies:
        raise ValueError("A validated study is required for claim composition")
    if not interventions:
        raise ValueError("A validated intervention is required for claim composition")

    study = studies[0]
    intervention = _join(interventions)
    comparator = _join(comparators)

    if claim_kind == "EFFICACY_ENDPOINT":
        endpoints = validated.get("endpoint") or []
        outcomes = validated.get("outcome") or []
        if not endpoints or not outcomes:
            raise ValueError("Validated endpoint and outcome evidence are required")
        comparator_phrase = f" against {comparator}" if comparator else ""
        return (
            f"In {study}, {intervention} was evaluated{comparator_phrase} in {population}. "
            f"The validated source reports the {_join(endpoints)} outcome as: {outcomes[0]}"
        )

    if claim_kind == "SAFETY":
        safety = validated.get("safety") or []
        if not safety:
            raise ValueError("Validated safety evidence is required")
        comparator_phrase = f" and {comparator}" if comparator else ""
        return (
            f"In {study}, validated safety evidence for {intervention}{comparator_phrase} "
            f"in {population} includes: {safety[0]}"
        )

    raise ValueError(f"Unsupported claim kind: {claim_kind}")


def list_composition_sources(
    conn: sqlite3.Connection,
    tenant_id: str,
) -> list[dict]:
    rows = conn.execute(
        """SELECT e.structure_id, e.document_id, e.chunk_id, e.review_status,
                  v.validation_id, v.validated_payload_json, v.validated_by,
                  v.validated_role, v.created_at_utc, d.file_name, c.page_number
           FROM evidence_intelligence e
           JOIN evidence_intelligence_validations v
             ON v.structure_id=e.structure_id AND v.tenant_id=e.tenant_id
           JOIN documents d
             ON d.document_id=e.document_id AND d.tenant_id=e.tenant_id
           JOIN document_chunks c
             ON c.chunk_id=e.chunk_id AND c.tenant_id=e.tenant_id
           WHERE e.tenant_id=? AND e.review_status='SME_VALIDATED_EVIDENCE'
           ORDER BY v.created_at_utc DESC""",
        (tenant_id,),
    ).fetchall()
    return [
        {
            "structure_id": row["structure_id"],
            "validation_id": row["validation_id"],
            "document_id": row["document_id"],
            "chunk_id": row["chunk_id"],
            "file_name": row["file_name"],
            "page_number": row["page_number"],
            "review_status": row["review_status"],
            "validated_by": row["validated_by"],
            "validated_role": row["validated_role"],
            "validated_at_utc": row["created_at_utc"],
            "validated_payload": json.loads(row["validated_payload_json"]),
        }
        for row in rows
    ]


def compose_claim_candidate(
    conn: sqlite3.Connection,
    tenant_id: str,
    structure_id: str,
    claim_kind: str,
    actor_id: str,
    actor_role: str,
    created_at_utc: str,
) -> dict:
    if claim_kind not in CLAIM_KINDS:
        raise ValueError(f"Unsupported claim kind: {claim_kind}")

    row = conn.execute(
        """SELECT e.review_status, v.validation_id, v.validated_payload_json
           FROM evidence_intelligence e
           LEFT JOIN evidence_intelligence_validations v
             ON v.structure_id=e.structure_id AND v.tenant_id=e.tenant_id
           WHERE e.tenant_id=? AND e.structure_id=?""",
        (tenant_id, structure_id),
    ).fetchone()
    if not row:
        raise LookupError("Evidence intelligence record not found")
    if row["review_status"] != "SME_VALIDATED_EVIDENCE" or not row["validation_id"]:
        raise ValueError("Only SME_VALIDATED_EVIDENCE can be used for claim composition")

    existing = conn.execute(
        """SELECT candidate_id FROM composed_claim_candidates
           WHERE tenant_id=? AND structure_id=? AND validation_id=?
             AND claim_kind=? AND status IN ('PENDING_MEDICAL_REVIEW','VALIDATED')""",
        (tenant_id, structure_id, row["validation_id"], claim_kind),
    ).fetchone()
    if existing:
        raise ValueError(f"An active {claim_kind} claim candidate already exists")

    validated = json.loads(row["validated_payload_json"])
    claim_text = _compose_text(validated, claim_kind)
    support = _claim_support(
        conn,
        tenant_id,
        structure_id,
        row["validation_id"],
        validated,
        claim_kind,
    )
    candidate_id = f"CCAND_{uuid.uuid4().hex[:12].upper()}"
    conn.execute(
        """INSERT INTO composed_claim_candidates
           (candidate_id, tenant_id, structure_id, validation_id, claim_kind,
            claim_text, support_json, status, created_by, created_role, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING_MEDICAL_REVIEW', ?, ?, ?)""",
        (
            candidate_id,
            tenant_id,
            structure_id,
            row["validation_id"],
            claim_kind,
            claim_text,
            json.dumps(support, sort_keys=True),
            actor_id,
            actor_role,
            created_at_utc,
        ),
    )
    return {
        "candidate_id": candidate_id,
        "structure_id": structure_id,
        "validation_id": row["validation_id"],
        "claim_kind": claim_kind,
        "claim_text": claim_text,
        "support": support,
        "status": "PENDING_MEDICAL_REVIEW",
    }


def list_composed_claim_candidates(
    conn: sqlite3.Connection,
    tenant_id: str,
    status: str | None = None,
) -> list[dict]:
    params: list[str] = [tenant_id]
    where = "WHERE tenant_id=?"
    if status:
        where += " AND status=?"
        params.append(status)
    rows = conn.execute(
        f"""SELECT * FROM composed_claim_candidates
            {where}
            ORDER BY created_at_utc DESC""",
        tuple(params),
    ).fetchall()
    return [
        {
            "candidate_id": row["candidate_id"],
            "structure_id": row["structure_id"],
            "validation_id": row["validation_id"],
            "claim_kind": row["claim_kind"],
            "claim_text": row["claim_text"],
            "support": json.loads(row["support_json"]),
            "status": row["status"],
            "created_by": row["created_by"],
            "created_role": row["created_role"],
            "created_at_utc": row["created_at_utc"],
        }
        for row in rows
    ]


def review_composed_claim_candidate(
    conn: sqlite3.Connection,
    tenant_id: str,
    candidate_id: str,
    decision: str,
    rationale: str,
    reviewer_actor_id: str,
    reviewer_role: str,
    created_at_utc: str,
) -> dict:
    if decision not in {"VALIDATED", "REJECTED", "NEEDS_REVISION"}:
        raise ValueError(f"Unsupported claim review decision: {decision}")

    candidate = conn.execute(
        """SELECT * FROM composed_claim_candidates
           WHERE tenant_id=? AND candidate_id=?""",
        (tenant_id, candidate_id),
    ).fetchone()
    if not candidate:
        raise LookupError("Composed claim candidate not found")
    if candidate["status"] != "PENDING_MEDICAL_REVIEW":
        raise ValueError("Composed claim candidate has already been decided")
    if candidate["created_by"] == reviewer_actor_id:
        raise ValueError("Composed claims require independent Medical review")

    decision_id = f"CCREV_{uuid.uuid4().hex[:12].upper()}"
    conn.execute(
        """INSERT INTO composed_claim_review_decisions
           (decision_id, tenant_id, candidate_id, reviewer_actor_id,
            reviewer_role, decision, rationale, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            decision_id,
            tenant_id,
            candidate_id,
            reviewer_actor_id,
            reviewer_role,
            decision,
            rationale,
            created_at_utc,
        ),
    )
    conn.execute(
        """UPDATE composed_claim_candidates SET status=?
           WHERE tenant_id=? AND candidate_id=?""",
        (decision, tenant_id, candidate_id),
    )

    claim_id = None
    approval_status = None
    if decision == "VALIDATED":
        claim_id = f"CCLM_{uuid.uuid4().hex[:12].upper()}"
        approval_status = "NOT_MLR_REVIEWED"
        conn.execute(
            """INSERT INTO medical_validated_composed_claims
               (claim_id, tenant_id, candidate_id, claim_kind, claim_text,
                support_json, status, approval_status, validated_by,
                validated_role, validated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?)""",
            (
                claim_id,
                tenant_id,
                candidate_id,
                candidate["claim_kind"],
                candidate["claim_text"],
                candidate["support_json"],
                approval_status,
                reviewer_actor_id,
                reviewer_role,
                created_at_utc,
            ),
        )

    return {
        "decision_id": decision_id,
        "candidate_id": candidate_id,
        "decision": decision,
        "claim_id": claim_id,
        "approval_status": approval_status,
    }


def list_medical_validated_composed_claims(
    conn: sqlite3.Connection,
    tenant_id: str,
) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM medical_validated_composed_claims
           WHERE tenant_id=?
           ORDER BY validated_at_utc DESC""",
        (tenant_id,),
    ).fetchall()
    return [
        {
            "claim_id": row["claim_id"],
            "candidate_id": row["candidate_id"],
            "claim_kind": row["claim_kind"],
            "claim_text": row["claim_text"],
            "support": json.loads(row["support_json"]),
            "status": row["status"],
            "approval_status": row["approval_status"],
            "validated_by": row["validated_by"],
            "validated_role": row["validated_role"],
            "validated_at_utc": row["validated_at_utc"],
        }
        for row in rows
    ]
