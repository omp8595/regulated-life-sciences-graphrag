from product_api.semantic.registry import (
    CONCEPTS,
    RELATIONSHIPS,
    CanonicalConcept,
    ExternalMapping,
    ResolvedConcept,
    SemanticRelationship,
)
from product_api.semantic.store import (
    concept_labels,
    get_concept,
    list_semantic_concepts,
    related_concept_ids,
    relationships_for_concepts,
    resolve_mentions,
    seed_semantic_master,
    semantic_match,
)


# The semantic package is loaded as part of the product API startup path. Register
# modular governance/query extensions once the base FastAPI application exists.
# This keeps the new workflows isolated while the prototype is still being
# modularized and preserves the existing API surface for backward compatibility.
def _register_product_extensions() -> None:
    try:
        from product_api.app import app
        from product_api.audience_query import register_audience_query_routes
        from product_api.mlr_activation import register_mlr_activation_routes

        register_mlr_activation_routes(app)
        register_audience_query_routes(app)
    except (ImportError, AttributeError):
        # Safe for standalone semantic-library imports where the FastAPI app is
        # not part of the current process.
        return


_register_product_extensions()


__all__ = [
    "CONCEPTS",
    "RELATIONSHIPS",
    "CanonicalConcept",
    "ExternalMapping",
    "ResolvedConcept",
    "SemanticRelationship",
    "concept_labels",
    "get_concept",
    "list_semantic_concepts",
    "related_concept_ids",
    "relationships_for_concepts",
    "resolve_mentions",
    "seed_semantic_master",
    "semantic_match",
]
