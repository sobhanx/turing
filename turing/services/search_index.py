from __future__ import annotations

"""
Transcript chunk indexing for Speech Center semantic search (Phase 4.5.3).

Indexes transcript *segments* (not full transcript only). Failures are logged
and never raised into the STT / analysis pipeline.
"""

import hashlib
import logging
from typing import Any, Iterable

from django.db import transaction

from turing.models import Embedding, ExternalReference, Transcript, TranscriptSegment
from turing.models.embedding import EmbeddingObjectType
from turing.search.base import SearchDocument, SemanticSearchProvider
from turing.search.exceptions import SemanticSearchIndexError
from turing.search.registry import SemanticSearchRegistry

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _external_references_snapshot(transcript: Transcript) -> list[dict[str, str]]:
    refs = ExternalReference.objects.filter(
        organization_id=transcript.organization_id,
    ).filter(
        models_q_media_or_transcript(transcript)
    ).order_by("-created_at")[:50]
    return [
        {
            "external_system": ref.external_system,
            "external_type": ref.external_type,
            "external_id": ref.external_id,
        }
        for ref in refs
    ]


def models_q_media_or_transcript(transcript: Transcript):
    from django.db.models import Q

    q = Q(transcript_id=transcript.pk)
    if transcript.media_id:
        q = q | Q(media_id=transcript.media_id)
    return q


class SearchIndexService:
    """Index / remove transcript segment embeddings via the active search provider."""

    def __init__(self, provider: SemanticSearchProvider | None = None) -> None:
        self._provider = provider

    @property
    def provider(self) -> SemanticSearchProvider:
        if self._provider is None:
            self._provider = SemanticSearchRegistry.create()
        return self._provider

    def index_transcript(self, transcript: Transcript) -> int:
        """Index all segments for a transcript. Returns number of chunks indexed."""
        segments = list(
            transcript.segments.select_related("speaker").order_by("sequence", "start_ms")
        )
        return self.index_segments(transcript, segments)

    def index_segments(
        self,
        transcript: Transcript,
        segments: Iterable[TranscriptSegment] | None = None,
    ) -> int:
        """
        Upsert Embedding rows + provider documents for transcript segments.

        Metadata includes transcript_id, media_id, speaker, timestamps, and an
        external_references snapshot.
        """
        if transcript.organization_id is None:
            raise SemanticSearchIndexError("Transcript has no organization.")

        if segments is None:
            segment_list = list(
                transcript.segments.select_related("speaker").order_by(
                    "sequence", "start_ms"
                )
            )
        else:
            segment_list = list(segments)

        ext_snapshot = _external_references_snapshot(transcript)
        media_id = str(transcript.media_id) if transcript.media_id else ""
        indexed = 0

        for segment in segment_list:
            text = (segment.text or "").strip()
            if not text:
                continue
            object_id = str(segment.id)
            content_hash = _content_hash(text)
            speaker_label = ""
            if segment.speaker_id and segment.speaker is not None:
                speaker_label = segment.speaker.resolved_name or segment.speaker.label or ""

            metadata: dict[str, Any] = {
                "transcript_id": str(transcript.id),
                "media_id": media_id,
                "segment_id": object_id,
                "sequence": segment.sequence,
                "speaker": speaker_label,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "external_references": ext_snapshot,
            }
            document_id = f"transcript_segment:{object_id}"

            with transaction.atomic():
                Embedding.objects.update_or_create(
                    organization_id=transcript.organization_id,
                    object_type=EmbeddingObjectType.TRANSCRIPT_SEGMENT,
                    object_id=object_id,
                    defaults={
                        "content_hash": content_hash,
                        "vector": [],
                        "metadata": metadata,
                    },
                )
                self.provider.index_document(
                    SearchDocument(
                        document_id=document_id,
                        organization_id=transcript.organization_id,
                        object_type=EmbeddingObjectType.TRANSCRIPT_SEGMENT,
                        object_id=object_id,
                        text=text,
                        content_hash=content_hash,
                        metadata=metadata,
                    )
                )
            indexed += 1
        return indexed

    def remove_index(self, transcript: Transcript | str) -> int:
        """Remove Embedding rows + provider docs for a transcript's segments."""
        if isinstance(transcript, str):
            try:
                transcript = Transcript.objects.get(pk=transcript)
            except Transcript.DoesNotExist:
                return 0

        segment_ids = list(
            transcript.segments.values_list("id", flat=True)
        )
        if not segment_ids:
            # Also clean any stale rows keyed by transcript metadata.
            qs = Embedding.objects.filter(
                organization_id=transcript.organization_id,
                object_type=EmbeddingObjectType.TRANSCRIPT_SEGMENT,
                metadata__transcript_id=str(transcript.id),
            )
        else:
            qs = Embedding.objects.filter(
                organization_id=transcript.organization_id,
                object_type=EmbeddingObjectType.TRANSCRIPT_SEGMENT,
                object_id__in=[str(i) for i in segment_ids],
            )

        removed = 0
        for row in qs:
            document_id = f"{row.object_type}:{row.object_id}"
            try:
                self.provider.delete_document(
                    document_id,
                    organization_id=row.organization_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Search provider delete failed document_id=%s (continuing)",
                    document_id,
                )
            row.delete()
            removed += 1
        return removed
