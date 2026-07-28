from __future__ import annotations

"""
LLM provider contract for RAG answer generation (Phase 4.5.6).

Separate from transcript-intelligence ``AIProvider`` (summarize / topics /
action items). Core services never hardcode a vendor SDK.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Sequence


@dataclass(frozen=True)
class LLMMessage:
    """One chat-style message."""

    role: str
    content: str


@dataclass
class LLMGeneration:
    """Outcome of ``LLMProvider.generate()``."""

    text: str
    model_name: str = ""
    provider: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Vendor-agnostic LLM for RAG / grounded answers."""

    code: ClassVar[str] = ""
    display_name: ClassVar[str] = ""

    @abstractmethod
    def generate(
        self,
        messages: Sequence[LLMMessage] | Sequence[dict[str, str]],
        context: str | dict[str, Any] | None = None,
    ) -> LLMGeneration:
        """
        Produce an answer from ``messages`` grounded in ``context``.

        ``context`` is typically the string (or structured payload) built by
        ``RAGService.build_context``.
        """

    @abstractmethod
    def model_name(self) -> str:
        """Configured model identifier."""
