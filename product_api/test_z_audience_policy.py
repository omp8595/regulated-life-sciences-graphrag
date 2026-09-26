from datetime import datetime, timedelta, timezone

from product_api.test_z_mlr_activation import ComposedClaimMlrActivationTests


class AudienceScopedGovernanceTests(ComposedClaimMlrActivationTests):
    # The parent workflow is already executed by its own test module. Reuse its
    # setup/helpers without duplicating that same test in this module.
    test_mlr_activation_controls_promotional_use_and_preserves_approved_wording = None

    def test_contextual_query_enforces_audience_scope_and_legacy_query_fails_closed(self):
        claim_id, submitter_key = self.build_medical_validated_claim()

        submitted = self.client.post(
            f"/v1/claims/medical-validated/{claim_id}/mlr-submit",
            headers=self.auth(submitter_key),
            json={
                "market": "US",
                "purpose": "PROMOTIONAL_CONTENT",
                "audience": "HCP",
                "rationale": "Submit the Medical-validated claim for an HCP-only US promotional approval.",
            },
        )
        self.assertEqual(submitted.status_code, 201)
        review_id = submitted.json()["review_id"]

        mlr_key = self.issue("audience_mlr_reviewer", "ROLE_MLR_REVIEWER")
        approved_wording = (
            "HCP-approved wording: ALPINE PFS evidence reports 78% versus 66% at 24 months."
        )
        decision = self.client.post(
            f"/v1/claims/mlr/reviews/{review_id}/decisions",
            headers=self.auth(mlr_key),
            json={
                "decision": "APPROVED_WITH_CHANGES",
                "rationale": "MLR approves this exact wording only for the requested HCP promotional scope.",
                "authorization_confirmed": True,
                "approved_wording": approved_wording,
                "conditions_of_use": "HCP promotional use only with the governed evidence citation package.",
                "effective_from_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "expires_at_utc": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            },
        )
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json()["audience"], "HCP")

        commercial_key = self.issue("audience_commercial_user", "ROLE_COMMERCIAL")
        auth = self.auth(commercial_key)

        # Backward-compatible legacy query has no audience. It must not guess that
        # an HCP-specific approval applies.
        legacy = self.client.post(
            "/v1/query",
            headers=auth,
            json={
                "question": "ALPINE PFS evidence 78% 66%",
                "purpose": "PROMOTIONAL_CONTENT",
                "market": "US",
            },
        )
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(legacy.json()["status"], "BLOCKED")

        hcp = self.client.post(
            "/v1/query/contextual",
            headers=auth,
            json={
                "question": "ALPINE PFS evidence 78% 66%",
                "purpose": "PROMOTIONAL_CONTENT",
                "market": "US",
                "audience": "HCP",
            },
        )
        self.assertEqual(hcp.status_code, 200)
        hcp_result = hcp.json()
        self.assertEqual(hcp_result["status"], "ANSWERED")
        self.assertEqual(hcp_result["answer"], approved_wording)
        self.assertEqual(hcp_result["audience"], "HCP")
        self.assertEqual(hcp_result["governed_claims"][0]["audience_scope"], "HCP")
        self.assertTrue(hcp_result["governed_claims"][0]["approved_wording_only"])

        patient = self.client.post(
            "/v1/query/contextual",
            headers=auth,
            json={
                "question": "ALPINE PFS evidence 78% 66%",
                "purpose": "PROMOTIONAL_CONTENT",
                "market": "US",
                "audience": "PATIENT",
            },
        )
        self.assertEqual(patient.status_code, 200)
        patient_result = patient.json()
        self.assertEqual(patient_result["status"], "BLOCKED")
        self.assertIn("MLR_AUDIENCE_MISMATCH", patient_result["block_reasons"])
        self.assertEqual(patient_result["audience"], "PATIENT")

        audit = self.client.get(
            "/v1/audit/verify",
            headers={
                "X-Tenant-ID": "tenant_a",
                "X-Actor-ID": "audience_audit_reader",
                "X-Role": "ROLE_REGULATORY",
            },
        ).json()
        self.assertTrue(audit["valid"])
