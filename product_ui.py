from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import gradio as gr
from fastapi.testclient import TestClient

from product_api.app import app, connection, hash_api_key, initialize_database
from product_api.worker import process_ingestion_job


initialize_database()


def _client() -> TestClient:
    return TestClient(app)


def _bearer(api_key: str) -> dict:
    if not (api_key or "").strip():
        raise gr.Error("Enter an API key.")
    return {"Authorization": f"Bearer {api_key.strip()}"}


def _principal(api_key: str) -> dict:
    with connection() as conn:
        row = conn.execute(
            """SELECT tenant_id, actor_id, role FROM api_principals
               WHERE api_key_hash=? AND status='ACTIVE'""",
            (hash_api_key((api_key or "").strip()),),
        ).fetchone()
    if not row:
        raise gr.Error("Invalid or inactive API key.")
    return dict(row)


def _result(response):
    payload = response.json()
    if response.status_code >= 400:
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        raise gr.Error(str(detail))
    return payload


def upload_and_process(api_key, file_path, market, data_class, sensitivity):
    if not file_path:
        raise gr.Error("Choose a source document.")
    principal = _principal(api_key)
    headers = {
        "X-Tenant-ID": principal["tenant_id"],
        "X-Actor-ID": principal["actor_id"],
        "X-Role": principal["role"],
    }
    with open(file_path, "rb") as source:
        upload = _result(
            _client().post(
                "/v1/documents",
                headers=headers,
                files={"file": (os.path.basename(file_path), source, "application/octet-stream")},
                data={"market": market, "data_class": data_class, "sensitivity": sensitivity},
            )
        )
    processed = process_ingestion_job(upload["job_id"], principal["tenant_id"], "system_worker")
    return {"upload": upload, "processing": processed}


def run_query(api_key, question, purpose, market, top_k):
    return _result(
        _client().post(
            "/v1/query",
            headers=_bearer(api_key),
            json={"question": question, "purpose": purpose, "market": market, "top_k": int(top_k)},
        )
    )


def refresh_sme(api_key):
    candidates = _result(_client().get("/v1/sme/candidates", headers=_bearer(api_key)))
    choices = [candidate["candidate_id"] for candidate in candidates]
    return gr.update(choices=choices, value=choices[0] if choices else None), candidates


def record_sme(api_key, candidate_id, decision, rationale, confirmed):
    if not candidate_id:
        raise gr.Error("Select a pending candidate.")
    return _result(
        _client().post(
            f"/v1/sme/candidates/{candidate_id}/decisions",
            headers=_bearer(api_key),
            json={"decision": decision, "rationale": rationale, "authorization_confirmed": confirmed},
        )
    )


def refresh_mlr(api_key):
    reviews = _result(_client().get("/v1/mlr/reviews", headers=_bearer(api_key)))
    choices = [review["review_id"] for review in reviews]
    return gr.update(choices=choices, value=choices[0] if choices else None), reviews


def record_mlr(api_key, review_id, decision, rationale, wording, conditions, confirmed, days_valid):
    if not review_id:
        raise gr.Error("Select a pending MLR review.")
    now = datetime.now(timezone.utc)
    payload = {
        "decision": decision,
        "rationale": rationale,
        "authorization_confirmed": confirmed,
        "approved_wording": wording or None,
        "conditions_of_use": conditions or None,
        "effective_from_utc": (now - timedelta(minutes=1)).isoformat(),
        "expires_at_utc": (now + timedelta(days=int(days_valid))).isoformat(),
    }
    if decision == "REJECTED":
        payload["effective_from_utc"] = None
        payload["expires_at_utc"] = None
    return _result(
        _client().post(
            f"/v1/mlr/reviews/{review_id}/decisions",
            headers=_bearer(api_key),
            json=payload,
        )
    )


def governance_status(api_key):
    principal = _principal(api_key)
    tenant_id = principal["tenant_id"]
    with connection() as conn:
        metrics = {}
        for label, table in {
            "Documents": "documents",
            "Evidence chunks": "document_chunks",
            "Pending SME candidates": "candidate_claims",
            "Governed claims": "governed_claims",
            "MLR decisions": "mlr_review_decisions",
            "Audit events": "audit_events",
        }.items():
            if table == "candidate_claims":
                value = conn.execute(
                    "SELECT COUNT(*) AS n FROM candidate_claims WHERE tenant_id=? AND status='PENDING'", (tenant_id,)
                ).fetchone()["n"]
            else:
                value = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id=?", (tenant_id,)).fetchone()["n"]
            metrics[label] = value
    audit = _result(
        _client().get(
            "/v1/audit/verify",
            headers={"X-Tenant-ID": tenant_id, "X-Actor-ID": principal["actor_id"], "X-Role": principal["role"]},
        )
    )
    return {"principal": principal, "metrics": metrics, "audit_chain": audit}


CSS = """
.hero {background: linear-gradient(120deg,#10243e,#173f5f); padding:24px; border-radius:16px; color:white;}
.notice {border-left:5px solid #ff7518; padding:12px 16px; background:#fff8ef;}
"""


