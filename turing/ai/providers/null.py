from __future__ import annotations

"""Null LLM provider — deterministic unavailable answers (Phase 4.5.6)."""

from typing import Any, Sequence

from turing.ai.base import LLMGeneration, LLMMessage, LLMProvider
from turing.ai.registry import LLMProviderRegistry

NULL_UNAVAILABLE_ANSWER = (
    "Answer generation is unavailable. No LLM provider is configured."
)


@LLMProviderRegistry.register
class NullLLMProvider(LLMProvider):
    """
    Default RAG LLM — keeps the retrieval pipeline testable without network
    or vendor SDKs.
    """

    code = "null"
    display_name = "Null (unavailable)"

    def model_name(self) -> str:
        return "null"

    def generate(
        self,
        messages: Sequence[LLMMessage] | Sequence[dict[str, str]],
        context: str | dict[str, Any] | None = None,
    ) -> LLMGeneration:
        _ = messages, context
        return LLMGeneration(
            text=NULL_UNAVAILABLE_ANSWER,
            model_name=self.model_name(),
            provider=self.code,
            details={"available": False},
        )
