from __future__ import annotations

"""
Provider-agnostic semantic search contract (Phase 4.5.3).

Concrete vector backends (pgvector, OpenSearch, Pinecone, etc.) implement
``SemanticSearchProvider``. Core services never import a vendor SDK.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True)
class SearchDocument:
    """Unit of content to index (never includes secrets)."""

    document_id: str
    organization_id: int
    object_type: str
    object_id: str
    text: str
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    vector: list[float] | None = None


@dataclass(frozen=True)
class SearchHit:
    """One search result (provider-neutral)."""

    document_id: str
    score: float = 0.0
    object_type: str = ""
    object_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Outcome of ``SemanticSearchProvider.search()``."""

    hits: list[SearchHit] = field(default_factory=list)
    provider: str = ""
    total: int = 0
    details: dict[str, Any] = field(default_factory=dict)


class SemanticSearchProvider(ABC):
    """
    Vendor-agnostic semantic search provider.

    Implementations register with ``SemanticSearchRegistry`` under ``code``.
    """

    code: ClassVar[str] = ""
    display_name: ClassVar[str] = ""

    @abstractmethod
    def index_document(self, document: SearchDocument) -> None:
        """Upsert a document into the provider index."""

    @abstractmethod
    def delete_document(self, document_id: str, *, organization_id: int | None = None) -> None:
        """Remove a document from the provider index (idempotent)."""

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        organization_id: int,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> SearchResult:
        """Run a semantic (or placeholder) search within an organization."""


class NullSemanticSearchProvider(SemanticSearchProvider):
    """
    Default no-op provider — foundation only, no production vector lock-in.

    Persists nothing remotely; ``SearchIndexService`` still writes Embedding rows.
    """

    code = "null"
    display_name = "Null (placeholder)"

    def index_document(self, document: SearchDocument) -> None:
        return None

    def delete_document(self, document_id: str, *, organization_id: int | None = None) -> None:
        return None

    def search(
        self,
        query: str,
        *,
        organization_id: int,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> SearchResult:
        return SearchResult(hits=[], provider=self.code, total=0, details={"indexed": False})
