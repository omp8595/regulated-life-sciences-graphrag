# Evidence Intelligence v1

Evidence Intelligence converts an ingested evidence chunk into structured,
source-traceable scientific metadata without generating new medical facts.

## Design principle

v1 is deterministic. It uses:

- canonical concepts already resolved by the semantic master;
- active typed semantic relationships;
- exact source sentences containing supported endpoint or safety signals.

It does not use an LLM to invent, summarize, or infer efficacy or safety claims.

Every extracted record is marked:

`UNVALIDATED_EXTRACTION`

until a future evidence-review workflow explicitly validates it.

## Structure

For each evidence chunk the platform stores:

- Study
- Population / indication
- Exact population-context sentence
- Intervention
- Comparator
- Endpoint
- Exact outcome-evidence sentence
- Exact safety-evidence sentence
- Semantic relationship paths
- Extraction method
- Review status
- Document and chunk lineage

The persistence layer is `evidence_intelligence`, keyed to the tenant,
document, and evidence chunk.

## Example

For a synthetic passage containing ALPINE, zanubrutinib, ibrutinib, CLL,
PFS results, and a safety sentence, the structure can look like:

```text
Study: ALPINE
Population: chronic lymphocytic leukemia
Intervention: zanubrutinib
Comparator: ibrutinib
Endpoint: PFS
Outcome evidence: <exact source sentence>
Safety evidence: <exact source sentence>
Review status: UNVALIDATED_EXTRACTION
```

The Medical Evidence Workspace renders this structure separately from:

1. the evidence / governed-claim table; and
2. provenance / lineage.

## API

Authenticated Medical, Clinical, Regulatory, and MLR reviewers can inspect
structured extraction records through:

`GET /v1/evidence/intelligence`

Optional query parameter:

`document_id=<document id>`

## Governance boundary

Evidence Intelligence improves scientific navigation. It does not:

- validate a medical claim;
- create promotional approval;
- replace SME review;
- replace MLR review;
- change role, purpose, market, or data-class policy decisions.
