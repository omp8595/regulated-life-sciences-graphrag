# Governed Claim Composition v1

Governed Claim Composition converts only **SME-validated structured evidence**
into deterministic candidate claims with immutable evidence lineage.

## Lifecycle

```text
SME_VALIDATED_EVIDENCE
        |
        | deterministic composition
        v
PENDING_MEDICAL_REVIEW
        |
        | independent Medical / Clinical / Regulatory review
        v
VALIDATED
        |
        v
NOT_MLR_REVIEWED
```

The new composed claim is deliberately excluded from promotional retrieval until
a future MLR workflow explicitly approves it.

## Composition sources

`GET /v1/claims/composition-sources`

Only evidence records with:

`review_status = SME_VALIDATED_EVIDENCE`

are eligible.

## Claim kinds

### EFFICACY_ENDPOINT

Requires validated:

- study
- intervention
- endpoint
- outcome

Comparator and population context are included when available.

The template does not invent an efficacy interpretation. It frames the validated
study context and embeds the exact validated source outcome sentence.

### SAFETY

Requires validated safety evidence plus study and intervention context. The
exact validated source safety sentence remains embedded in the claim.

## Support package

Every candidate stores:

- evidence structure ID
- evidence validation ID
- document ID
- chunk ID
- source file
- page number
- complete source text
- claim kind
- validated scientific fields used to compose the claim

The support package is stored with the candidate and copied unchanged to the
Medical-validated claim.

## Separation of duties

The actor who composes a claim cannot validate the same candidate.

A separate authorized Medical, Clinical, or Regulatory reviewer must make the
Medical claim decision.

Supported decisions:

- `VALIDATED`
- `REJECTED`
- `NEEDS_REVISION`

A `VALIDATED` decision also requires explicit authorization confirmation.

## APIs

### Compose

`POST /v1/claims/compose`

Example:

```json
{
  "structure_id": "EVI_...",
  "claim_kind": "EFFICACY_ENDPOINT"
}
```

### Pending candidates

`GET /v1/claims/composed-candidates?status=PENDING_MEDICAL_REVIEW`

### Medical review

`POST /v1/claims/composed-candidates/{candidate_id}/decisions`

### Medical-validated claims

`GET /v1/claims/medical-validated`

Validated claims are created with:

`approval_status = NOT_MLR_REVIEWED`

## Audit events

- `COMPOSED_CLAIM_CREATED`
- `COMPOSED_CLAIM_REVIEWED`

Both participate in the tenant hash-linked audit chain.

## Product UI

The **Claim composer** tab provides:

1. validated evidence-source selection;
2. efficacy or safety composition;
3. complete support-package inspection;
4. independent Medical review queue;
5. final list of Medical-validated claims awaiting MLR.

## Governance boundary

v1 does not:

- create a promotional approval;
- bypass MLR;
- alter the original validated evidence;
- allow unvalidated extraction to support a claim;
- use an LLM to generate unsupported medical conclusions.
