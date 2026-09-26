# Audience-Aware Governed Query Policy

## Why audience is a first-class policy input

A promotional approval is not defined only by scientific validity or market. The permitted use may also depend on the intended audience, such as an HCP, patient, payer, or internal user.

The contextual runtime therefore evaluates:

```text
Role + Purpose + Market + Audience + Approval state + Validity window
```

before returning a governed promotional answer.

## Contextual query contract

`POST /v1/query/contextual`

Example:

```json
{
  "question": "What evidence supports BRUKINSA in relapsed/refractory CLL?",
  "purpose": "PROMOTIONAL_CONTENT",
  "market": "US",
  "audience": "HCP",
  "top_k": 3
}
```

The legacy `POST /v1/query` endpoint remains backward compatible. Because it does not carry audience, it deliberately fails closed for approvals narrower than `audience=ALL`.

## Promotional decision rule

An evidence-bound composed claim can be returned for promotional use only when a currently effective MLR activation matches:

- tenant;
- purpose;
- market, or a `Global` approval that covers the market;
- audience, or an `ALL` audience approval;
- current effective/expiry window;
- active activation state.

The returned promotional answer is the exact MLR-approved wording.

## Audience examples

### HCP approval + HCP request

```text
Approval audience: HCP
Query audience:    HCP
→ eligible when all other policy dimensions match
```

### HCP approval + patient request

```text
Approval audience: HCP
Query audience:    PATIENT
→ BLOCK
Reason: MLR_AUDIENCE_MISMATCH
```

### HCP approval + legacy query without audience

```text
Approval audience: HCP
Legacy query:      no audience context
→ BLOCK
```

The platform does not infer or guess an audience when the caller has not supplied it.

## Runtime outcomes

The contextual query preserves the North Star decision model:

- `ANSWERED` — a permitted governed answer exists;
- `EVIDENCE_ONLY` — relevant evidence is discoverable but not eligible as a governed claim;
- `ABSTAIN` — insufficient trusted support exists;
- `BLOCKED` — relevant knowledge exists but the requested use is not permitted.

Audience-specific block reasons include:

- `MLR_AUDIENCE_MISMATCH`
- `MLR_MARKET_MISMATCH`
- `MLR_PURPOSE_MISMATCH`
- `MLR_APPROVAL_NOT_CURRENT`
- `MLR_APPROVAL_NOT_ACTIVE`
- `MLR_APPROVAL_REQUIRED`

## Demo workspace

`product_journey_ui.py` is the recommended end-to-end demonstration workspace. It shows:

1. source ingestion;
2. audience-aware governed query;
3. field-level SME evidence review;
4. evidence-bound claim composition;
5. independent Medical claim review;
6. MLR submission scoped by market/purpose/audience;
7. independent MLR decision;
8. governed-use activation;
9. governance and audit status.

The UI is a prototype demonstration surface, not a validated production MLR system.
