# MLR + Governed Claim Activation v1

## Business issue

A scientifically valid claim is not automatically permissible for promotional or external use. The platform therefore separates Medical validation from MLR approval and from runtime activation.

## Root cause

Traditional RAG systems and simple approval flags collapse multiple governance questions into one state:

- Is the scientific evidence valid?
- Is the claim wording medically faithful?
- Is this wording approved for a specific market and purpose?
- Is the approval currently effective?

Those questions require separate decision objects and lineage.

## Recommendation implemented

MLR is represented as a separate workflow on top of `medical_validated_composed_claims`.

```text
SME_VALIDATED_EVIDENCE
        ↓
Evidence-bound candidate
        ↓
Independent Medical validation
        ↓
NOT_MLR_REVIEWED
        ↓
SUBMITTED_FOR_MLR
        ↓
APPROVED / APPROVED_WITH_CHANGES / REJECTED
        ↓
ACTIVE_FOR_GOVERNED_USE
```

## MLR review scope

Every submission carries:

- market;
- purpose;
- audience;
- submission rationale;
- claim ID;
- exact evidence support package inherited from the composed claim.

The evidence support package retains:

- evidence structure ID;
- evidence validation ID;
- source document ID;
- chunk ID;
- file/page lineage;
- validated scientific fields.

## Separation of duties

The actor who performed the independent Medical claim validation cannot approve the same claim in MLR, even if that actor is issued an MLR-capable role.

Approval also requires explicit authorization confirmation.

## Decisions

### `APPROVED`

The original Medical-validated wording becomes the approved wording.

### `APPROVED_WITH_CHANGES`

MLR stores a separate approved wording. The original Medical-validated claim text is not mutated.

### `REJECTED`

No activation is created.

## Effective period

Approvals require:

- `effective_from_utc`;
- `expires_at_utc`;
- expiry later than effective time.

Runtime retrieval evaluates the timestamps on every query. Expired approvals remain in history but are not eligible for governed promotional use.

## Activation

An approval creates an immutable activation object with:

- activation ID;
- claim ID;
- MLR review and decision IDs;
- market;
- purpose;
- audience;
- approved wording;
- conditions of use;
- effective/expiry timestamps;
- `ACTIVE_FOR_GOVERNED_USE` state.

A newer approval for the same claim and scope supersedes the previous active activation rather than overwriting history.

## Runtime policy

### Medical / Regulatory use

Independent Medical-validated composed claims can be used for non-promotional governed answers before MLR.

### Promotional use

Promotional retrieval can return a composed claim only when:

1. an MLR activation exists;
2. the activation purpose matches the query purpose;
3. the activation market matches the requested market or is Global;
4. the activation is currently effective and unexpired;
5. the approval scope is usable by the current query context;
6. retrieval finds the approved wording relevant.

The answer uses the **MLR-approved wording**, not a new LLM paraphrase.

If a relevant Medical-validated claim exists but no applicable active approval exists, the platform returns `BLOCKED` rather than falling back to raw evidence.

## Audience boundary in v1

The current `/v1/query` request does not yet include an explicit audience field. To avoid guessing audience, runtime retrieval only activates composed approvals scoped to `ALL` when audience is absent.

Audience-specific approvals are stored and visible in governance, but they should remain blocked until the query context is extended to carry audience explicitly.

This is a deliberate fail-closed control.

## API

### Submit Medical-validated claim to MLR

`POST /v1/claims/medical-validated/{claim_id}/mlr-submit`

### List pending composed-claim MLR reviews

`GET /v1/claims/mlr/reviews`

### Record MLR decision

`POST /v1/claims/mlr/reviews/{review_id}/decisions`

### Inspect claim activations

`GET /v1/claims/activations`

## Audit events

- `COMPOSED_CLAIM_SUBMITTED_MLR`
- `COMPOSED_CLAIM_MLR_DECIDED`

Both are included in the tenant hash-linked audit chain.

## Governance principle

> Scientific truth, Medical-valid wording, and permitted promotional use are separate governed states.

The model does not decide approval. Policy evaluates human approval objects and determines which wording is eligible at runtime.
