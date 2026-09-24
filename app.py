from __future__ import annotations

import json
import os
import re
from pathlib import Path

import gradio as gr
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer


ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "platform_artifacts"

CLAIMS = pd.read_csv(ARTIFACTS / "claims.csv")
CLAIM_EVIDENCE = pd.read_csv(ARTIFACTS / "claim_evidence.csv")
EVIDENCE = pd.read_csv(ARTIFACTS / "evidence_sources.csv")
POLICIES = pd.read_csv(ARTIFACTS / "purpose_access_policies.csv")


def load_or_build_index() -> dict:
    """Load the packaged index, rebuilding it from governed chunks if needed."""
    index_path = ARTIFACTS / "document_retrieval_index.joblib"
    try:
        loaded = joblib.load(index_path)
        required = {"word_vectorizer", "word_matrix", "character_vectorizer", "character_matrix", "chunks"}
        if required.issubset(loaded):
            return loaded
    except Exception as exc:
        print(f"Packaged retrieval index unavailable ({type(exc).__name__}); rebuilding from document_chunks.csv.")

    chunks = pd.read_csv(ARTIFACTS / "document_chunks.csv")
    text = chunks["chunk_text"].fillna("").astype(str)
    word_vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1)
    character_vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
    return {
        "word_vectorizer": word_vectorizer,
        "word_matrix": word_vectorizer.fit_transform(text),
        "character_vectorizer": character_vectorizer,
        "character_matrix": character_vectorizer.fit_transform(text),
        "chunks": chunks,
        "word_weight": 0.65,
        "character_weight": 0.35,
        "index_type": "RUNTIME_REBUILT_TFIDF",
        "chunk_count": len(chunks),
    }


INDEX = load_or_build_index()

INTENT_RULES = {
    "DOSAGE": ("dose", "dosage", "mg", "take each day"),
    "EFFICACY": ("efficacy", "effective", "perform", "response rate", "orr", "compared"),
    "SAFETY": ("safety", "adverse", "toxicity", "cardiac", "heart", "atrial"),
    "TRIAL_DESIGN": ("trial design", "study design", "randomized", "designed"),
    "PUBLICATION": ("publication", "paper", "article"),
    "CLINICAL_STUDY": ("clinical study", "clinical trial", "nct"),
    "ROUTE": ("route", "administered", "oral"),
    "REGULATORY": ("fda", "application", "regulatory", "approval"),
    "IDENTITY": ("what is", "brand", "generic", "name"),
}

FOCUS_QUERY_TERMS = {
    "EFFICACY": "overall response rate ORR progression-free survival treatment response comparative efficacy",
    "SAFETY": "cardiac safety atrial fibrillation adverse events hypertension hemorrhage toxicity",
    "DOSAGE": "recommended dosage dose administration milligrams",
    "TRIAL_DESIGN": "randomized phase enrollment endpoint study design",
    "PUBLICATION": "publication analysis article manuscript",
    "CLINICAL_STUDY": "clinical trial registry NCT study",
}


def detect_intent(question: str) -> str:
    return detect_intents(question)[0]


def detect_intents(question: str) -> list[str]:
    text = question.lower()
    detected = [
        intent for intent, terms in INTENT_RULES.items()
        if any(term in text for term in terms)
    ]
    # IDENTITY terms such as "what is" are generic and should not override
    # more specific regulated intents.
    if len(detected) > 1 and "IDENTITY" in detected:
        detected.remove("IDENTITY")
    return detected or ["UNKNOWN"]


def explicit_market(question: str, selected_market: str) -> str:
    text = question.lower()
    known = {"india": "India", "china": "China", "united states": "United States", "usa": "United States", "global": "Global"}
    for token, market in known.items():
        if re.search(rf"\b{re.escape(token)}\b", text):
            return market
    match = re.search(r"\b(?:in|for)\s+(?:the\s+)?([a-z][a-z\s-]*?)\s*\??$", text)
    if match and match.group(1).strip() not in {"brukinsa", "zanubrutinib"}:
        return match.group(1).strip().title()
    return selected_market


