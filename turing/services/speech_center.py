from __future__ import annotations

"""
Speech Center query service (Phase 4.5.1).

Aggregates media + transcript + speakers + analyses for host Speech Center UIs.
Never invents search/vector/sentiment — reuses existing transcript intelligence.
"""

from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Prefetch

from turing.auth.tenancy import assert_organization_access
from turing.domain.enums import AnalysisType
from turing.domain.exceptions import NotFoundError, ValidationError
from turing.models import (
    ExternalReference,
    MediaAsset,
    Organization,
    Transcript,
    TranscriptAnalysis,
    TranscriptSegment,
)
from turing.services.external_reference import ExternalReferenceService
from turing.services.transcript_analysis import TranscriptAnalysisService


class SpeechCenterService:
    """Unified speech object access for host applications."""

    def get_by_external_reference(
        self,
        *,
        organization: Organization,
        external_system: str,
        external_type: str,
        external_id: str,
        user: AbstractBaseUser | None = None,
    ) -> dict[str, Any]:
        """
        Resolve a host object key to a Speech Center context payload.

        Prefers a transcript-linked external reference; otherwise uses media and
        the primary (or latest) transcript when available.
        """
        if user is not None:
            assert_organization_access(
                user,
                organization,
                capability="view_transcript",
            )

        refs = list(
            ExternalReferenceService().lookup(
                organization=organization,
                external_system=external_system,
                external_type=external_type,
                external_id=external_id,
                user=user,
            )
        )
        if not refs:
            raise NotFoundError(
                "No speech object found for the given external reference."
            )

        transcript: Transcript | None = None
        media: MediaAsset | None = None
        for ref in refs:
            if ref.transcript_id:
                transcript = ref.transcript
                media = transcript.media if transcript is not None else None
                break
        if transcript is None:
            for ref in refs:
                if ref.media_id:
                    media = ref.media
                    transcript = self._primary_transcript_for_media(media)
                    break

        if media is None and transcript is None:
            raise NotFoundError(
                "No speech object found for the given external reference."
            )

        if transcript is not None:
            return self.get_transcript_context(transcript, user=user)

        return self._context_for_media_only(media, user=user)

    def get_transcript_context(
        self,
        transcript: Transcript,
        *,
        user: AbstractBaseUser | None = None,
    ) -> dict[str, Any]:
        """Build the unified Speech Center payload for a transcript."""
        if user is not None:
            assert_organization_access(
                user,
                transcript.organization,
                capability="view_transcript",
            )

        transcript = (
            Transcript.objects.select_related("media", "organization", "job")
            .prefetch_related(
                "speakers",
                Prefetch(
                    "segments",
                    queryset=TranscriptSegment.objects.select_related("speaker").order_by(
                        "sequence", "start_ms"
                    ),
                ),
                "external_references",
                "media__external_references",
            )
            .get(pk=transcript.pk)
        )
        media = transcript.media
        analyses = self.get_available_intelligence(transcript, user=user)
        external_references = self._collect_external_references(media, transcript)

        return {
            "media": media,
            "transcript": transcript,
            "status": transcript.status,
            "speakers": list(transcript.speakers.all()),
            "analyses": analyses,
            "external_references": external_references,
        }

    def get_available_intelligence(
        self,
        transcript: Transcript,
        *,
        user: AbstractBaseUser | None = None,
    ) -> dict[str, TranscriptAnalysis | None]:
        """
        Latest analysis row per type (summary / topics / action_items).

        Missing types are ``None`` (hosts can poll while generation runs).
        """
        if user is not None:
            assert_organization_access(
                user,
                transcript.organization,
                capability="view_transcript",
            )

        service = TranscriptAnalysisService()
        result: dict[str, TranscriptAnalysis | None] = {
            AnalysisType.SUMMARY: None,
            AnalysisType.TOPICS: None,
            AnalysisType.ACTION_ITEMS: None,
        }
        for analysis_type in result:
            try:
                result[analysis_type] = service.latest_by_type(
                    transcript,
                    analysis_type=analysis_type,
                    user=None,  # already checked above
                )
            except NotFoundError:
                result[analysis_type] = None
        return result

    def get_latest_intelligence(
        self,
        transcript: Transcript,
        *,
        user: AbstractBaseUser | None = None,
    ) -> dict[str, Any]:
        """
        Host-ready intelligence layer using latest-per-type semantics.

        Returns unified structure::

            {
              "summary": <content>|None,
              "topics": <content>|None,
              "action_items": <content>|None,
              "metadata": {...},
              "generated_at": <datetime>|None,
            }
        """
        if user is not None:
            assert_organization_access(
                user,
                transcript.organization,
                capability="view_transcript",
            )
        latest = self.get_available_intelligence(transcript, user=None)
        return self.aggregate_analyses(latest)

    def aggregate_analyses(
        self,
        analyses: dict[str, TranscriptAnalysis | None] | list[TranscriptAnalysis],
    ) -> dict[str, Any]:
        """
        Collapse analysis rows into the unified Speech Center intelligence contract.

        When given a list, keeps the newest row per ``analysis_type``.
        """
        by_type: dict[str, TranscriptAnalysis | None]
        if isinstance(analyses, dict):
            by_type = {
                AnalysisType.SUMMARY: analyses.get(AnalysisType.SUMMARY),
                AnalysisType.TOPICS: analyses.get(AnalysisType.TOPICS),
                AnalysisType.ACTION_ITEMS: analyses.get(AnalysisType.ACTION_ITEMS),
            }
        else:
            by_type = {
                AnalysisType.SUMMARY: None,
                AnalysisType.TOPICS: None,
                AnalysisType.ACTION_ITEMS: None,
            }
            for row in analyses:
                if row is None:
                    continue
                current = by_type.get(row.analysis_type)
                if current is None or row.created_at >= current.created_at:
                    by_type[row.analysis_type] = row

        summary = by_type.get(AnalysisType.SUMMARY)
        topics = by_type.get(AnalysisType.TOPICS)
        action_items = by_type.get(AnalysisType.ACTION_ITEMS)
        present = [row for row in (summary, topics, action_items) if row is not None]
        generated_at = max((row.created_at for row in present), default=None)

        return {
            "summary": summary.content if summary is not None else None,
            "topics": topics.content if topics is not None else None,
            "action_items": action_items.content if action_items is not None else None,
            "metadata": {
                "summary_id": str(summary.id) if summary is not None else None,
                "topics_id": str(topics.id) if topics is not None else None,
                "action_items_id": str(action_items.id) if action_items is not None else None,
                "providers": {
                    AnalysisType.SUMMARY: (summary.provider or "") if summary else "",
                    AnalysisType.TOPICS: (topics.provider or "") if topics else "",
                    AnalysisType.ACTION_ITEMS: (
                        (action_items.provider or "") if action_items else ""
                    ),
                },
                "model_names": {
                    AnalysisType.SUMMARY: (summary.model_name or "") if summary else "",
                    AnalysisType.TOPICS: (topics.model_name or "") if topics else "",
                    AnalysisType.ACTION_ITEMS: (
                        (action_items.model_name or "") if action_items else ""
                    ),
                },
                "available_types": [
                    key for key, row in by_type.items() if row is not None
                ],
            },
            "generated_at": generated_at,
        }

    def intelligence_summary(
        self,
        transcript: Transcript,
        *,
        user: AbstractBaseUser | None = None,
    ) -> dict[str, Any]:
        """
        Convenience wrapper for hosts: latest intelligence contract for a transcript.

        Equivalent to ``get_latest_intelligence`` (kept as an explicit Speech Center
        API surface name).
        """
        return self.get_latest_intelligence(transcript, user=user)

    def get_timeline(
        self,
        transcript: Transcript,
        *,
        user: AbstractBaseUser | None = None,
    ) -> dict[str, Any]:
        """
        Host timeline payload: segments, speakers, timestamps, analysis refs.
        """
        if user is not None:
            assert_organization_access(
                user,
                transcript.organization,
                capability="view_transcript",
            )

        transcript = (
            Transcript.objects.select_related("organization")
            .prefetch_related(
                "speakers",
                Prefetch(
                    "segments",
                    queryset=TranscriptSegment.objects.select_related("speaker").order_by(
                        "sequence", "start_ms"
                    ),
                ),
            )
            .get(pk=transcript.pk)
        )
        segments = list(transcript.segments.all())
        speakers = list(transcript.speakers.all())
        start_ms = min((s.start_ms for s in segments), default=None)
        end_ms = max((s.end_ms for s in segments), default=None)

        analyses = TranscriptAnalysisService().list_for_transcript(transcript)
        # Deduplicate to latest id per type for timeline references.
        latest_ids: dict[str, TranscriptAnalysis] = {}
        for row in analyses:
            if row.analysis_type not in latest_ids:
                latest_ids[row.analysis_type] = row

        return {
            "transcript_id": str(transcript.id),
            "status": transcript.status,
            "speakers": speakers,
            "segments": segments,
            "timestamps": {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "segment_count": len(segments),
            },
            "analysis_references": [
                {
                    "id": str(row.id),
                    "analysis_type": row.analysis_type,
                    "provider": row.provider or "",
                    "created_at": row.created_at,
                }
                for row in latest_ids.values()
            ],
        }

    def get_transcript_for_user(
        self,
        transcript_id: str,
        *,
        user: AbstractBaseUser,
    ) -> Transcript:
        """Org-scoped transcript retrieve for timeline / detail paths."""
        qs = Transcript.objects.select_related("organization", "media")
        from turing.auth.tenancy import scope_by_organization

        qs = scope_by_organization(qs, user)
        try:
            return qs.get(pk=transcript_id)
        except Transcript.DoesNotExist as exc:
            raise NotFoundError(f"Transcript '{transcript_id}' not found.") from exc

    def _primary_transcript_for_media(
        self, media: MediaAsset | None
    ) -> Transcript | None:
        if media is None:
            return None
        primary = (
            Transcript.objects.filter(media=media, is_primary=True)
            .order_by("-created_at")
            .first()
        )
        if primary is not None:
            return primary
        return Transcript.objects.filter(media=media).order_by("-created_at").first()

    def _context_for_media_only(
        self,
        media: MediaAsset,
        *,
        user: AbstractBaseUser | None = None,
    ) -> dict[str, Any]:
        if user is not None:
            assert_organization_access(
                user,
                media.organization,
                capability="view_transcript",
            )
        media = (
            MediaAsset.objects.select_related("organization")
            .prefetch_related("external_references")
            .get(pk=media.pk)
        )
        return {
            "media": media,
            "transcript": None,
            "status": None,
            "speakers": [],
            "analyses": {
                AnalysisType.SUMMARY: None,
                AnalysisType.TOPICS: None,
                AnalysisType.ACTION_ITEMS: None,
            },
            "external_references": list(media.external_references.all()),
        }

    def _collect_external_references(
        self,
        media: MediaAsset | None,
        transcript: Transcript | None,
    ) -> list[ExternalReference]:
        seen: set[str] = set()
        rows: list[ExternalReference] = []
        sources: list[ExternalReference] = []
        if transcript is not None:
            sources.extend(list(transcript.external_references.all()))
        if media is not None:
            sources.extend(list(media.external_references.all()))
        for ref in sources:
            key = str(ref.id)
            if key in seen:
                continue
            seen.add(key)
            rows.append(ref)
        return rows


def require_external_lookup_params(
    *,
    external_system: str | None,
    external_type: str | None,
    external_id: str | None,
) -> tuple[str, str, str]:
    """Validate Speech Center list query params."""
    system = (external_system or "").strip()
    type_ = (external_type or "").strip()
    eid = (external_id or "").strip()
    missing = [
        name
        for name, value in (
            ("external_system", system),
            ("external_type", type_),
            ("external_id", eid),
        )
        if not value
    ]
    if missing:
        raise ValidationError(
            "Query parameters required: " + ", ".join(missing) + "."
        )
    return system, type_, eid
