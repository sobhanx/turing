from __future__ import annotations

"""Semantic search package (Phase 4.5.4)."""

from django.conf import settings

from turing.search.base import (
    NullSemanticSearchProvider,
    SearchDocument,
    SearchHit,
    SearchResult,
    SemanticSearchProvider,
)
from turing.search.exceptions import (
    SemanticSearchConfigurationError,
    SemanticSearchError,
    SemanticSearchIndexError,
    SemanticSearchProviderNotFoundError,
)
from turing.search.providers.pgvector import PgVectorSearchProvider
from turing.search.registry import SemanticSearchRegistry

__all__ = [
    "NullSemanticSearchProvider",
    "PgVectorSearchProvider",
    "SearchDocument",
    "SearchHit",
    "SearchResult",
    "SemanticSearchConfigurationError",
    "SemanticSearchError",
    "SemanticSearchIndexError",
    "SemanticSearchProvider",
    "SemanticSearchProviderNotFoundError",
    "SemanticSearchRegistry",
    "register_builtin_search_providers",
]


def register_builtin_search_providers() -> None:
    """Idempotently register shipped search providers (pgvector default)."""
    if "null" not in SemanticSearchRegistry.codes():
        SemanticSearchRegistry.register(NullSemanticSearchProvider)
    if "pgvector" not in SemanticSearchRegistry.codes():
        SemanticSearchRegistry.register(PgVectorSearchProvider)

    default = (
        getattr(settings, "TURING_SEARCH_PROVIDER", None) or "pgvector"
    ).strip() or "pgvector"
    if default not in SemanticSearchRegistry.codes():
        default = "pgvector" if "pgvector" in SemanticSearchRegistry.codes() else "null"
    SemanticSearchRegistry.set_default(default)
