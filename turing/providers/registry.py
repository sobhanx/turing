from __future__ import annotations

from typing import Type

from turing.domain.exceptions import ConfigurationError, NotFoundError
from turing.providers.base import STTProvider


class ProviderRegistry:
    """Strategy registry for STT providers."""

    _providers: dict[str, Type[STTProvider]] = {}

    @classmethod
    def register(cls, provider_cls: Type[STTProvider]) -> Type[STTProvider]:
        code = getattr(provider_cls, "code", None)
        if not code:
            raise ConfigurationError("Provider class must define a 'code' attribute.")
        cls._providers[code] = provider_cls
        return provider_cls

    @classmethod
    def get(cls, code: str) -> STTProvider:
        provider_cls = cls._providers.get(code)
        if not provider_cls:
            raise NotFoundError(f"Unknown speech provider '{code}'.")
        return provider_cls()

    @classmethod
    def codes(cls) -> list[str]:
        return sorted(cls._providers.keys())

    @classmethod
    def clear(cls) -> None:
        cls._providers.clear()
