from __future__ import annotations

"""Minimal injected client for API-key STT providers (not Speechmatics HTTP)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiKeyClient:
    """
    Bearer-style API key holder for sticky credential injection.

    Speechmatics keeps its own ``SpeechmaticsClient``. Other API-key STT
    adapters may accept this (or any object with ``api_key``) via
    ``ProviderRegistry.get(code, client=...)``.
    """

    api_key: str
