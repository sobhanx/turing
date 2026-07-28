from __future__ import annotations

from typing import Type

from django.conf import settings

from turing.ai.base import LLMProvider
from turing.ai.exceptions import (
    LLMProviderConfigurationError,
    LLMProviderNotFoundError,
)
from turing.ai.interfaces import AIProvider
from turing.domain.exceptions import ConfigurationError, NotFoundError


class AIProviderRegistry:
    """Strategy registry for transcript intelligence providers."""

    _providers: dict[str, Type[AIProvider]] = {}

    @classmethod
    def register(cls, provider_cls: Type[AIProvider]) -> Type[AIProvider]:
        code = getattr(provider_cls, "code", None)
        if not code:
            raise ConfigurationError("AI provider class must define a 'code' attribute.")
        cls._providers[code] = provider_cls
        return provider_cls

    @classmethod
    def get(cls, code: str) -> AIProvider:
        provider_cls = cls._providers.get(code)
        if not provider_cls:
            raise NotFoundError(f"Unknown AI provider '{code}'.")
        return provider_cls()

    @classmethod
    def codes(cls) -> list[str]:
        return sorted(cls._providers.keys())

    @classmethod
    def clear(cls) -> None:
        cls._providers.clear()


class LLMProviderRegistry:
    """Strategy registry for RAG LLM providers (Phase 4.5.6)."""

    _providers: dict[str, Type[LLMProvider]] = {}
    _default_code: str = "null"

    @classmethod
    def register(cls, provider_cls: Type[LLMProvider]) -> Type[LLMProvider]:
        code = getattr(provider_cls, "code", None) or ""
        if not code:
            raise LLMProviderConfigurationError(
                "LLM provider must define a non-empty 'code'."
            )
        cls._providers[code] = provider_cls
        return provider_cls

    @classmethod
    def get(cls, code: str | None = None) -> Type[LLMProvider]:
        key = (code or cls._default_code or "").strip() or cls._default_code
        provider_cls = cls._providers.get(key)
        if provider_cls is None:
            raise LLMProviderNotFoundError(f"Unknown LLM provider '{key}'.")
        return provider_cls

    @classmethod
    def create(cls, code: str | None = None, **kwargs) -> LLMProvider:
        """Instantiate a provider; unknown codes fall back to ``null``."""
        key = (code if code is not None else cls._default_code) or ""
        key = str(key).strip() or cls._default_code
        provider_cls = cls._providers.get(key)
        if provider_cls is None:
            null_cls = cls._providers.get("null")
            if null_cls is None:
                raise LLMProviderNotFoundError(
                    f"Unknown LLM provider '{key}' and null is not registered."
                )
            return null_cls()
        return provider_cls(**kwargs)

    @classmethod
    def codes(cls) -> list[str]:
        return sorted(cls._providers.keys())

    @classmethod
    def set_default(cls, code: str) -> None:
        if code not in cls._providers:
            raise LLMProviderNotFoundError(f"Unknown LLM provider '{code}'.")
        cls._default_code = code

    @classmethod
    def clear(cls) -> None:
        cls._providers.clear()
        cls._default_code = "null"


def register_builtin_llm_providers() -> None:
    """Idempotently register shipped LLM providers (null + openai)."""
    from turing.ai.providers.null import NullLLMProvider
    from turing.ai.providers.openai import OpenAILLMProvider

    if "null" not in LLMProviderRegistry.codes():
        LLMProviderRegistry.register(NullLLMProvider)
    if "openai" not in LLMProviderRegistry.codes():
        LLMProviderRegistry.register(OpenAILLMProvider)

    configured = (
        getattr(settings, "TURING_LLM_PROVIDER", None) or "null"
    ).strip() or "null"
    if configured not in LLMProviderRegistry.codes():
        configured = "null"
    LLMProviderRegistry.set_default(configured)
