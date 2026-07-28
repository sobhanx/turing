from __future__ import annotations

"""
Embedding provider contract (Phase 4.5.5).

Separates *how text becomes a vector* from *how vectors are stored/searched*
(``SemanticSearchProvider`` / pgvector).
"""

from abc import ABC, abstractmethod
from typing import ClassVar


class EmbeddingProvider(ABC):
    """Vendor-agnostic text → vector provider."""

    code: ClassVar[str] = ""
    display_name: ClassVar[str] = ""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return an L2-oriented dense embedding for ``text``."""

    @abstractmethod
    def dimensions(self) -> int:
        """Fixed output dimensionality for this provider instance."""

    def model_name(self) -> str:
        """Configured model identifier (empty for null)."""
        return ""


class NullEmbeddingProvider(EmbeddingProvider):
    """No-op embedder — empty vectors (search ranks nothing)."""

    code = "null"
    display_name = "Null (no embeddings)"

    def embed(self, text: str) -> list[float]:
        return []

    def dimensions(self) -> int:
        return 0

    def model_name(self) -> str:
        return ""
