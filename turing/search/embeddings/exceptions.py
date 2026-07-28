from __future__ import annotations

"""Embedding provider errors (Phase 4.5.5)."""


class EmbeddingProviderError(Exception):
    """Base embedding-provider error."""


class EmbeddingProviderConfigurationError(EmbeddingProviderError):
    """Invalid embedding provider configuration."""


class EmbeddingProviderNotFoundError(EmbeddingProviderError):
    """Requested embedding provider code is not registered."""
