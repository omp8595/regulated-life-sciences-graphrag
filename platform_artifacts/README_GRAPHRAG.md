# Governed Life Sciences GraphRAG Platform

This prototype implements graph-based retrieval for regulated
life-sciences knowledge.

## GraphRAG architecture

Questions are processed through:

1. Intent detection
2. Entity resolution
3. Role and purpose policy evaluation
4. Market and source-authority filtering
5. Multi-hop graph traversal
6. Hybrid textual and graph-proximity ranking
7. Governed claim selection or evidence discovery
8. Citation and audit recording

## Graph path

The core evidence path is:

Drug or brand
→ clinical trial or regulatory label
→ publication or governed claim
→ evidence source
→ ingested document
→ evidence chunk

## Response types

- GOVERNED_ANSWER
- EVIDENCE_DISCOVERY
- POLICY_BLOCK
- NO_SUPPORT

## Governance controls

- Commercial access to clinical-restricted data is denied.
- Commercial access to patient-level data is denied.
- Promotional use requires genuine authorized MLR approval.
- Market-specific claims cannot cross market boundaries.
- Candidate claims require SME validation.
- Evidence chunks cannot be presented as approved claims.
- Every query is written to a hash-linked audit log.

## Validation status

- Core governance tests: 21/21 passed
- GraphRAG governance tests: 14/14 passed
- Graph nodes: 244
- Graph edges: 521
- Evidence chunks represented: 221
- Audit chain: valid

## Important

This prototype is not a validated production medical,
regulatory, legal or promotional system.

The MLR and SME decision files are intentionally empty.
Do not create fictional approvals.
