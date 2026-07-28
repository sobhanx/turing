from __future__ import annotations

"""Semantic search package (Phase 4.5.3)."""

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
from turing.search.registry import SemanticSearchRegistry

__all__ = [
    "NullSemanticSearchProvider",
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
    """Idempotently register shipped search providers (null placeholder)."""
    if "null" not in SemanticSearchRegistry.codes():
        SemanticSearchRegistry.register(NullSemanticSearchProvider)
        SemanticSearchRegistry.set_default("null")
