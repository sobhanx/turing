from __future__ import annotations

"""
PostgreSQL pgvector-oriented semantic search provider (Phase 4.5.4).

Stores float vectors on ``Embedding.vector`` (JSON list — portable across
SQLite test DBs and Postgres). When running on PostgreSQL with the ``vector``
extension available, search can optionally use SQL distance operators; otherwise
cosine similarity is computed in Python over org-scoped rows.
"""

import logging
from typing import Any

from django.conf import settings
from django.db import connection

from turing.models import Embedding
from turing.models.embedding import EmbeddingObjectType
from turing.search.base import SearchDocument, SearchHit, SearchResult, SemanticSearchProvider
from turing.search.embedder import cosine_similarity, embed_text

logger = logging.getLogger(__name__)


def _default_dimensions() -> int:
    return int(getattr(settings, "TURING_SEARCH_EMBEDDING_DIMS", 256) or 256)


class PgVectorSearchProvider(SemanticSearchProvider):
    """
    First production-oriented vector provider.

    - ``index_document`` generates an embedding and upserts ``Embedding``
    - ``search`` ranks org-scoped rows by cosine similarity
    - Never crosses organization boundaries
    """

    code = "pgvector"
    display_name = "PostgreSQL pgvector"

    def __init__(self, *, dimensions: int | None = None) -> None:
        self.dimensions = int(dimensions) if dimensions is not None else _default_dimensions()

    def embed(self, text: str) -> list[float]:
        """Generate a vector for ``text`` (provider-owned embed step)."""
        return embed_text(text, dimensions=self.dimensions)

    def index_document(self, document: SearchDocument) -> None:
        vector = self.embed(document.text)
        metadata = dict(document.metadata or {})
        # Persist text for API responses (never secrets).
        metadata.setdefault("text", document.text)
        metadata["dimensions"] = self.dimensions

        Embedding.objects.update_or_create(
            organization_id=document.organization_id,
            object_type=document.object_type or EmbeddingObjectType.TRANSCRIPT_SEGMENT,
            object_id=document.object_id,
            defaults={
                "content_hash": document.content_hash or "",
                "vector": vector,
                "dimensions": self.dimensions,
                "metadata": metadata,
            },
        )

    def delete_document(self, document_id: str, *, organization_id: int | None = None) -> None:
        object_type, _, object_id = document_id.partition(":")
        if not object_id:
            object_type = EmbeddingObjectType.TRANSCRIPT_SEGMENT
            object_id = document_id
        qs = Embedding.objects.filter(object_type=object_type, object_id=object_id)
        if organization_id is not None:
            qs = qs.filter(organization_id=organization_id)
        qs.delete()

    def search(
        self,
        query: str,
        *,
        organization_id: int,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> SearchResult:
        query = (query or "").strip()
        limit = max(1, min(int(limit or 20), 100))
        filters = dict(filters or {})

        # Hard org isolation — never query without organization_id.
        qs = Embedding.objects.filter(organization_id=organization_id).exclude(
            vector=[]
        )
        qs = self._apply_filters(qs, filters)

        if not query:
            return SearchResult(hits=[], provider=self.code, total=0)

        query_vec = self.embed(query)
        hits = self._rank(qs, query_vec, limit=limit)
        return SearchResult(
            hits=hits,
            provider=self.code,
            total=len(hits),
            details={
                "backend": (
                    "pgvector_sql"
                    if self._can_use_pgvector_sql()
                    else "python_cosine"
                ),
            },
        )

    def _apply_filters(self, qs, filters: dict[str, Any]):
        external_system = (filters.get("external_system") or "").strip()
        external_type = (filters.get("external_type") or "").strip()
        external_id = (filters.get("external_id") or "").strip()
        if not (external_system or external_type or external_id):
            return qs

        # Filter in Python over metadata snapshot for portability (JSON contains
        # varies by backend). Still org-scoped via qs.
        matched_ids: list[str] = []
        for row in qs.only("id", "metadata").iterator():
            refs = (row.metadata or {}).get("external_references") or []
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                if external_system and ref.get("external_system") != external_system:
                    continue
                if external_type and ref.get("external_type") != external_type:
                    continue
                if external_id and ref.get("external_id") != external_id:
                    continue
                matched_ids.append(str(row.id))
                break
        return qs.filter(id__in=matched_ids)

    def _rank(self, qs, query_vec: list[float], *, limit: int) -> list[SearchHit]:
        if self._can_use_pgvector_sql():
            try:
                return self._rank_pgvector_sql(qs, query_vec, limit=limit)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "pgvector SQL ranking failed; falling back to Python cosine"
                )

        scored: list[tuple[float, Embedding]] = []
        for row in qs.iterator():
            vec = row.vector if isinstance(row.vector, list) else []
            score = cosine_similarity(query_vec, vec)
            if score <= 0:
                continue
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits: list[SearchHit] = []
        for score, row in scored[:limit]:
            hits.append(
                SearchHit(
                    document_id=f"{row.object_type}:{row.object_id}",
                    score=round(float(score), 6),
                    object_type=row.object_type,
                    object_id=row.object_id,
                    metadata=dict(row.metadata or {}),
                )
            )
        return hits

    def _can_use_pgvector_sql(self) -> bool:
        if connection.vendor != "postgresql":
            return False
        return bool(getattr(settings, "TURING_SEARCH_PGVECTOR_SQL", False))

    def _rank_pgvector_sql(self, qs, query_vec: list[float], *, limit: int) -> list[SearchHit]:
        """
        Optional Postgres path: cosine distance via pgvector when enabled.

        Requires ``CREATE EXTENSION vector`` and JSON vectors castable to
        ``vector``. Disabled by default so SQLite CI stays green.
        """
        org_ids = list(qs.values_list("id", flat=True)[:5000])
        if not org_ids:
            return []
        vector_literal = "[" + ",".join(str(float(x)) for x in query_vec) + "]"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id,
                       1 - (vector::text::vector <=> %s::vector) AS score
                FROM turing_embedding
                WHERE id = ANY(%s)
                ORDER BY vector::text::vector <=> %s::vector
                LIMIT %s
                """,
                [vector_literal, org_ids, vector_literal, limit],
            )
            rows = cursor.fetchall()
        id_to_score = {str(r[0]): float(r[1]) for r in rows}
        embeddings = {
            str(e.id): e
            for e in Embedding.objects.filter(id__in=list(id_to_score.keys()))
        }
        hits: list[SearchHit] = []
        for emb_id, score in id_to_score.items():
            row = embeddings.get(emb_id)
            if row is None:
                continue
            hits.append(
                SearchHit(
                    document_id=f"{row.object_type}:{row.object_id}",
                    score=round(score, 6),
                    object_type=row.object_type,
                    object_id=row.object_id,
                    metadata=dict(row.metadata or {}),
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits
