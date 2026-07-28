from __future__ import annotations

"""
Retrieval-augmented generation foundation (Phase 4.5.6 / 4.5.7).

Retrieves org-scoped Speech Center segments via ``SemanticSearchProvider``,
builds grounded context, and asks an ``LLMProvider`` for an answer.
"""

import logging
from typing import Any

from turing.ai.base import LLMMessage, LLMProvider
from turing.ai.providers.null import NullLLMProvider
from turing.ai.registry import LLMProviderRegistry
from turing.models import Organization
from turing.search.base import SearchHit, SemanticSearchProvider
from turing.search.registry import SemanticSearchRegistry

logger = logging.getLogger(__name__)


class RAGService:
    """Retrieve → build context → generate grounded answer."""

    def __init__(
        self,
        *,
        search_provider: SemanticSearchProvider | None = None,
        llm_provider: LLMProvider | None = None,
        limit: int = 8,
    ) -> None:
        self._search_provider = search_provider
        self._llm_provider = llm_provider
        self.limit = max(1, min(int(limit or 8), 50))

    @property
    def search_provider(self) -> SemanticSearchProvider:
        if self._search_provider is None:
            self._search_provider = SemanticSearchRegistry.create()
        return self._search_provider

    @property
    def llm_provider(self) -> LLMProvider:
        if self._llm_provider is None:
            self._llm_provider = LLMProviderRegistry.create()
        return self._llm_provider

    def retrieve_context(
        self,
        query: str,
        organization: Organization | int,
        filters: dict[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Run org-scoped semantic search and return structured source rows.

        Never crosses organization boundaries — ``organization_id`` is always set.
        """
        org_id = (
            organization.id if isinstance(organization, Organization) else int(organization)
        )
        query = (query or "").strip()
        if not query:
            return []

        result = self.search_provider.search(
            query,
            organization_id=org_id,
            limit=limit if limit is not None else self.limit,
            filters=dict(filters or {}),
        )
        return [self._hit_to_source(hit) for hit in result.hits]

    def build_context(self, results: list[dict[str, Any]] | list[SearchHit]) -> str:
        """
        Format retrieval results into an LLM context block.

        Includes transcript/segment ids, speaker, timestamps, external
        references, and relevant text — only for the retrieved hits.
        """
        if not results:
            return ""

        blocks: list[str] = []
        for i, row in enumerate(results, start=1):
            if isinstance(row, SearchHit):
                source = self._hit_to_source(row)
            else:
                source = dict(row)
            ts = source.get("timestamp") or {}
            refs = source.get("external_references") or []
            ref_bits = []
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                ref_bits.append(
                    f"{ref.get('external_system', '')}/"
                    f"{ref.get('external_type', '')}/"
                    f"{ref.get('external_id', '')}"
                )
            blocks.append(
                "\n".join(
                    [
                        f"[source {i}]",
                        f"transcript_id: {source.get('transcript_id', '')}",
                        f"segment_id: {source.get('segment_id', '')}",
                        f"speaker: {source.get('speaker', '')}",
                        f"speaker_label: {source.get('speaker_label', '')}",
                        f"speaker_name: {source.get('speaker_name', '')}",
                        f"start_ms: {ts.get('start_ms', '')}",
                        f"end_ms: {ts.get('end_ms', '')}",
                        f"external_references: {', '.join(ref_bits) or '(none)'}",
                        f"text: {source.get('text', '')}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    def answer(
        self,
        query: str,
        organization: Organization | int,
        *,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Full RAG pipeline: retrieve → build context → LLM generate.

        Returns ``{answer, sources, provider, model_name}``.
        Falls back to ``NullLLMProvider`` when generation fails.
        """
        sources = self.retrieve_context(
            query, organization, filters=filters, limit=limit
        )
        context = self.build_context(sources)
        llm = self.llm_provider
        messages = [
            LLMMessage(
                role="system",
                content=(
                    "Answer using only the provided Speech Center context. "
                    "If context is empty, say you do not have enough information."
                ),
            ),
            LLMMessage(role="user", content=(query or "").strip()),
        ]
        generation = self._generate_with_fallback(llm, messages, context)
        api_sources = [
            {
                "transcript_id": s.get("transcript_id", ""),
                "segment_id": s.get("segment_id", ""),
                "score": s.get("score", 0.0),
                "timestamp": s.get("timestamp") or {},
            }
            for s in sources
        ]
        return {
            "answer": generation.text,
            "sources": api_sources,
            "provider": generation.provider or llm.code,
            "model_name": generation.model_name or llm.model_name(),
        }

    def _generate_with_fallback(self, llm: LLMProvider, messages, context):
        try:
            return llm.generate(messages, context=context)
        except Exception:  # noqa: BLE001
            # Never log messages/context (transcript PII). Provider code only.
            logger.exception(
                "LLM generate failed provider=%s; falling back to null",
                getattr(llm, "code", "") or "unknown",
            )
            if isinstance(llm, NullLLMProvider):
                raise
            return NullLLMProvider().generate(messages, context=context)

    def _hit_to_source(self, hit: SearchHit) -> dict[str, Any]:
        meta = hit.metadata or {}
        return {
            "transcript_id": meta.get("transcript_id") or "",
            "segment_id": meta.get("segment_id") or hit.object_id,
            "speaker": (
                meta.get("speaker_name")
                or meta.get("speaker")
                or meta.get("speaker_label")
                or ""
            ),
            "speaker_label": meta.get("speaker_label") or "",
            "speaker_name": meta.get("speaker_name") or "",
            "timestamp": {
                "start_ms": meta.get("start_ms"),
                "end_ms": meta.get("end_ms"),
            },
            "external_references": meta.get("external_references") or [],
            "text": meta.get("text") or "",
            "score": hit.score,
        }
