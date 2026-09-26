from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import gradio as gr
from fastapi.testclient import TestClient

from product_api.app import app
from product_api.audience_query import register_audience_query_routes
from product_api.mlr_activation import register_mlr_activation_routes
from product_ui import (
    _bearer,
    _result,
    _workspace_evidence_rows,
    _workspace_provenance_rows,
    _workspace_scientific_rows,
    _workspace_summary,
    compose_claim_ui,
    finalize_evidence_review_ui,
    governance_status,
    record_evidence_field_review,
    refresh_claim_composition_sources,
    refresh_composed_claim_candidates,
    refresh_evidence_review,
    refresh_evidence_review_state,
    refresh_medical_validated_claims,
    review_composed_claim_ui,
    upload_and_process,
)


register_mlr_activation_routes(app)
register_audience_query_routes(app)


def _client() -> TestClient:
    return TestClient(app)


def run_contextual_query(api_key, question, purpose, market, audience, top_k):
    result = _result(
        _client().post(
            "/v1/query/contextual",
            headers=_bearer(api_key),
            json={
                "question": question,
                "purpose": purpose,
                "market": market,
                "audience": audience,
                "top_k": int(top_k),
            },
        )
    )
    summary = _workspace_summary(result, question)
    summary += f"\n**Audience:** {result.get('audience', audience)}\n"
    if result.get("block_reasons"):
        summary += "\n**Policy block reasons**\n" + "\n".join(
            f"- {reason}" for reason in result["block_reasons"]
        )
    return (
        summary,
        _workspace_scientific_rows(result),
        _workspace_evidence_rows(result),
        _workspace_provenance_rows(result),
        result,
    )


def refresh_mlr_submission_claims(api_key):
    claims = _result(
        _client().get("/v1/claims/medical-validated", headers=_bearer(api_key))
    )
    choices = [claim["claim_id"] for claim in claims if claim.get("status") == "ACTIVE"]
    return gr.update(choices=choices, value=choices[0] if choices else None), claims


def submit_composed_claim_mlr(api_key, claim_id, market, purpose, audience, rationale):
    if not claim_id:
        raise gr.Error("Select a Medical-validated claim.")
    return _result(
        _client().post(
            f"/v1/claims/medical-validated/{claim_id}/mlr-submit",
            headers=_bearer(api_key),
            json={
                "market": market,
                "purpose": purpose,
                "audience": audience,
                "rationale": rationale,
            },
        )
    )


def refresh_composed_mlr_reviews(api_key):
    reviews = _result(
        _client().get("/v1/claims/mlr/reviews", headers=_bearer(api_key))
    )
    choices = [review["review_id"] for review in reviews]
    return gr.update(choices=choices, value=choices[0] if choices else None), reviews


def decide_composed_mlr(
    api_key,
    review_id,
    decision,
    rationale,
    approved_wording,
    conditions,
    days_valid,
    confirmed,
):
    if not review_id:
        raise gr.Error("Select a composed-claim MLR review.")
    now = datetime.now(timezone.utc)
    payload = {
        "decision": decision,
        "rationale": rationale,
        "authorization_confirmed": confirmed,
        "approved_wording": approved_wording or None,
        "conditions_of_use": conditions or None,
        "effective_from_utc": None,
        "expires_at_utc": None,
    }
    if decision in {"APPROVED", "APPROVED_WITH_CHANGES"}:
        payload["effective_from_utc"] = (now - timedelta(minutes=1)).isoformat()
        payload["expires_at_utc"] = (now + timedelta(days=int(days_valid))).isoformat()
    return _result(
        _client().post(
            f"/v1/claims/mlr/reviews/{review_id}/decisions",
            headers=_bearer(api_key),
            json=payload,
        )
    )


def refresh_composed_activations(api_key):
    return _result(
        _client().get("/v1/claims/activations", headers=_bearer(api_key))
    )


CSS = """
.hero {background:linear-gradient(120deg,#10243e,#173f5f);padding:24px;border-radius:16px;color:white;}
.notice {border-left:5px solid #ff7518;padding:12px 16px;background:#fff8ef;}
.step {font-weight:700;font-size:1.05rem;margin-top:8px;}
.gradio-container {max-width:1500px !important;}
"""


