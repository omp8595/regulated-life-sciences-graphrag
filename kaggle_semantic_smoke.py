from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


DATA_DIR = Path(os.getenv("KAGGLE_SMOKE_DATA_DIR", "/kaggle/working/regulated_graphrag_smoke"))
if not Path("/kaggle/working").exists():
    DATA_DIR = Path(os.getenv("KAGGLE_SMOKE_DATA_DIR", "./.kaggle_smoke"))

shutil.rmtree(DATA_DIR, ignore_errors=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

os.environ["PRODUCT_DATA_DIR"] = str(DATA_DIR)
os.environ["PRODUCT_DB_PATH"] = str(DATA_DIR / "product.db")
os.environ["PLATFORM_ADMIN_KEY"] = "kaggle-local-admin-key"

from fastapi.testclient import TestClient  # noqa: E402

from product_api.app import app, connection  # noqa: E402
from product_api.semantic.store import get_concept, resolve_mentions  # noqa: E402
from product_api.worker import process_ingestion_job  # noqa: E402


TENANT = "kaggle_demo"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS | {message}")


def issue_key(client: TestClient, actor_id: str, role: str) -> str:
    response = client.post(
        "/v1/auth/api-keys",
        headers={"X-Platform-Admin-Key": "kaggle-local-admin-key"},
        json={"tenant_id": TENANT, "actor_id": actor_id, "role": role},
    )
    check(response.status_code == 201, f"API key issued for {role}")
    return response.json()["api_key"]


def main() -> None:
    print("\n=== Regulated Life Sciences GraphRAG | Kaggle Smoke Test ===\n")

    with TestClient(app) as client:
        tenant = client.post("/v1/tenants", json={"tenant_id": TENANT, "name": "Kaggle Demo Pharma"})
        check(tenant.status_code == 201, "tenant created")

        evidence_text = (
            "ALPINE evaluated zanubrutinib and ibrutinib in patients with relapsed or "
            "refractory chronic lymphocytic leukemia (CLL) or small lymphocytic lymphoma (SLL)."
        )
        upload = client.post(
            "/v1/documents",
            headers={"X-Tenant-ID": TENANT, "X-Actor-ID": "kaggle_loader", "X-Role": "ROLE_MEDICAL"},
            files={"file": ("alpine_kaggle_evidence.txt", evidence_text.encode(), "text/plain")},
            data={
                "market": "Global",
                "data_class": "MEDICAL_SCIENTIFIC_EVIDENCE",
                "sensitivity": "MEDICAL_ONLY",
            },
        )
        check(upload.status_code == 202, "evidence document accepted into quarantine")
        job = process_ingestion_job(upload.json()["job_id"], TENANT, actor_id="kaggle_worker")
        check(job["status"] == "COMPLETED", "ingestion completed")
        check(job["stage"] == "READY_FOR_SME_REVIEW", "document stopped at SME-review boundary")
        check(job["graph_edges_created"] > 0, "semantic graph edges created")

        medical_key = issue_key(client, "kaggle_medical", "ROLE_MEDICAL")
        query = client.post(
            "/v1/query",
            headers={"Authorization": f"Bearer {medical_key}"},
            json={"question": "BRUKINSA", "purpose": "MEDICAL_RESPONSE", "market": "Global", "top_k": 3},
        )
        check(query.status_code == 200, "governed query executed")
        query_result = query.json()
        check(query_result["status"] == "EVIDENCE_ONLY", "unvalidated evidence remains EVIDENCE_ONLY")
        check(query_result["result_count"] >= 1, "BRUKINSA retrieved molecule evidence")
        top_result = query_result["results"][0]
        check(top_result["lexical_score"] == 0.0, "brand query has zero exact lexical overlap")
        check(top_result["graph_score"] > 0.0, "semantic relationship supplied retrieval signal")
        check(
            "zanubrutinib" in top_result["semantic_related_matches"],
            "BRUKINSA expanded through BRAND_OF to zanubrutinib",
        )

        catalog = client.get(
            "/v1/semantic/concepts",
            headers={"Authorization": f"Bearer {medical_key}"},
        )
        check(catalog.status_code == 200, "semantic catalog endpoint available")
        concepts = {item["concept_id"]: item for item in catalog.json()}
        check("TRIAL:ALPINE" in concepts, "ALPINE canonical trial exists")
        check(
            any(
                mapping["system"] == "CLINICALTRIALS.GOV"
                and mapping["identifier"] == "NCT03734016"
                for mapping in concepts["TRIAL:ALPINE"]["external_mappings"]
            ),
            "ALPINE external trial mapping persisted",
        )

        steward_key = issue_key(client, "kaggle_steward", "ROLE_SEMANTIC_STEWARD")
        reviewer_key = issue_key(client, "kaggle_regulatory", "ROLE_REGULATORY")

        proposal = client.post(
            "/v1/semantic/change-requests",
            headers={"Authorization": f"Bearer {steward_key}"},
            json={
                "change_type": "ADD_ALIAS",
                "concept_id": "BRAND:BRUKINSA",
                "payload": {
                    "alias": "Brukinsa oncology brand",
                    "source": "KAGGLE_VALIDATION",
                    "confidence": 0.95,
                },
                "rationale": "Validate controlled semantic alias governance in the Kaggle smoke test.",
            },
        )
        check(proposal.status_code == 201, "semantic alias change proposed")
        change_id = proposal.json()["change_request_id"]

        self_approval = client.post(
            f"/v1/semantic/change-requests/{change_id}/decisions",
            headers={"Authorization": f"Bearer {steward_key}"},
            json={
                "decision": "APPROVED",
                "rationale": "Attempting self approval to validate segregation of duties control.",
                "authorization_confirmed": True,
            },
        )
        check(self_approval.status_code == 403, "self-approval blocked")

        approval = client.post(
            f"/v1/semantic/change-requests/{change_id}/decisions",
            headers={"Authorization": f"Bearer {reviewer_key}"},
            json={
                "decision": "APPROVED",
                "rationale": "Independent regulatory review approves this test semantic alias.",
                "authorization_confirmed": True,
            },
        )
        check(approval.status_code == 200, "independent semantic approval accepted")
        check(approval.json()["applied_concept_version"] == 2, "semantic concept version advanced to v2")

        with connection() as conn:
            current = get_concept(conn, "BRAND:BRUKINSA")
            check(current is not None and current["version"] == 2, "new semantic version is ACTIVE")
            versions = conn.execute(
                "SELECT version, status FROM semantic_concepts WHERE concept_id=? ORDER BY version",
                ("BRAND:BRUKINSA",),
            ).fetchall()
            check(
                [(row["version"], row["status"]) for row in versions]
                == [(1, "SUPERSEDED"), (2, "ACTIVE")],
                "previous semantic version retained as SUPERSEDED",
            )
            resolved = resolve_mentions(conn, "Brukinsa oncology brand")
            check(
                "BRAND:BRUKINSA" in {item.concept_id for item in resolved},
                "new approved alias resolves through the active master",
            )

        audit = client.get(
            "/v1/audit/verify",
            headers={"X-Tenant-ID": TENANT, "X-Actor-ID": "kaggle_auditor", "X-Role": "ROLE_REGULATORY"},
        )
        check(audit.status_code == 200, "audit verification endpoint available")
        check(audit.json()["valid"] is True, "hash-linked audit chain valid")

        summary = {
            "ingestion": {
                "status": job["status"],
                "stage": job["stage"],
                "graph_edges_created": job["graph_edges_created"],
            },
            "semantic_retrieval": {
                "query": "BRUKINSA",
                "status": query_result["status"],
                "lexical_score": top_result["lexical_score"],
                "semantic_score": top_result["graph_score"],
                "related_matches": top_result["semantic_related_matches"],
            },
            "semantic_governance": {
                "change_request_id": change_id,
                "self_approval_blocked": self_approval.status_code == 403,
                "applied_version": approval.json()["applied_concept_version"],
            },
            "audit": audit.json(),
        }

        print("\n=== SMOKE TEST SUMMARY ===")
        print(json.dumps(summary, indent=2))
        print("\nALL KAGGLE SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
