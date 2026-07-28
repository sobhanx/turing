from __future__ import annotations

from typing import Type

from turing.search.base import SemanticSearchProvider
from turing.search.exceptions import (
    SemanticSearchConfigurationError,
    SemanticSearchProviderNotFoundError,
)


class SemanticSearchRegistry:
    """Strategy registry for semantic search providers (no vendor hardcoding)."""

    _providers: dict[str, Type[SemanticSearchProvider]] = {}
    _default_code: str = "pgvector"

    @classmethod
    def register(
        cls, provider_cls: Type[SemanticSearchProvider]
    ) -> Type[SemanticSearchProvider]:
        code = getattr(provider_cls, "code", None) or ""
        if not code:
            raise SemanticSearchConfigurationError(
                "Semantic search provider must define a non-empty 'code'."
            )
        cls._providers[code] = provider_cls
        return provider_cls

    @classmethod
    def get(cls, code: str | None = None) -> Type[SemanticSearchProvider]:
        key = (code or cls._default_code or "").strip() or cls._default_code
        provider_cls = cls._providers.get(key)
        if provider_cls is None:
            raise SemanticSearchProviderNotFoundError(
                f"Unknown semantic search provider '{key}'."
            )
        return provider_cls

    @classmethod
    def create(cls, code: str | None = None) -> SemanticSearchProvider:
        return cls.get(code)()

    @classmethod
    def codes(cls) -> list[str]:
        return sorted(cls._providers.keys())

    @classmethod
    def set_default(cls, code: str) -> None:
        if code not in cls._providers:
            raise SemanticSearchProviderNotFoundError(
                f"Unknown semantic search provider '{code}'."
            )
        cls._default_code = code

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (tests)."""
        cls._providers.clear()
        cls._default_code = "pgvector"
