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


def _semantic_relationship_lines(result: dict) -> list[str]:
    query_labels = set(result.get("resolved_entities") or [])
    related_labels = set()
    for item in (result.get("governed_claims") or []) + (result.get("results") or []):
        related_labels.update(item.get("semantic_related_matches") or [])
        related_labels.update(item.get("semantic_direct_matches") or [])

    if not query_labels or not related_labels:
        return []

    with connection() as conn:
        rows = conn.execute(
            """SELECT s.canonical_name AS source_name,
                      r.relationship_type,
                      t.canonical_name AS target_name
               FROM semantic_relationships r
               JOIN semantic_concepts s
                 ON s.concept_id=r.source_concept_id AND s.version=r.source_version
               JOIN semantic_concepts t
                 ON t.concept_id=r.target_concept_id AND t.version=r.target_version
               WHERE r.status='ACTIVE'"""
        ).fetchall()

    lines = []
    for row in rows:
        source = row["source_name"]
        target = row["target_name"]
        if (source in query_labels and target in related_labels) or (
            target in query_labels and source in related_labels
        ):
            lines.append(f"{source} —{row['relationship_type']}→ {target}")
    return sorted(set(lines))


def _workspace_summary(result: dict, question: str) -> str:
    status = result.get("status", "UNKNOWN")
    response_type = result.get("response_type", "UNKNOWN")
    role = result.get("role", "UNKNOWN")
    purpose = result.get("purpose", "UNKNOWN")
    market = result.get("market", "UNKNOWN")
    governance_message = result.get("governance_message", "")
    audit_id = result.get("audit_id", "")
    entities = result.get("resolved_entities") or []
    relationships = _semantic_relationship_lines(result)

    if status == "ANSWERED":
        answer = result.get("answer", "No answer text returned.")
        state = "✅ Governed answer"
    elif status == "EVIDENCE_ONLY":
        answer = (
            "Relevant permitted evidence was found, but it has not yet been promoted "
            "to an SME-validated governed claim. Review the evidence below."
        )
        state = "🟡 Evidence discovery"
    elif status == "BLOCKED":
        answer = "The platform found relevant evidence but policy does not permit its use for this request."
        state = "⛔ Policy blocked"
    else:
        answer = "The platform abstained because no authoritative permitted evidence supported this request."
        state = "⚪ Abstained"

    entity_text = ", ".join(entities) if entities else "No canonical entities resolved"
    relationship_text = (
        "\n".join(f"- {line}" for line in relationships)
        if relationships
        else "- No explicit semantic relationship was required for this result."
    )

    return f"""### {state}

**Question:** {question}

**Answer / decision**

{answer}

**Governance context**
- Response type: {response_type}
- Role: {role}
- Purpose: {purpose}
- Market: {market}
- Policy explanation: {governance_message}
- Audit ID: {audit_id}

**Canonical entities:** {entity_text}

**Semantic path**
{relationship_text}
"""


def _workspace_evidence_rows(result: dict) -> list[list]:
    rows = []
    if result.get("status") == "ANSWERED":
        for claim in result.get("governed_claims") or []:
            rows.append(
                [
                    "Governed claim",
                    claim.get("claim_text", ""),
                    claim.get("file_name", ""),
                    claim.get("approval_status", ""),
                    claim.get("usage_condition", ""),
                    claim.get("score", 0),
                    ", ".join(claim.get("semantic_direct_matches") or []),
                    ", ".join(claim.get("semantic_related_matches") or []),
                ]
            )
    else:
        for item in result.get("results") or []:
            rows.append(
                [
                    "Evidence",
                    item.get("text", ""),
                    item.get("file_name", ""),
                    "NOT_SME_VALIDATED",
                    item.get("usage_condition", ""),
                    item.get("hybrid_score", 0),
                    ", ".join(item.get("semantic_direct_matches") or []),
                    ", ".join(item.get("semantic_related_matches") or []),
                ]
            )
    return rows


def _workspace_scientific_rows(result: dict) -> list[list]:
    rows = []
    source_items = result.get("governed_claims") or result.get("results") or []
    for item in source_items:
        intelligence = item.get("evidence_intelligence") or {}
        population = intelligence.get("population") or {}
        intervention = intelligence.get("intervention") or {}
        rows.append(
            [
                ", ".join(intelligence.get("study") or []),
                ", ".join(population.get("indications") or []),
                " | ".join(population.get("context") or []),
                ", ".join(intervention.get("interventions") or []),
                ", ".join(intelligence.get("comparator") or []),
                ", ".join(intelligence.get("endpoint") or []),
                " | ".join(intelligence.get("outcome") or []),
                " | ".join(intelligence.get("safety") or []),
                intelligence.get("review_status", ""),
                intelligence.get("extraction_method", ""),
            ]
        )
    return rows


