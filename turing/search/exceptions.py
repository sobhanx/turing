from __future__ import annotations

"""Semantic search exceptions (Phase 4.5.3)."""


class SemanticSearchError(Exception):
    """Base error for semantic search operations."""

    def __init__(self, message: str, *, code: str = "semantic_search_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class SemanticSearchProviderNotFoundError(SemanticSearchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="search_provider_not_found")


class SemanticSearchConfigurationError(SemanticSearchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="search_configuration_error")


class SemanticSearchIndexError(SemanticSearchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="search_index_error")