def policy_for(role: str, purpose: str, data_class: str) -> tuple[str, str]:
    matches = POLICIES[
        (POLICIES.role_id == role)
        & (POLICIES.purpose.isin([purpose, "*"]))
        & (POLICIES.data_class.isin([data_class, "*"]))
    ]
    if matches.empty:
        return "DENY", "NO_MATCHING_POLICY"
    row = matches.iloc[0]
    return str(row.decision), str(row.condition)


def market_matches(claim_market: str, requested_market: str) -> bool:
    return claim_market == requested_market or claim_market == "Global"


def citation_for_claim(claim_id: str) -> list[dict]:
    links = CLAIM_EVIDENCE[CLAIM_EVIDENCE.claim_id == claim_id]
    rows = links.merge(EVIDENCE, on="source_id", how="inner")
    return [
        {"title": row.source_title, "url": row.source_url, "market": row.market}
        for row in rows.itertuples()
    ]


def governed_claim_answer(question: str, role: str, purpose: str, market: str) -> dict | None:
    intent = detect_intent(question)
    candidates = CLAIMS[(CLAIMS.claim_type == intent) & (CLAIMS.status == "SUPPORTED")]
    candidates = candidates[candidates.market.map(lambda value: market_matches(str(value), market))]
    if candidates.empty:
        return None

    blocked = []
    for row in candidates.itertuples():
        decision, condition = policy_for(role, purpose, row.data_class)
        promotional_unreviewed = purpose == "PROMOTIONAL_CONTENT" and row.approval_status != "MLR_APPROVED"
        if decision == "DENY" or promotional_unreviewed or decision == "CONDITIONAL":
            blocked.append(row.claim_id)
            continue
        return {
            "status": "ANSWERED",
            "response_type": "GOVERNED_ANSWER",
            "answer": row.claim_text,
            "citations": citation_for_claim(row.claim_id),
            "usage_conditions": condition,
            "intent": intent,
            "market": market,
            "blocked_claims": blocked,
            "evidence_results": [],
        }

    return {
        "status": "BLOCKED",
        "response_type": "POLICY_BLOCK",
        "answer": "Relevant governed knowledge exists, but its use is not permitted for this role and purpose.",
        "citations": [],
        "usage_conditions": "",
        "intent": intent,
        "market": market,
        "blocked_claims": blocked,
        "evidence_results": [],
    }


def retrieve_evidence(question: str, market: str, top_k: int = 3, focus: str | None = None) -> list[dict]:
    if focus and focus != "UNKNOWN":
        question = f"{question} {FOCUS_QUERY_TERMS.get(focus, focus.lower().replace('_', ' '))}"
    word_query = INDEX["word_vectorizer"].transform([question])
    character_query = INDEX["character_vectorizer"].transform([question])
    word_scores = cosine_similarity(word_query, INDEX["word_matrix"]).ravel()
    character_scores = cosine_similarity(character_query, INDEX["character_matrix"]).ravel()
    scores = INDEX["word_weight"] * word_scores + INDEX["character_weight"] * character_scores
    chunks = INDEX["chunks"].copy()
    chunks["score"] = scores

    if market == "United States":
        allowed_types = {"APPROVED_LABEL", "SCIENTIFIC_PUBLICATION"}
    elif market in {"Global", "China"}:
        allowed_types = {"SCIENTIFIC_PUBLICATION"}
    else:
        return []

    chunks = chunks[chunks.document_type.isin(allowed_types)].sort_values("score", ascending=False).head(top_k)
    return [
        {
            "file": row.file_name,
            "page": int(row.page_number),
            "score": round(float(row.score), 4),
            "focus": focus or "GENERAL",
            "passage": str(row.chunk_text)[:900],
        }
        for row in chunks.itertuples()
        if row.score > 0
    ]