def _workspace_provenance_rows(result: dict) -> list[list]:
    rows = []
    source_items = result.get("governed_claims") or result.get("results") or []
    for item in source_items:
        rows.append(
            [
                item.get("file_name", ""),
                item.get("document_id", ""),
                item.get("chunk_id", ""),
                item.get("page_number", ""),
                item.get("market", result.get("market", "")),
                item.get("data_class", ""),
                item.get("approval_status", "EVIDENCE_ONLY"),
                item.get("usage_condition", ""),
            ]
        )
    return rows


def run_query_workspace(api_key, question, purpose, market, top_k):
    result = run_query(api_key, question, purpose, market, top_k)
    return (
        _workspace_summary(result, question),
        _workspace_scientific_rows(result),
        _workspace_evidence_rows(result),
        _workspace_provenance_rows(result),
        result,
    )


def refresh_evidence_review(api_key):
    queue = _result(_client().get("/v1/evidence/review-queue", headers=_bearer(api_key)))
    choices = [item["structure_id"] for item in queue]
    return gr.update(choices=choices, value=choices[0] if choices else None), queue


def refresh_evidence_review_state(api_key, structure_id):
    if not structure_id:
        raise gr.Error("Select an evidence structure.")
    return _result(
        _client().get(
            f"/v1/evidence/intelligence/{structure_id}/reviews",
            headers=_bearer(api_key),
        )
    )


def record_evidence_field_review(
    api_key,
    structure_id,
    field_name,
    decision,
    corrected_value_json,
    rationale,
):
    if not structure_id:
        raise gr.Error("Select an evidence structure.")
    reviewed_value = None
    if decision == "CORRECTED":
        try:
            reviewed_value = json.loads(corrected_value_json)
        except json.JSONDecodeError as exc:
            raise gr.Error("Corrected value must be valid JSON.") from exc
    return _result(
        _client().post(
            f"/v1/evidence/intelligence/{structure_id}/reviews",
            headers=_bearer(api_key),
            json={
                "field_name": field_name,
                "decision": decision,
                "reviewed_value": reviewed_value,
                "rationale": rationale,
            },
        )
    )


