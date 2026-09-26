from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from product_api import test_product_api as base
from product_api.mlr_activation import ensure_mlr_activation_schema


class ComposedClaimMlrActivationTests(base.unittest.TestCase):
    def setUp(self):
        self.client = TestClient(base.app)
        self.client.__enter__()
        ensure_mlr_activation_schema()
        with base.connection() as conn:
            for table in (
                "composed_claim_activations",
                "composed_claim_mlr_decisions",
                "composed_claim_mlr_reviews",
                "semantic_change_decisions",
                "semantic_change_requests",
                "mlr_review_decisions",
                "mlr_review_queue",
                "governed_claim_evidence",
                "governed_claims",
                "sme_review_decisions",
                "candidate_claims",
                "graph_edges",
                "graph_nodes",
                "document_findings",
                "medical_validated_composed_claims",
                "composed_claim_review_decisions",
                "composed_claim_candidates",
                "evidence_intelligence_validations",
                "evidence_intelligence_review_decisions",
                "evidence_intelligence",
                "document_chunks",
                "audit_events",
                "api_principals",
                "ingestion_jobs",
                "documents",
                "tenants",
            ):
                conn.execute(f"DELETE FROM {table}")
        for tenant in ("tenant_a", "tenant_b"):
            self.client.post("/v1/tenants", json={"tenant_id": tenant, "name": tenant})

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def issue(self, actor: str, role: str) -> str:
        return self.client.post(
            "/v1/auth/api-keys",
            headers={"X-Platform-Admin-Key": "test-platform-admin-key"},
            json={"tenant_id": "tenant_a", "actor_id": actor, "role": role},
        ).json()["api_key"]

    @staticmethod
    def auth(key: str) -> dict:
        return {"Authorization": f"Bearer {key}"}

    def build_medical_validated_claim(self) -> tuple[str, str]:
        content = (
            b"ALPINE evaluated zanubrutinib and ibrutinib in patients with relapsed or refractory "
            b"chronic lymphocytic leukemia (CLL). Progression-free survival (PFS) was 78% versus "
            b"66% at 24 months. Atrial fibrillation occurred in 5% versus 13%."
        )
        upload = self.client.post(
            "/v1/documents",
            headers=base.ProductApiTests.headers("tenant_a"),
            files={"file": ("mlr_source.txt", content, "text/plain")},
            data={
                "market": "Global",
                "data_class": "MEDICAL_SCIENTIFIC_EVIDENCE",
                "sensitivity": "MEDICAL_ONLY",
            },
        ).json()
        base.process_ingestion_job(upload["job_id"], "tenant_a")

        evidence_key = self.issue("evidence_sme", "ROLE_MEDICAL")
        evidence_auth = self.auth(evidence_key)
        queue = self.client.get("/v1/evidence/review-queue", headers=evidence_auth).json()
        structure_id = queue[0]["structure_id"]
        state = self.client.get(
            f"/v1/evidence/intelligence/{structure_id}/reviews",
            headers=evidence_auth,
        ).json()
        for field_name in state["required_fields"]:
            response = self.client.post(
                f"/v1/evidence/intelligence/{structure_id}/reviews",
                headers=evidence_auth,
                json={
                    "field_name": field_name,
                    "decision": "VERIFIED",
                    "reviewed_value": None,
                    "rationale": f"Scientific SME verified {field_name} against the source evidence.",
                },
            )
            self.assertEqual(response.status_code, 201)
        finalized = self.client.post(
            f"/v1/evidence/intelligence/{structure_id}/finalize",
            headers=evidence_auth,
            json={
                "rationale": "All populated scientific evidence fields were verified against source evidence.",
                "authorization_confirmed": True,
            },
        )
        self.assertEqual(finalized.status_code, 200)

        composer_key = self.issue("claim_composer", "ROLE_MEDICAL")
        composed = self.client.post(
            "/v1/claims/compose",
            headers=self.auth(composer_key),
            json={"structure_id": structure_id, "claim_kind": "EFFICACY_ENDPOINT"},
        )
        self.assertEqual(composed.status_code, 201)
        candidate_id = composed.json()["candidate_id"]

        medical_key = self.issue("medical_claim_reviewer", "ROLE_REGULATORY")
        reviewed = self.client.post(
            f"/v1/claims/composed-candidates/{candidate_id}/decisions",
            headers=self.auth(medical_key),
            json={
                "decision": "VALIDATED",
                "rationale": "Independent Medical review confirms the candidate wording is evidence-bound.",
                "authorization_confirmed": True,
            },
        )
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.json()["approval_status"], "NOT_MLR_REVIEWED")
        return reviewed.json()["claim_id"], medical_key

    def test_mlr_activation_controls_promotional_use_and_preserves_approved_wording(self):
        claim_id, submitter_key = self.build_medical_validated_claim()

        commercial_key = self.issue("commercial_user", "ROLE_COMMERCIAL")
        before_mlr = self.client.post(
            "/v1/query",
            headers=self.auth(commercial_key),
            json={
                "question": "ALPINE PFS 78% 66%",
                "purpose": "PROMOTIONAL_CONTENT",
                "market": "US",
            },
        )
        self.assertEqual(before_mlr.status_code, 200)
        self.assertEqual(before_mlr.json()["status"], "BLOCKED")

        medical_query = self.client.post(
            "/v1/query",
            headers=self.auth(submitter_key),
            json={
                "question": "ALPINE PFS 78% 66%",
                "purpose": "MEDICAL_RESPONSE",
                "market": "Global",
            },
        )
        self.assertEqual(medical_query.status_code, 200)
        self.assertEqual(medical_query.json()["status"], "ANSWERED")
        self.assertEqual(
            medical_query.json()["governed_claims"][0]["claim_source"],
            "EVIDENCE_BOUND_COMPOSED_CLAIM",
        )

        submitted = self.client.post(
            f"/v1/claims/medical-validated/{claim_id}/mlr-submit",
            headers=self.auth(submitter_key),
            json={
                "market": "US",
                "purpose": "PROMOTIONAL_CONTENT",
                "audience": "ALL",
                "rationale": "Submit this Medical-validated evidence-bound claim for US promotional MLR review.",
            },
        )
        self.assertEqual(submitted.status_code, 201)
        self.assertEqual(submitted.json()["review_status"], "SUBMITTED_FOR_MLR")
        review_id = submitted.json()["review_id"]

        mlr_key = self.issue("mlr_reviewer", "ROLE_MLR_REVIEWER")
        reviews = self.client.get(
            "/v1/claims/mlr/reviews",
            headers=self.auth(mlr_key),
        )
        self.assertEqual(reviews.status_code, 200)
        self.assertEqual(reviews.json()[0]["claim_id"], claim_id)
        self.assertEqual(reviews.json()[0]["purpose"], "PROMOTIONAL_CONTENT")
        self.assertIn("validation_id", reviews.json()[0]["support"])

        same_actor_mlr_key = self.issue("medical_claim_reviewer", "ROLE_MLR_REVIEWER")
        self_review = self.client.post(
            f"/v1/claims/mlr/reviews/{review_id}/decisions",
            headers=self.auth(same_actor_mlr_key),
            json={
                "decision": "APPROVED",
                "rationale": "This deliberately tests the independent MLR reviewer control boundary.",
                "authorization_confirmed": True,
                "effective_from_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "expires_at_utc": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(self_review.status_code, 403)

        missing_confirmation = self.client.post(
            f"/v1/claims/mlr/reviews/{review_id}/decisions",
            headers=self.auth(mlr_key),
            json={
                "decision": "APPROVED",
                "rationale": "The evidence and Medical review support approval within the requested scope.",
                "authorization_confirmed": False,
                "effective_from_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "expires_at_utc": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(missing_confirmation.status_code, 403)

        approved_wording = (
            "MLR-approved controlled wording: ALPINE PFS evidence reports 78% versus 66% at 24 months."
        )
        decision = self.client.post(
            f"/v1/claims/mlr/reviews/{review_id}/decisions",
            headers=self.auth(mlr_key),
            json={
                "decision": "APPROVED_WITH_CHANGES",
                "rationale": "MLR approved revised controlled wording for the requested US promotional scope.",
                "authorization_confirmed": True,
                "approved_wording": approved_wording,
                "conditions_of_use": "Use only this approved wording with the governed evidence citation package.",
                "effective_from_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "expires_at_utc": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json()["approval_status"], "MLR_APPROVED")
        self.assertTrue(decision.json()["activation_id"].startswith("CACT_"))
        self.assertEqual(decision.json()["approved_wording"], approved_wording)

        activations = self.client.get(
            "/v1/claims/activations",
            headers=self.auth(mlr_key),
        )
        self.assertEqual(activations.status_code, 200)
        self.assertEqual(activations.json()[0]["runtime_status"], "ACTIVE_FOR_GOVERNED_USE")
        self.assertEqual(activations.json()[0]["support"]["validation_id"], reviews.json()[0]["support"]["validation_id"])

        promotional = self.client.post(
            "/v1/query",
            headers=self.auth(commercial_key),
            json={
                "question": "ALPINE PFS evidence 78% 66%",
                "purpose": "PROMOTIONAL_CONTENT",
                "market": "US",
            },
        )
        self.assertEqual(promotional.status_code, 200)
        result = promotional.json()
        self.assertEqual(result["status"], "ANSWERED")
        self.assertEqual(result["answer"], approved_wording)
        self.assertTrue(result["governed_claims"][0]["approved_wording_only"])
        self.assertEqual(result["governed_claims"][0]["purpose_scope"], "PROMOTIONAL_CONTENT")
        self.assertEqual(result["governed_claims"][0]["market"], "US")

        wrong_market = self.client.post(
            "/v1/query",
            headers=self.auth(commercial_key),
            json={
                "question": "ALPINE PFS evidence 78% 66%",
                "purpose": "PROMOTIONAL_CONTENT",
                "market": "India",
            },
        ).json()
        self.assertEqual(wrong_market["status"], "BLOCKED")

        with base.connection() as conn:
            conn.execute(
                """UPDATE composed_claim_activations
                   SET expires_at_utc=? WHERE tenant_id=? AND claim_id=?""",
                (
                    (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                    "tenant_a",
                    claim_id,
                ),
            )

        expired = self.client.post(
            "/v1/query",
            headers=self.auth(commercial_key),
            json={
                "question": "ALPINE PFS evidence 78% 66%",
                "purpose": "PROMOTIONAL_CONTENT",
                "market": "US",
            },
        ).json()
        self.assertEqual(expired["status"], "BLOCKED")
        self.assertIn("MLR_APPROVAL_SCOPE_OR_VALIDITY_MISMATCH", expired["block_reasons"])

        audit = self.client.get(
            "/v1/audit/verify",
            headers={
                "X-Tenant-ID": "tenant_a",
                "X-Actor-ID": "audit_reader",
                "X-Role": "ROLE_REGULATORY",
            },
        ).json()
        self.assertTrue(audit["valid"])
