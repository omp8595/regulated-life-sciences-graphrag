# Regulated Life Sciences Context Platform

This prototype demonstrates a governed context layer for
regulated life-sciences workflows.

## Product scope

- Product: BRUKINSA
- Generic name: zanubrutinib
- Clinical study: ALPINE
- Trial identifier: NCT03734016
- Primary regulatory market: United States

## Response types

1. GOVERNED_ANSWER
   A supported governed claim is available and permitted.

2. EVIDENCE_DISCOVERY
   Relevant authoritative evidence exists, but no
   SME-validated governed claim supports a final answer.

3. POLICY_BLOCK
   The requested use is prohibited or requires an
   authorized MLR decision.

4. NO_SUPPORT
   No authoritative, permitted and market-appropriate
   support exists.

## Governance rules

- Commercial teams cannot access clinical-restricted data.
- Commercial teams cannot access patient-level data.
- Scientific evidence may be available for internal,
  non-promotional use.
- Promotional use requires authorized MLR approval.
- US label claims cannot answer India-market questions.
- Candidate claims cannot answer users until SME validation.
- No fictional SME or MLR approval is included.
- Every governed answer includes source attribution.
- Query decisions are recorded in a hash-linked audit log.

## Important prototype limitation

This is a demonstration and not a validated production,
medical, legal, regulatory or promotional system.

The MLR and SME decision files are intentionally empty.
Only authorized reviewers should record real decisions.

## Primary artifacts

- life_sciences_context.db
- document_retrieval_index.joblib
- claims.csv
- claim_evidence.csv
- source_registry.csv
- purpose_access_policies.csv
- mlr_review_queue.csv
- mlr_decisions.csv
- candidate_sme_review_queue.csv
- sme_review_decisions.csv
- query_audit_log.csv
- governance_test_results.csv
- life_sciences_context_graph.graphml
- life_sciences_context_graph.png