def build_ui():
    with gr.Blocks(title="Governed Life Sciences GraphRAG", css=CSS) as demo:
        gr.HTML("""<div class='hero'><h1>Governed Life Sciences GraphRAG</h1>
        <p>Multi-tenant evidence ingestion, governed retrieval, SME validation, MLR approval and audit.</p></div>""")
        gr.HTML("<div class='notice'><b>Prototype notice:</b> Do not validate or approve unless you are a genuinely authorized reviewer.</div>")
        api_key = gr.Textbox(label="API key", type="password", placeholder="rgp_…")

        with gr.Tab("Document ingestion"):
            source = gr.File(label="Source document", type="filepath", file_types=[".pdf", ".txt", ".md", ".csv", ".json"])
            with gr.Row():
                market = gr.Dropdown(["Global", "United States", "India", "China", "European Union"], value="Global", label="Market")
                data_class = gr.Dropdown(
                    ["PUBLIC_PRODUCT_INFORMATION", "APPROVED_LABEL_CONTENT", "MEDICAL_SCIENTIFIC_EVIDENCE", "REGULATORY_INFORMATION", "CLINICAL_RESTRICTED"],
                    value="MEDICAL_SCIENTIFIC_EVIDENCE", label="Data class",
                )
                sensitivity = gr.Dropdown(["PUBLIC", "CONTROLLED", "MEDICAL_ONLY", "RESTRICTED"], value="MEDICAL_ONLY", label="Sensitivity")
            ingest_button = gr.Button("Upload and process", variant="primary")
            ingest_output = gr.JSON(label="Ingestion result")
            ingest_button.click(upload_and_process, [api_key, source, market, data_class, sensitivity], ingest_output)

        with gr.Tab("Governed query"):
            question = gr.Textbox(label="Question", lines=3)
            with gr.Row():
                purpose = gr.Dropdown(["MEDICAL_RESPONSE", "INTERNAL_INSIGHT", "PROMOTIONAL_CONTENT", "CLINICAL_ANALYSIS", "REGULATORY_ANALYSIS"], value="MEDICAL_RESPONSE", label="Purpose")
                query_market = gr.Dropdown(["Global", "United States", "India", "China", "European Union"], value="Global", label="Market")
                top_k = gr.Slider(1, 10, value=3, step=1, label="Evidence results")
            query_button = gr.Button("Ask governed platform", variant="primary")
            query_output = gr.JSON(label="Decision, answer and evidence")
            query_button.click(run_query, [api_key, question, purpose, query_market, top_k], query_output)

        with gr.Tab("SME validation"):
            refresh_sme_button = gr.Button("Refresh pending candidates")
            candidate_id = gr.Dropdown([], label="Candidate")
            candidate_queue = gr.JSON(label="Pending queue")
            refresh_sme_button.click(refresh_sme, api_key, [candidate_id, candidate_queue])
            sme_decision = gr.Radio(["VALIDATED", "REJECTED", "NEEDS_REVISION"], value="NEEDS_REVISION", label="Decision")
            sme_rationale = gr.Textbox(label="Expert rationale", lines=4)
            sme_confirm = gr.Checkbox(label="I confirm that I am genuinely authorized to make this SME decision")
            sme_submit = gr.Button("Record SME decision")
            sme_output = gr.JSON(label="Decision result")
            sme_submit.click(record_sme, [api_key, candidate_id, sme_decision, sme_rationale, sme_confirm], sme_output)

        with gr.Tab("MLR review"):
            refresh_mlr_button = gr.Button("Refresh pending reviews")
            review_id = gr.Dropdown([], label="MLR review")
            review_queue = gr.JSON(label="Pending queue")
            refresh_mlr_button.click(refresh_mlr, api_key, [review_id, review_queue])
            mlr_decision = gr.Radio(["APPROVED", "APPROVED_WITH_CHANGES", "REJECTED"], value="REJECTED", label="Decision")
            mlr_rationale = gr.Textbox(label="MLR rationale", lines=4)
            approved_wording = gr.Textbox(label="Approved wording—required when approving with changes", lines=4)
            conditions = gr.Textbox(value="MLR_APPROVED_WORDING_ONLY", label="Conditions of use")
            days_valid = gr.Slider(1, 730, value=365, step=1, label="Approval validity in days")
            mlr_confirm = gr.Checkbox(label="I confirm that I am genuinely authorized to make this MLR decision")
            mlr_submit = gr.Button("Record MLR decision")
            mlr_output = gr.JSON(label="Decision result")
            mlr_submit.click(
                record_mlr,
                [api_key, review_id, mlr_decision, mlr_rationale, approved_wording, conditions, mlr_confirm, days_valid],
                mlr_output,
            )

        with gr.Tab("Governance dashboard"):
            status_button = gr.Button("Refresh governance status")
            status_output = gr.JSON(label="Tenant governance status")
            status_button.click(governance_status, api_key, status_output)
    return demo


if __name__ == "__main__":
    build_ui().launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("PORT", "7860")),
        share=os.getenv("GRADIO_SHARE", "false").lower() == "true",
    )
