from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import uuid
from pathlib import Path

from product_api.app import (
    TenantContext,
    append_audit,
    connection,
    initialize_database,
    utc_now,
)


EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")
US_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PROMPT_INJECTION = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|system\s+prompt|"
    r"developer\s+message|reveal\s+(?:your|the)\s+prompt",
    re.I,
)


def _extract_pdf(path: Path) -> list[tuple[int | None, str]]:
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    document = pymupdf.open(path)
    try:
        return [(number + 1, page.get_text("text")) for number, page in enumerate(document)]
    finally:
        document.close()


def _extract_structured(path: Path) -> list[tuple[int | None, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".json":
        parsed = json.loads(raw)
        return [(None, json.dumps(parsed, ensure_ascii=False, indent=2))]
    if path.suffix.lower() == ".csv":
        rows = list(csv.reader(io.StringIO(raw)))
        return [(None, "\n".join(" | ".join(cell.strip() for cell in row) for row in rows))]
    return [(None, raw)]


def extract_document(path: Path) -> list[tuple[int | None, str]]:
    if path.suffix.lower() == ".pdf":
        if path.read_bytes()[:5] != b"%PDF-":
            raise ValueError("PDF signature is invalid")
        return _extract_pdf(path)
    return _extract_structured(path)


def chunk_pages(
    pages: list[tuple[int | None, str]], maximum_characters: int = 1200, overlap: int = 150
) -> list[tuple[int | None, str]]:
    chunks: list[tuple[int | None, str]] = []
    step = maximum_characters - overlap
    for page_number, text in pages:
        normalized = re.sub(r"\s+", " ", text).strip()
        for start in range(0, len(normalized), step):
            chunk = normalized[start : start + maximum_characters].strip()
            if chunk:
                chunks.append((page_number, chunk))
            if start + maximum_characters >= len(normalized):
                break
    return chunks


def detect_findings(text: str) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    patterns = [
        ("EMAIL", "MEDIUM", EMAIL),
        ("PHONE", "MEDIUM", PHONE),
        ("US_SSN", "HIGH", US_SSN),
        ("PROMPT_INJECTION", "HIGH", PROMPT_INJECTION),
    ]
    for finding_type, severity, pattern in patterns:
        matches = pattern.findall(text)
        if matches:
            # Store counts, never the sensitive matched values.
            findings.append((finding_type, severity, str(len(matches))))
    return findings


def process_ingestion_job(job_id: str, tenant_id: str, actor_id: str = "system_worker") -> dict:
    initialize_database()
    context = TenantContext(tenant_id=tenant_id, actor_id=actor_id, role="ROLE_SYSTEM_WORKER")
    with connection() as conn:
        job = conn.execute(
            "SELECT * FROM ingestion_jobs WHERE job_id=? AND tenant_id=?", (job_id, tenant_id)
        ).fetchone()
        if not job:
            raise LookupError("Ingestion job not found")
        document = conn.execute(
            "SELECT * FROM documents WHERE document_id=? AND tenant_id=?",
            (job["document_id"], tenant_id),
        ).fetchone()
        if not document:
            raise LookupError("Document not found")
        if job["status"] == "COMPLETED":
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM document_chunks WHERE tenant_id=? AND document_id=?",
                (tenant_id, document["document_id"]),
            ).fetchone()["n"]
            return {"job_id": job_id, "status": "COMPLETED", "chunks_created": count, "idempotent": True}

        now = utc_now()
        conn.execute(
            "UPDATE ingestion_jobs SET status='RUNNING', stage='SECURITY_SCAN', updated_at_utc=? WHERE job_id=?",
            (now, job_id),
        )
        try:
            path = Path(document["object_path"])
            if not path.is_file():
                raise FileNotFoundError("Stored object is missing")

            conn.execute(
                "UPDATE ingestion_jobs SET stage='TEXT_EXTRACTION', updated_at_utc=? WHERE job_id=?",
                (utc_now(), job_id),
            )
            pages = extract_document(path)
            complete_text = "\n".join(text for _, text in pages)
            if not complete_text.strip():
                raise ValueError("No readable text was extracted")

            conn.execute(
                "UPDATE ingestion_jobs SET stage='SENSITIVITY_CLASSIFICATION', updated_at_utc=? WHERE job_id=?",
                (utc_now(), job_id),
            )
            findings = detect_findings(complete_text)
            for finding_type, severity, count in findings:
                conn.execute(
                    "INSERT INTO document_findings VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"FND_{uuid.uuid4().hex[:12].upper()}", tenant_id, document["document_id"],
                        finding_type, severity, count, utc_now(),
                    ),
                )

            injection_detected = any(f[0] == "PROMPT_INJECTION" for f in findings)
            if injection_detected:
                conn.execute(
                    "UPDATE documents SET status='REJECTED_SECURITY' WHERE document_id=? AND tenant_id=?",
                    (document["document_id"], tenant_id),
                )
                conn.execute(
                    "UPDATE ingestion_jobs SET status='FAILED', stage='SECURITY_REJECTED', updated_at_utc=? WHERE job_id=?",
                    (utc_now(), job_id),
                )
                append_audit(conn, context, "DOCUMENT_SECURITY_REJECTED", document["document_id"], {"job_id": job_id})
                return {"job_id": job_id, "status": "FAILED", "stage": "SECURITY_REJECTED", "chunks_created": 0}

            conn.execute(
                "UPDATE ingestion_jobs SET stage='CHUNKING', updated_at_utc=? WHERE job_id=?",
                (utc_now(), job_id),
            )
            chunks = chunk_pages(pages)
            conn.execute(
                "DELETE FROM document_chunks WHERE tenant_id=? AND document_id=?",
                (tenant_id, document["document_id"]),
            )
            for sequence, (page_number, chunk_text) in enumerate(chunks, start=1):
                conn.execute(
                    "INSERT INTO document_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"CHK_{uuid.uuid4().hex[:12].upper()}", tenant_id, document["document_id"],
                        sequence, page_number, chunk_text, len(chunk_text), utc_now(),
                    ),
                )

            # No candidate becomes a claim here. Human validation remains mandatory.
            conn.execute(
                "UPDATE documents SET status='READY_FOR_SME_REVIEW' WHERE document_id=? AND tenant_id=?",
                (document["document_id"], tenant_id),
            )
            conn.execute(
                "UPDATE ingestion_jobs SET status='COMPLETED', stage='READY_FOR_SME_REVIEW', updated_at_utc=? WHERE job_id=?",
                (utc_now(), job_id),
            )
            audit_id = append_audit(
                conn, context, "DOCUMENT_INGESTED", document["document_id"],
                {"job_id": job_id, "chunks_created": len(chunks), "findings": [f[0] for f in findings]},
            )
            return {
                "job_id": job_id,
                "document_id": document["document_id"],
                "status": "COMPLETED",
                "stage": "READY_FOR_SME_REVIEW",
                "chunks_created": len(chunks),
                "finding_types": [f[0] for f in findings],
                "audit_id": audit_id,
                "idempotent": False,
            }
        except Exception as exc:
            conn.execute(
                "UPDATE documents SET status='INGESTION_FAILED' WHERE document_id=? AND tenant_id=?",
                (document["document_id"], tenant_id),
            )
            conn.execute(
                "UPDATE ingestion_jobs SET status='FAILED', stage='FAILED', updated_at_utc=? WHERE job_id=?",
                (utc_now(), job_id),
            )
            append_audit(conn, context, "DOCUMENT_INGESTION_FAILED", document["document_id"], {"job_id": job_id, "error_type": type(exc).__name__})
            raise


def process_next_job() -> dict | None:
    initialize_database()
    with connection() as conn:
        job = conn.execute(
            "SELECT job_id, tenant_id FROM ingestion_jobs WHERE status='QUEUED' ORDER BY created_at_utc LIMIT 1"
        ).fetchone()
    return process_ingestion_job(job["job_id"], job["tenant_id"]) if job else None
