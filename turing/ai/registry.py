from __future__ import annotations

from typing import Type

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
