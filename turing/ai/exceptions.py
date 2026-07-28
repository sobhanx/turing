from __future__ import annotations

"""LLM / RAG provider errors (Phase 4.5.6)."""


class LLMProviderError(Exception):
    """Base LLM provider error."""


class LLMProviderConfigurationError(LLMProviderError):
    """Invalid LLM provider configuration."""


class LLMProviderNotFoundError(LLMProviderError):
    """Requested LLM provider code is not registered."""


class RAGError(Exception):
    """Base RAG pipeline error."""


class RAGConfigurationError(RAGError):
    """RAG misconfiguration."""
