# Pharma Semantic Layer v1

## Objective

Turn the regulated GraphRAG prototype from keyword-aware retrieval into a
canonical pharma knowledge layer that can normalize different surface forms,
represent controlled domain relationships, and improve retrieval without
weakening the existing governance model.

The semantic layer is deliberately separate from SME validation and MLR
approval. It can normalize what an entity *is* and how curated concepts relate;
it cannot decide that a scientific statement is true, approved, or promotional.

## v1 architecture

```text
Source document
  -> chunking
  -> curated mention resolution
  -> canonical concept IDs
  -> typed semantic edges
  -> tenant-scoped knowledge graph
  -> lexical + semantic retrieval
  -> policy evaluation
  -> evidence only | governed answer | block | abstain
```

Implementation:

- `product_api/semantic/registry.py` owns canonical concepts, aliases and
  curated relationships.
- `product_api/worker.py` resolves mentions during ingestion and indexes
  canonical concept nodes plus typed edges.
- `product_api/retrieval.py` combines lexical similarity with direct concept
  overlap and one-hop relationship expansion.
- Governance, tenant isolation, SME validation and MLR approval remain
  authoritative after semantic retrieval.

## Canonical concepts in the initial slice

| Canonical ID | Type | Canonical name | Example aliases |
|---|---|---|---|
| `DRUG:ZANUBRUTINIB` | Drug | zanubrutinib | zanubrutinib |
| `BRAND:BRUKINSA` | Brand | BRUKINSA | brukinsa |
| `DRUG:IBRUTINIB` | Drug | ibrutinib | ibrutinib |
| `TRIAL:ALPINE` | Clinical trial | ALPINE | ALPINE study, ALPINE trial |
| `INDICATION:CLL` | Indication | chronic lymphocytic leukemia | CLL |
| `INDICATION:SLL` | Indication | small lymphocytic lymphoma | SLL |
| `TARGET:BTK` | Molecular target | Bruton's tyrosine kinase | BTK |

The seed set is intentionally narrow. It exists to prove the architecture
against the current BRUKINSA/ALPINE demo before expanding the ontology.

## Typed relationships

The v1 registry includes:

- `BRAND_OF`
- `EVALUATES`
- `COMPARES_WITH`
- `STUDIES_INDICATION`
- `TARGETS`

These are controlled semantic relationships, not generated conclusions.
Document ingestion creates relationship edges only when both endpoint concepts
are present in the evidence chunk. This keeps semantic enrichment traceable to
the ingested context.

## Retrieval behavior

Semantic matching has two levels:

1. **Direct match** — query and evidence resolve to the same canonical concept.
2. **Related match** — an evidence concept is one curated semantic hop from the
   query concept.

Direct matches receive full semantic weight. One-hop related matches receive a
lower weight. The semantic score is then combined with lexical similarity in
the existing hybrid retrieval path.

Example:

```text
Query:    BRUKINSA
Evidence: zanubrutinib
          |
          +-- BRAND:BRUKINSA --BRAND_OF--> DRUG:ZANUBRUTINIB
```

The evidence can now be discovered semantically even when exact lexical overlap
is absent. Policy checks still determine whether that evidence can be returned.

## Governance boundaries

Semantic resolution must never be treated as:

- proof that a scientific claim is true;
- an approved indication;
- MLR approval;
- promotional eligibility;
- clinical advice;
- a substitute for authoritative terminology or product-master governance.

The semantic layer proposes canonical identity and curated relationships. Human
review and governed claims remain separate lifecycle stages.

## Next expansion

After v1 is stable, extend in this order:

1. Persist semantic concepts and aliases as tenant-configurable master data
   rather than Python constants.
2. Add controlled external terminology mappings for drug, disease, study and
   publication identifiers.
3. Add endpoint, biomarker, mechanism, population, organization and publication
   concept types.
4. Add document-level provenance to semantic relationship edges.
5. Support multi-hop expansion with explicit hop caps and policy-aware edge
   classes.
6. Add semantic coverage, ambiguity and mapping-quality metrics to governance
   tests.
7. Introduce reviewer workflows for semantic-master changes so production
   mappings are versioned and auditable.