def finalize_evidence_review_ui(api_key, structure_id, rationale, confirmed):
    if not structure_id:
        raise gr.Error("Select an evidence structure.")
    return _result(
        _client().post(
            f"/v1/evidence/intelligence/{structure_id}/finalize",
            headers=_bearer(api_key),
            json={
                "rationale": rationale,
                "authorization_confirmed": confirmed,
            },
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


def refresh_semantic_catalog(api_key):
    return _result(_client().get("/v1/semantic/concepts", headers=_bearer(api_key)))


def propose_semantic_change(
    api_key,
    change_type,
    concept_id,
    alias,
    alias_source,
    confidence,
    system,
    identifier,
    source_uri,
    canonical_name,
    rationale,
):
    payload = {}
    if change_type == "ADD_ALIAS":
        payload = {
            "alias": (alias or "").strip(),
            "source": (alias_source or "GOVERNED_CHANGE").strip(),
            "confidence": float(confidence),
        }
    elif change_type == "ADD_EXTERNAL_MAPPING":
        payload = {
            "system": (system or "").strip(),
            "identifier": (identifier or "").strip(),
            "source_uri": (source_uri or "").strip(),
        }
    elif change_type == "UPDATE_CONCEPT":
        payload = {"canonical_name": (canonical_name or "").strip()}

    return _result(
        _client().post(
            "/v1/semantic/change-requests",
            headers=_bearer(api_key),
            json={
                "change_type": change_type,
                "concept_id": (concept_id or "").strip(),
                "payload": payload,
                "rationale": rationale,
            },
        )
    )


def refresh_semantic_changes(api_key):
    changes = _result(
        _client().get("/v1/semantic/change-requests", headers=_bearer(api_key))
    )
    pending = [item for item in changes if item["status"] == "PENDING"]
    choices = [item["change_request_id"] for item in pending]
    return gr.update(choices=choices, value=choices[0] if choices else None), changes


def decide_semantic_change(api_key, change_request_id, decision, rationale, confirmed):
    if not change_request_id:
        raise gr.Error("Select a pending semantic change request.")
    return _result(
        _client().post(
            f"/v1/semantic/change-requests/{change_request_id}/decisions",
            headers=_bearer(api_key),
            json={
                "decision": decision,
                "rationale": rationale,
                "authorization_confirmed": confirmed,
            },
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
            "Structured evidence": "evidence_intelligence",
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
.gradio-container {max-width: 1500px !important;}
"""


def build_ui():
    with gr.Blocks(title="Governed Life Sciences GraphRAG", css=CSS) as demo:
        gr.HTML("""<div class='hero'><h1>Governed Life Sciences GraphRAG</h1>
        <p>Multi-tenant evidence ingestion, semantic master, governed retrieval, semantic governance, SME validation, MLR approval and audit.</p></div>""")
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

        with gr.Tab("Medical Evidence Workspace"):
            gr.Markdown(
                "Ask a medical or regulatory question and review the governed answer, "
                "semantic reasoning path, structured scientific evidence, provenance and policy state in one place."
            )
            question = gr.Textbox(
                label="Question",
                lines=3,
                placeholder="e.g. What evidence supports BRUKINSA in relapsed/refractory CLL?",
            )
            with gr.Row():
                purpose = gr.Dropdown(
                    ["MEDICAL_RESPONSE", "INTERNAL_INSIGHT", "PROMOTIONAL_CONTENT", "CLINICAL_ANALYSIS", "REGULATORY_ANALYSIS"],
                    value="MEDICAL_RESPONSE",
                    label="Purpose",
                )
                query_market = gr.Dropdown(
                    ["Global", "United States", "India", "China", "European Union"],
                    value="Global",
                    label="Market",
                )
                top_k = gr.Slider(1, 10, value=3, step=1, label="Evidence results")
            query_button = gr.Button("Ask governed platform", variant="primary")
            query_summary = gr.Markdown(label="Governed response")
            query_scientific = gr.Dataframe(
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
            query_evidence = gr.Dataframe(
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
            query_provenance = gr.Dataframe(
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
                datatype=["str", "str", "str", "str", "str", "str", "str", "str"],
                interactive=False,
                wrap=True,
                label="Provenance and lineage",
            )
            with gr.Accordion("Raw decision payload", open=False):
                query_output = gr.JSON(label="Raw API response")
            query_button.click(
                run_query_workspace,
                [api_key, question, purpose, query_market, top_k],
                [query_summary, query_scientific, query_evidence, query_provenance, query_output],
            )

        with gr.Tab("Evidence review"):
            gr.Markdown(
                "Review machine-extracted scientific structure field by field. "
                "The original extraction is preserved; corrections are stored as separate audited SME decisions."
            )
            evidence_review_refresh = gr.Button("Refresh evidence review queue")
            evidence_structure_id = gr.Dropdown([], label="Evidence structure")
            evidence_review_queue = gr.JSON(label="Pending / partial evidence structures")
            evidence_review_refresh.click(
                refresh_evidence_review,
                api_key,
                [evidence_structure_id, evidence_review_queue],
            )

            evidence_state_refresh = gr.Button("Load review state")
            evidence_review_state = gr.JSON(label="Current extraction and field-review state")
            evidence_state_refresh.click(
                refresh_evidence_review_state,
                [api_key, evidence_structure_id],
                evidence_review_state,
            )

            with gr.Row():
                evidence_field = gr.Dropdown(
                    ["study", "population", "intervention", "comparator", "endpoint", "outcome", "safety"],
                    value="study",
                    label="Scientific field",
                )
                evidence_field_decision = gr.Radio(
                    ["VERIFIED", "CORRECTED", "REJECTED"],
                    value="VERIFIED",
                    label="SME decision",
                )
            corrected_value = gr.Textbox(
                label="Corrected value as JSON — required only for CORRECTED",
                placeholder='e.g. ["PFS"] or {"indications":["CLL"],"context":["..."]}',
                lines=4,
            )
            evidence_field_rationale = gr.Textbox(label="Field-review rationale", lines=4)
            evidence_field_submit = gr.Button("Record field review")
            evidence_field_result = gr.JSON(label="Field-review result")
            evidence_field_submit.click(
                record_evidence_field_review,
                [
                    api_key,
                    evidence_structure_id,
                    evidence_field,
                    evidence_field_decision,
                    corrected_value,
                    evidence_field_rationale,
                ],
                evidence_field_result,
            )

            gr.Markdown("### Finalize SME evidence validation")
            evidence_finalize_rationale = gr.Textbox(label="Final validation rationale", lines=4)
            evidence_finalize_confirm = gr.Checkbox(
                label="I confirm that I am genuinely authorized to validate this structured scientific evidence"
            )
            evidence_finalize = gr.Button("Promote to SME_VALIDATED_EVIDENCE", variant="primary")
            evidence_finalize_result = gr.JSON(label="Evidence validation result")
            evidence_finalize.click(
                finalize_evidence_review_ui,
                [
                    api_key,
                    evidence_structure_id,
                    evidence_finalize_rationale,
                    evidence_finalize_confirm,
                ],
                evidence_finalize_result,
            )

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

        with gr.Tab("Semantic catalog"):
            semantic_refresh = gr.Button("Refresh semantic master")
            semantic_catalog = gr.JSON(label="Active canonical concepts, aliases and external mappings")
            semantic_refresh.click(refresh_semantic_catalog, api_key, semantic_catalog)

        with gr.Tab("Semantic governance"):
            gr.Markdown(
                "Propose controlled semantic-master changes. "
                "Approval requires an independent Regulatory or Semantic Steward reviewer."
            )
            with gr.Row():
                semantic_change_type = gr.Dropdown(
                    ["ADD_ALIAS", "ADD_EXTERNAL_MAPPING", "UPDATE_CONCEPT"],
                    value="ADD_ALIAS",
                    label="Change type",
                )
                semantic_concept_id = gr.Textbox(
                    value="BRAND:BRUKINSA",
                    label="Canonical concept ID",
                    placeholder="e.g. BRAND:BRUKINSA",
                )
            with gr.Accordion("Alias payload", open=True):
                semantic_alias = gr.Textbox(label="Alias", placeholder="e.g. Brukinsa oncology brand")
                semantic_alias_source = gr.Textbox(value="ENTERPRISE_MASTER", label="Alias source")
                semantic_confidence = gr.Slider(0.0, 1.0, value=0.95, step=0.01, label="Alias confidence")
            with gr.Accordion("External mapping payload", open=False):
                semantic_system = gr.Textbox(label="Terminology system", placeholder="e.g. RXNORM")
                semantic_identifier = gr.Textbox(label="External identifier")
                semantic_source_uri = gr.Textbox(label="Authoritative source URI")
            with gr.Accordion("Canonical concept update", open=False):
                semantic_canonical_name = gr.Textbox(label="New canonical name")
            semantic_rationale = gr.Textbox(label="Proposal rationale", lines=4)
            semantic_propose = gr.Button("Submit semantic change request", variant="primary")
            semantic_proposal_output = gr.JSON(label="Proposal result")
            semantic_propose.click(
                propose_semantic_change,
                [
                    api_key,
                    semantic_change_type,
                    semantic_concept_id,
                    semantic_alias,
                    semantic_alias_source,
                    semantic_confidence,
                    semantic_system,
                    semantic_identifier,
                    semantic_source_uri,
                    semantic_canonical_name,
                    semantic_rationale,
                ],
                semantic_proposal_output,
            )

            gr.Markdown("### Independent review")
            semantic_queue_refresh = gr.Button("Refresh semantic change queue")
            semantic_change_id = gr.Dropdown([], label="Pending change request")
            semantic_change_queue = gr.JSON(label="Semantic governance queue")
            semantic_queue_refresh.click(
                refresh_semantic_changes,
                api_key,
                [semantic_change_id, semantic_change_queue],
            )
            semantic_decision = gr.Radio(
                ["APPROVED", "REJECTED"],
                value="REJECTED",
                label="Reviewer decision",
            )
            semantic_decision_rationale = gr.Textbox(label="Reviewer rationale", lines=4)
            semantic_confirm = gr.Checkbox(
                label="I confirm that I am independently authorized to make this semantic-governance decision"
            )
            semantic_decide = gr.Button("Record semantic decision")
            semantic_decision_output = gr.JSON(label="Semantic decision result")
            semantic_decide.click(
                decide_semantic_change,
                [
                    api_key,
                    semantic_change_id,
                    semantic_decision,
                    semantic_decision_rationale,
                    semantic_confirm,
                ],
                semantic_decision_output,
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
