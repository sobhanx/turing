from __future__ import annotations

"""Text → vector embedding providers (Phase 4.5.5)."""

from django.conf import settings

from turing.search.embeddings.base import EmbeddingProvider, NullEmbeddingProvider
from turing.search.embeddings.exceptions import (
    EmbeddingProviderConfigurationError,
    EmbeddingProviderError,
    EmbeddingProviderNotFoundError,
)
from turing.search.embeddings.local import DEFAULT_MODEL, LocalNeuralEmbeddingProvider
from turing.search.embeddings.registry import EmbeddingProviderRegistry

__all__ = [
    "DEFAULT_MODEL",
    "EmbeddingProvider",
    "EmbeddingProviderConfigurationError",
    "EmbeddingProviderError",
    "EmbeddingProviderNotFoundError",
    "EmbeddingProviderRegistry",
    "LocalNeuralEmbeddingProvider",
    "NullEmbeddingProvider",
    "register_builtin_embedding_providers",
]


def register_builtin_embedding_providers() -> None:
    """Idempotently register shipped embedding providers (local default)."""
    if "null" not in EmbeddingProviderRegistry.codes():
        EmbeddingProviderRegistry.register(NullEmbeddingProvider)
    if "local" not in EmbeddingProviderRegistry.codes():
        EmbeddingProviderRegistry.register(LocalNeuralEmbeddingProvider)

    configured = (
        getattr(settings, "TURING_EMBEDDING_PROVIDER", None) or "local"
    ).strip() or "local"
    if configured not in EmbeddingProviderRegistry.codes():
        # Unknown → null fallback (requirement).
        configured = "null"
    EmbeddingProviderRegistry.set_default(configured)
