# Evidence Review v1

Evidence Review adds a governed human-validation layer on top of deterministic
Evidence Intelligence.

## Why it exists

Evidence Intelligence v1 produces structured scientific metadata from a source
chunk, but those fields are machine extractions and must not be treated as
validated medical evidence.

Evidence Review preserves that boundary by keeping the original extraction
immutable and storing SME decisions separately.

## Review lifecycle

```text
UNVALIDATED_EXTRACTION
        |
        | field-level SME review
        v
PARTIALLY_REVIEWED
        |
        | all populated fields reviewed
        | + explicit SME authorization confirmation
        v
SME_VALIDATED_EVIDENCE
```

## Reviewable scientific fields

- study
- population
- intervention
- comparator
- endpoint
- outcome
- safety

Only fields that contain extracted content are required for finalization.

## Field decisions

Each populated field can receive one of three decisions:

- `VERIFIED`: the extraction is correct as written.
- `CORRECTED`: the SME supplies a corrected value. The correction must retain
  the same top-level data shape as the extraction.
- `REJECTED`: the extracted field is not supported and is removed from the
  final validated payload.

Every review decision is append-only and contains reviewer identity, role,
rationale, original value, optional corrected value, and timestamp.

## Finalization controls

A record cannot become `SME_VALIDATED_EVIDENCE` unless:

1. every populated scientific field has a current review decision;
2. the reviewer has an authorized Medical, Clinical, or Regulatory role;
3. the reviewer explicitly confirms their authorization;
4. a substantive validation rationale is supplied.

Finalization creates a separate immutable validated payload. It does not
overwrite the original machine extraction.

## API

### Review queue

`GET /v1/evidence/review-queue`

Returns unvalidated or partially reviewed structured evidence records.

### Review state

`GET /v1/evidence/intelligence/{structure_id}/reviews`

Returns:

- original extraction
- required fields
- reviewed fields
- missing fields
- latest field-level decisions
- finalization eligibility
- validated payload, when one exists

### Record a field review

`POST /v1/evidence/intelligence/{structure_id}/reviews`

Example:

```json
{
  "field_name": "population",
  "decision": "CORRECTED",
  "reviewed_value": {
    "indications": ["chronic lymphocytic leukemia"],
    "context": ["patients with relapsed or refractory chronic lymphocytic leukemia (CLL)"]
  },
  "rationale": "SME reviewed the extracted population against the source passage."
}
```

### Finalize

`POST /v1/evidence/intelligence/{structure_id}/finalize`

Finalization is blocked when required fields remain unreviewed.

## Audit events

Evidence Review adds two hash-linked audit events:

- `EVIDENCE_FIELD_REVIEWED`
- `EVIDENCE_SME_VALIDATED`

## Product UI

The Gradio product now includes an **Evidence review** tab where an authorized
SME can:

1. load the review queue;
2. inspect the original extraction;
3. verify, correct, or reject individual fields;
4. see remaining fields;
5. explicitly promote the completed structure to
   `SME_VALIDATED_EVIDENCE`.

This workflow validates structured evidence metadata only. It does not create an
MLR-approved promotional claim and does not replace the separate governed-claim
and MLR workflows.
