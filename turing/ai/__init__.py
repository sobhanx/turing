from __future__ import annotations

"""AI package — transcript intelligence + RAG LLM providers."""

from turing.ai.base import LLMGeneration, LLMMessage, LLMProvider
from turing.ai.exceptions import (
    LLMProviderConfigurationError,
    LLMProviderError,
    LLMProviderNotFoundError,
    RAGConfigurationError,
    RAGError,
)
from turing.ai.interfaces import AIProvider
from turing.ai.providers.null import NULL_UNAVAILABLE_ANSWER, NullLLMProvider
from turing.ai.providers.openai import OpenAILLMProvider
from turing.ai.registry import (
    AIProviderRegistry,
    LLMProviderRegistry,
    register_builtin_llm_providers,
)

__all__ = [
    "AIProvider",
    "AIProviderRegistry",
    "LLMGeneration",
    "LLMMessage",
    "LLMProvider",
    "LLMProviderConfigurationError",
    "LLMProviderError",
    "LLMProviderNotFoundError",
    "LLMProviderRegistry",
    "NULL_UNAVAILABLE_ANSWER",
    "NullLLMProvider",
    "OpenAILLMProvider",
    "RAGConfigurationError",
    "RAGError",
    "register_builtin_llm_providers",
]