def orchestrate(question: str, role: str, purpose: str, selected_market: str) -> dict:
    question = (question or "").strip()
    if not question:
        return {"status": "ABSTAIN", "answer": "Enter a question.", "citations": [], "evidence_results": []}

    market = explicit_market(question, selected_market)
    intents = detect_intents(question)
    intent = intents[0]
    if role == "ROLE_COMMERCIAL" and purpose == "PROMOTIONAL_CONTENT":
        return {
            "status": "BLOCKED", "response_type": "POLICY_BLOCK",
            "answer": "Relevant evidence may exist, but promotional use requires genuine MLR approval.",
            "citations": [], "evidence_results": [], "intent": intent, "intents": intents, "market": market,
        }

    # A single-intent request may be resolved deterministically from a governed
    # claim. Multi-intent questions stay on the evidence-discovery path until an
    # authorized SME validates a composite claim.
    if len(intents) == 1:
        governed = governed_claim_answer(question, role, purpose, market)
        if governed is not None:
            governed["intents"] = intents
            return governed

    if market not in {"United States", "Global", "China"}:
        return {
            "status": "ABSTAIN", "response_type": "NO_SUPPORT",
            "answer": f"No authoritative, supported and permitted answer was found for the {market} market.",
            "citations": [], "evidence_results": [], "intent": intent, "intents": intents, "market": market,
        }

    evidence = []
    seen = set()
    for focus in intents:
        for item in retrieve_evidence(question, market, top_k=2, focus=focus):
            key = (item["file"], item["page"], item["passage"])
            if key not in seen:
                evidence.append(item)
                seen.add(key)
    evidence = evidence[:6]
    if not evidence:
        return {
            "status": "ABSTAIN", "response_type": "NO_SUPPORT",
            "answer": "No authoritative, supported and permitted evidence was found.",
            "citations": [], "evidence_results": [], "intent": intent, "intents": intents, "market": market,
        }

    return {
        "status": "EVIDENCE_ONLY", "response_type": "EVIDENCE_DISCOVERY",
        "answer": "Relevant permitted evidence was found, but no SME-validated governed claim supports a final answer yet.",
        "citations": [], "evidence_results": evidence, "intent": intent, "intents": intents, "market": market,
    }


def run_ui(question: str, role: str, purpose: str, market: str):
    result = orchestrate(question, role, purpose, market)
    citations = "\n".join(f"- [{c['title']}]({c['url']})" for c in result.get("citations", [])) or "No governed citation returned."
    evidence = "\n\n".join(
        f"### Evidence {i}\n**Focus:** {item.get('focus', 'GENERAL')}  \n**File:** {item['file']} — page {item['page']} — score {item['score']}\n\n> {item['passage']}"
        for i, item in enumerate(result.get("evidence_results", []), 1)
    ) or "No raw evidence exposed."
    trace = json.dumps({k: v for k, v in result.items() if k not in {"answer", "evidence_results"}}, indent=2)
    return result.get("status", ""), result.get("response_type", ""), result.get("answer", ""), citations, evidence, trace


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Regulated Life Sciences GraphRAG") as demo:
        gr.Markdown("# Regulated Life Sciences GraphRAG\nSource-grounded retrieval with role, purpose and market controls.")
        with gr.Row():
            role = gr.Dropdown(["ROLE_COMMERCIAL", "ROLE_MEDICAL", "ROLE_CLINICAL", "ROLE_REGULATORY"], value="ROLE_COMMERCIAL", label="Role")
            purpose = gr.Dropdown(["INTERNAL_INSIGHT", "PROMOTIONAL_CONTENT", "MEDICAL_RESPONSE", "CLINICAL_ANALYSIS", "REGULATORY_ANALYSIS"], value="INTERNAL_INSIGHT", label="Purpose")
            market = gr.Dropdown(["United States", "Global", "China", "India"], value="United States", label="Market")
        question = gr.Textbox(label="Question", value="What is the approved dosage for BRUKINSA?")
        ask = gr.Button("Ask governed GraphRAG", variant="primary")
        with gr.Row():
            status = gr.Textbox(label="Decision status")
            response_type = gr.Textbox(label="Response type")
        answer = gr.Textbox(label="Governed response", lines=4)
        citations = gr.Markdown(label="Citation chain")
        with gr.Accordion("Retrieved evidence", open=False):
            evidence = gr.Markdown()
        with gr.Accordion("Decision trace", open=False):
            trace = gr.Code(language="json")
        ask.click(run_ui, [question, role, purpose, market], [status, response_type, answer, citations, evidence, trace])
        gr.Markdown("**Prototype notice:** Evidence discovery is not an approved medical, regulatory or promotional claim.")
    return demo


if __name__ == "__main__":
    build_app().launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("PORT", "7860")),
        share=os.getenv("GRADIO_SHARE", "false").lower() == "true",
    )