def build_journey_ui():
    with gr.Blocks(title="Governed Pharma Evidence Journey", css=CSS) as demo:
        gr.HTML(
            """<div class='hero'><h1>Governed Pharma Evidence & Context Platform</h1>
            <p>Source → semantic context → structured evidence → SME validation → evidence-bound claim → Medical validation → MLR → governed use.</p></div>"""
        )
        gr.HTML(
            "<div class='notice'><b>Prototype notice:</b> Scientific and MLR state transitions must only be made by genuinely authorized reviewers.</div>"
        )
        api_key = gr.Textbox(label="API key", type="password", placeholder="rgp_…")

        with gr.Tab("0 · Ingest evidence"):
            source = gr.File(
                label="Scientific source",
                type="filepath",
                file_types=[".pdf", ".txt", ".md", ".csv", ".json"],
            )
            with gr.Row():
                ingest_market = gr.Dropdown(
                    ["Global", "US", "India", "China", "European Union"],
                    value="Global",
                    label="Source market",
                )
                data_class = gr.Dropdown(
                    [
                        "MEDICAL_SCIENTIFIC_EVIDENCE",
                        "PUBLIC_PRODUCT_INFORMATION",
                        "APPROVED_LABEL_CONTENT",
                        "REGULATORY_INFORMATION",
                        "CLINICAL_RESTRICTED",
                    ],
                    value="MEDICAL_SCIENTIFIC_EVIDENCE",
                    label="Data class",
                )
                sensitivity = gr.Dropdown(
                    ["PUBLIC", "CONTROLLED", "MEDICAL_ONLY", "RESTRICTED"],
                    value="MEDICAL_ONLY",
                    label="Sensitivity",
                )
            ingest = gr.Button("Upload and process", variant="primary")
            ingest_result = gr.JSON(label="Ingestion result")
            ingest.click(
                upload_and_process,
                [api_key, source, ingest_market, data_class, sensitivity],
                ingest_result,
            )

        with gr.Tab("1 · Governed query"):
            gr.Markdown(
                "Runtime policy evaluates **role + purpose + market + audience + validation/approval state** before returning a governed answer."
            )
            question = gr.Textbox(
                label="Question",
                lines=3,
                placeholder="What evidence supports BRUKINSA in relapsed/refractory CLL?",
            )
            with gr.Row():
                purpose = gr.Dropdown(
                    [
                        "MEDICAL_RESPONSE",
                        "INTERNAL_INSIGHT",
                        "PROMOTIONAL_CONTENT",
                        "CLINICAL_ANALYSIS",
                        "REGULATORY_ANALYSIS",
                    ],
                    value="MEDICAL_RESPONSE",
                    label="Purpose",
                )
                market = gr.Dropdown(
                    ["Global", "US", "India", "China", "European Union"],
                    value="Global",
                    label="Market",
                )
                audience = gr.Dropdown(
                    ["ALL", "HCP", "PATIENT", "PAYER", "INTERNAL"],
                    value="ALL",
                    label="Audience",
                )
                top_k = gr.Slider(1, 10, value=3, step=1, label="Results")
            ask = gr.Button("Ask governed platform", variant="primary")
            summary = gr.Markdown(label="Governed response")
            scientific = gr.Dataframe(
                headers=[
                    "Study",
                    "Population / indication",
                    "Population context",
                    "Intervention",
                    "Comparator",
                    "Endpoint",
                    "Outcome evidence",
                    "Safety evidence",
                    "Extraction review status",
                    "Extraction method",
                ],
                datatype=["str"] * 10,
                interactive=False,
                wrap=True,
                label="Structured scientific evidence",
            )
            evidence = gr.Dataframe(
                headers=[
                    "Type",
                    "Evidence / governed claim",
                    "Source file",
                    "Approval state",
                    "Usage condition",
                    "Relevance",
                    "Direct semantic matches",
                    "Related semantic matches",
                ],
                datatype=["str", "str", "str", "str", "str", "number", "str", "str"],
                interactive=False,
                wrap=True,
                label="Evidence and governance",
            )
            provenance = gr.Dataframe(
                headers=[
                    "Source file",
                    "Document ID",
                    "Chunk ID",
                    "Page",
                    "Market",
                    "Data class",
                    "Approval state",
                    "Usage condition",
                ],
                datatype=["str"] * 8,
                interactive=False,
                wrap=True,
                label="Provenance and lineage",
            )
            with gr.Accordion("Raw policy decision", open=False):
                raw = gr.JSON(label="Raw contextual query response")
            ask.click(
                run_contextual_query,
                [api_key, question, purpose, market, audience, top_k],
                [summary, scientific, evidence, provenance, raw],
            )

        with gr.Tab("2 · Evidence SME review"):
            gr.Markdown(
                "Machine extraction remains unvalidated until an authorized SME verifies, corrects or rejects every populated scientific field."
            )
            refresh_evidence = gr.Button("Refresh evidence review queue")
            structure_id = gr.Dropdown([], label="Evidence structure")
            evidence_queue = gr.JSON(label="Pending evidence")
            refresh_evidence.click(
                refresh_evidence_review,
                api_key,
                [structure_id, evidence_queue],
            )
            load_state = gr.Button("Load field-review state")
            review_state = gr.JSON(label="Extraction + review progress")
            load_state.click(
                refresh_evidence_review_state,
                [api_key, structure_id],
                review_state,
            )
            with gr.Row():
                field = gr.Dropdown(
                    ["study", "population", "intervention", "comparator", "endpoint", "outcome", "safety"],
                    value="study",
                    label="Field",
                )
                field_decision = gr.Radio(
                    ["VERIFIED", "CORRECTED", "REJECTED"],
                    value="VERIFIED",
                    label="SME decision",
                )
            corrected = gr.Textbox(
                label="Corrected JSON — only for CORRECTED",
                lines=3,
            )
            field_rationale = gr.Textbox(label="Field rationale", lines=3)
            record_field = gr.Button("Record field decision")
            field_result = gr.JSON(label="Field-review result")
            record_field.click(
                record_evidence_field_review,
                [api_key, structure_id, field, field_decision, corrected, field_rationale],
                field_result,
            )
            final_rationale = gr.Textbox(label="Final SME validation rationale", lines=3)
            final_confirm = gr.Checkbox(
                label="I confirm I am genuinely authorized to validate this scientific evidence"
            )
            finalize = gr.Button("Promote to SME_VALIDATED_EVIDENCE", variant="primary")
            final_result = gr.JSON(label="Evidence validation result")
            finalize.click(
                finalize_evidence_review_ui,
                [api_key, structure_id, final_rationale, final_confirm],
                final_result,
            )

        with gr.Tab("3 · Claim + Medical review"):
            gr.Markdown(
                "Claims can be composed only from **SME_VALIDATED_EVIDENCE** and require an independent Medical reviewer."
            )
            refresh_sources = gr.Button("Refresh validated evidence sources")
            claim_structure = gr.Dropdown([], label="Validated evidence structure")
            source_payload = gr.JSON(label="Eligible evidence sources")
            refresh_sources.click(
                refresh_claim_composition_sources,
                api_key,
                [claim_structure, source_payload],
            )
            claim_kind = gr.Radio(
                ["EFFICACY_ENDPOINT", "SAFETY"],
                value="EFFICACY_ENDPOINT",
                label="Claim kind",
            )
            compose = gr.Button("Compose evidence-bound claim", variant="primary")
            compose_result = gr.JSON(label="Candidate + exact support package")
            compose.click(
                compose_claim_ui,
                [api_key, claim_structure, claim_kind],
                compose_result,
            )
            gr.Markdown("### Independent Medical review")
            refresh_candidates = gr.Button("Refresh pending claims")
            candidate_id = gr.Dropdown([], label="Composed claim candidate")
            candidates = gr.JSON(label="Pending Medical review")
            refresh_candidates.click(
                refresh_composed_claim_candidates,
                api_key,
                [candidate_id, candidates],
            )
            medical_decision = gr.Radio(
                ["VALIDATED", "REJECTED", "NEEDS_REVISION"],
                value="NEEDS_REVISION",
                label="Medical decision",
            )
            medical_rationale = gr.Textbox(label="Medical rationale", lines=3)
            medical_confirm = gr.Checkbox(
                label="I confirm I am independently authorized to validate this claim"
            )
            medical_submit = gr.Button("Record Medical decision")
            medical_result = gr.JSON(label="Medical review result")
            medical_submit.click(
                review_composed_claim_ui,
                [api_key, candidate_id, medical_decision, medical_rationale, medical_confirm],
                medical_result,
            )

        with gr.Tab("4 · MLR + activation"):
            gr.Markdown(
                "Scientific validity does not equal permitted promotional use. MLR approval is scoped by **market + purpose + audience + validity window**."
            )
            refresh_claims = gr.Button("Refresh Medical-validated claims")
            medical_claim_id = gr.Dropdown([], label="Medical-validated claim")
            medical_claims = gr.JSON(label="Claims awaiting / eligible for MLR")
            refresh_claims.click(
                refresh_mlr_submission_claims,
                api_key,
                [medical_claim_id, medical_claims],
            )
            with gr.Row():
                mlr_market = gr.Dropdown(
                    ["Global", "US", "India", "China", "European Union"],
                    value="US",
                    label="Approval market",
                )
                mlr_purpose = gr.Dropdown(
                    ["PROMOTIONAL_CONTENT", "MEDICAL_RESPONSE", "INTERNAL_INSIGHT"],
                    value="PROMOTIONAL_CONTENT",
                    label="Approval purpose",
                )
                mlr_audience = gr.Dropdown(
                    ["ALL", "HCP", "PATIENT", "PAYER", "INTERNAL"],
                    value="HCP",
                    label="Approval audience",
                )
            submission_rationale = gr.Textbox(label="MLR submission rationale", lines=3)
            submit_mlr = gr.Button("Submit claim to MLR", variant="primary")
            submission_result = gr.JSON(label="MLR submission result")
            submit_mlr.click(
                submit_composed_claim_mlr,
                [api_key, medical_claim_id, mlr_market, mlr_purpose, mlr_audience, submission_rationale],
                submission_result,
            )

            gr.Markdown("### Independent MLR decision")
            refresh_reviews = gr.Button("Refresh composed-claim MLR queue")
            composed_review_id = gr.Dropdown([], label="MLR review")
            review_queue = gr.JSON(label="Pending MLR reviews")
            refresh_reviews.click(
                refresh_composed_mlr_reviews,
                api_key,
                [composed_review_id, review_queue],
            )
            mlr_decision = gr.Radio(
                ["APPROVED", "APPROVED_WITH_CHANGES", "REJECTED"],
                value="REJECTED",
                label="MLR decision",
            )
            mlr_rationale = gr.Textbox(label="MLR decision rationale", lines=3)
            approved_wording = gr.Textbox(
                label="Approved wording — required for APPROVED_WITH_CHANGES",
                lines=4,
            )
            conditions = gr.Textbox(
                value="Use only this approved wording with the governed evidence citation package.",
                label="Conditions of use",
                lines=2,
            )
            days_valid = gr.Slider(1, 730, value=365, step=1, label="Validity in days")
            mlr_confirm = gr.Checkbox(
                label="I confirm I am genuinely authorized and independent to make this MLR decision"
            )
            decide_mlr = gr.Button("Record MLR decision")
            decision_result = gr.JSON(label="MLR decision / activation")
            decide_mlr.click(
                decide_composed_mlr,
                [
                    api_key,
                    composed_review_id,
                    mlr_decision,
                    mlr_rationale,
                    approved_wording,
                    conditions,
                    days_valid,
                    mlr_confirm,
                ],
                decision_result,
            )
            refresh_activations = gr.Button("Refresh governed-use activations")
            activations = gr.JSON(label="Activation history and runtime state")
            refresh_activations.click(
                refresh_composed_activations,
                api_key,
                activations,
            )

        with gr.Tab("5 · Governance status"):
            gr.Markdown(
                "Review tenant metrics and the hash-linked audit chain after completing the workflow."
            )
            status = gr.Button("Refresh governance status")
            status_result = gr.JSON(label="Governance status")
            status.click(governance_status, api_key, status_result)

    return demo


if __name__ == "__main__":
    build_journey_ui().launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("PORT", "7860")),
        share=os.getenv("GRADIO_SHARE", "false").lower() == "true",
    )
