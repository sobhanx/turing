from __future__ import annotations

from typing import Type

from turing.search.embeddings.base import EmbeddingProvider
from turing.search.embeddings.exceptions import (
    EmbeddingProviderConfigurationError,
    EmbeddingProviderNotFoundError,
)


class EmbeddingProviderRegistry:
    """Strategy registry for embedding providers."""

    _providers: dict[str, Type[EmbeddingProvider]] = {}
    _default_code: str = "local"

    @classmethod
    def register(
        cls, provider_cls: Type[EmbeddingProvider]
    ) -> Type[EmbeddingProvider]:
        code = getattr(provider_cls, "code", None) or ""
        if not code:
            raise EmbeddingProviderConfigurationError(
                "Embedding provider must define a non-empty 'code'."
            )
        cls._providers[code] = provider_cls
        return provider_cls

    @classmethod
    def get(cls, code: str | None = None) -> Type[EmbeddingProvider]:
        key = (code or cls._default_code or "").strip() or cls._default_code
        provider_cls = cls._providers.get(key)
        if provider_cls is None:
            raise EmbeddingProviderNotFoundError(
                f"Unknown embedding provider '{key}'."
            )
        return provider_cls

    @classmethod
    def create(cls, code: str | None = None, **kwargs) -> EmbeddingProvider:
        """
        Instantiate a provider.

        Unknown codes fall back to ``null`` (never raise for host typos).
        """
        key = (code if code is not None else cls._default_code) or ""
        key = str(key).strip() or cls._default_code
        provider_cls = cls._providers.get(key)
        if provider_cls is None:
            null_cls = cls._providers.get("null")
            if null_cls is None:
                raise EmbeddingProviderNotFoundError(
                    f"Unknown embedding provider '{key}' and null is not registered."
                )
            return null_cls()
        return provider_cls(**kwargs)

    @classmethod
    def codes(cls) -> list[str]:
        return sorted(cls._providers.keys())

    @classmethod
    def set_default(cls, code: str) -> None:
        if code not in cls._providers:
            raise EmbeddingProviderNotFoundError(
                f"Unknown embedding provider '{code}'."
            )
        cls._default_code = code

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (tests)."""
        cls._providers.clear()
        cls._default_code = "local"
