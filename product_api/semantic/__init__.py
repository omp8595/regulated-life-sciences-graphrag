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
# the composed-claim MLR extension once the base FastAPI application exists. This
# keeps the new governance workflow isolated from the legacy MLR tables while the
# prototype is still being modularized.
def _register_composed_mlr_extension() -> None:
    try:
        from product_api.app import app
        from product_api.mlr_activation import register_mlr_activation_routes

        register_mlr_activation_routes(app)
    except (ImportError, AttributeError):
        # Safe for standalone semantic-library imports where the FastAPI app is
        # not part of the current process.
        return


_register_composed_mlr_extension()


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
