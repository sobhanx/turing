from __future__ import annotations

import logging
from typing import Iterable

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from turing.ai.registry import AIProviderRegistry
from turing.ai.types import TranscriptInput, TranscriptSegmentInput
from turing.auth.tenancy import assert_organization_access, scope_by_organization
from turing.conf import get_turing_settings
from turing.domain.enums import AnalysisType
from turing.domain.events import analysis_completed
from turing.domain.exceptions import ProviderError, ValidationError
from turing.events.bus import emit_after_commit
from turing.events.payloads import snapshot_external_references
from turing.models import Transcript, TranscriptAnalysis
from turing.services.transcript import TranscriptService

logger = logging.getLogger(__name__)

DEFAULT_ANALYSIS_TYPES: tuple[str, ...] = (
    AnalysisType.SUMMARY,
    AnalysisType.ACTION_ITEMS,
    AnalysisType.TOPICS,
)


class TranscriptAnalysisService:
    """Generate and persist derived AI analyses without mutating transcripts."""

    def __init__(self, transcript_service: TranscriptService | None = None) -> None:
        self.transcript_service = transcript_service or TranscriptService()

    def generate_default_suite(
        self,
        transcript: Transcript,
        *,
        user: AbstractBaseUser | None = None,
        provider_code: str | None = None,
    ) -> list[TranscriptAnalysis]:
        return self.generate(
            transcript,
            analysis_types=DEFAULT_ANALYSIS_TYPES,
            user=user,
            provider_code=provider_code,
        )

    @transaction.atomic
    def generate(
        self,
        transcript: Transcript,
        *,
        analysis_types: Iterable[str] = DEFAULT_ANALYSIS_TYPES,
        user: AbstractBaseUser | None = None,
        provider_code: str | None = None,
    ) -> list[TranscriptAnalysis]:
        if user is not None:
            assert_organization_access(
                user,
                transcript.organization,
                capability="view_transcript",
            )

        provider_code = provider_code or get_turing_settings().ai_provider
        provider = AIProviderRegistry.get(provider_code)
        transcript_input = self._build_input(transcript)
        created: list[TranscriptAnalysis] = []

        for analysis_type in analysis_types:
            try:
                result = provider.analyze(transcript_input, analysis_type)
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(
                    f"AI provider failed for {analysis_type}: {exc}",
                    code="PROVIDER_RESPONSE",
                    retryable=False,
                ) from exc

            content = self._validate_content(analysis_type, result.content)
            analysis = TranscriptAnalysis.objects.create(
                transcript=transcript,
                organization=transcript.organization,
                analysis_type=analysis_type,
                content=content,
                provider=provider_code,
                model_name=result.model_name or "",
            )
            created.append(analysis)

        if created:
            emit_after_commit(
                analysis_completed(
                    transcript_id=str(transcript.id),
                    organization_id=transcript.organization_id,
                    analysis_ids=[str(row.id) for row in created],
                    analysis_types=[row.analysis_type for row in created],
                    provider=provider_code,
                    external_references=snapshot_external_references(
                        organization_id=transcript.organization_id,
                        media_id=transcript.media_id,
                    )
                    + snapshot_external_references(
                        organization_id=transcript.organization_id,
                        transcript_id=transcript.id,
                    ),
                )
            )

        return created

    def list_for_transcript(
        self,
        transcript: Transcript,
        *,
        user: AbstractBaseUser | None = None,
        analysis_type: str | None = None,
    ):
        if user is not None:
            assert_organization_access(
                user,
                transcript.organization,
                capability="view_transcript",
            )
        qs = TranscriptAnalysis.objects.filter(transcript=transcript)
        if analysis_type:
            qs = qs.filter(analysis_type=analysis_type)
        return qs.order_by("-created_at")

    def latest_by_type(
        self,
        transcript: Transcript,
        *,
        analysis_type: str,
        user: AbstractBaseUser | None = None,
    ) -> TranscriptAnalysis:
        """
        Return the newest analysis row for ``analysis_type`` (append-only history).

        Raises ``NotFoundError`` when no row exists for that type.
        """
        from turing.domain.exceptions import NotFoundError

        if user is not None:
            assert_organization_access(
                user,
                transcript.organization,
                capability="view_transcript",
            )
        type_key = (analysis_type or "").strip()
        if not type_key:
            raise ValidationError("analysis type is required.")
        allowed = {choice.value for choice in AnalysisType}
        if type_key not in allowed:
            raise ValidationError(f"Unsupported analysis type: {type_key}")

        analysis = (
            TranscriptAnalysis.objects.filter(
                transcript=transcript,
                analysis_type=type_key,
            )
            .order_by("-created_at")
            .first()
        )
        if analysis is None:
            raise NotFoundError(
                f"No '{type_key}' analysis found for transcript '{transcript.id}'."
            )
        return analysis

    def get(
        self,
        analysis_id: str,
        *,
        user: AbstractBaseUser | None = None,
    ) -> TranscriptAnalysis:
        from turing.domain.exceptions import NotFoundError

        qs = TranscriptAnalysis.objects.select_related("transcript", "organization")
        if user is not None:
            qs = self.scope_queryset(qs, user)
        try:
            return qs.get(pk=analysis_id)
        except TranscriptAnalysis.DoesNotExist as exc:
            raise NotFoundError(f"TranscriptAnalysis '{analysis_id}' not found.") from exc

    def scope_queryset(self, queryset, user):
        return scope_by_organization(queryset, user, field="organization_id")

    def _build_input(self, transcript: Transcript) -> TranscriptInput:
        loaded = self.transcript_service.get(str(transcript.id))
        segments = tuple(
            TranscriptSegmentInput(
                sequence=segment.sequence,
                text=segment.text,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                speaker_label=segment.speaker.resolved_name if segment.speaker_id else "",
            )
            for segment in loaded.segments.select_related("speaker").order_by("sequence")
        )
        return TranscriptInput(
            transcript_id=str(loaded.id),
            language_code=loaded.language_code,
            full_text=loaded.full_text,
            segments=segments,
        )

    def _validate_content(self, analysis_type: str, content) -> dict | list:
        if analysis_type == AnalysisType.SUMMARY:
            if not isinstance(content, dict):
                raise ValidationError("Summary analysis must be a JSON object.")
            summary = content.get("summary")
            main_points = content.get("main_points")
            if not isinstance(summary, str):
                raise ValidationError("Summary analysis requires a string 'summary'.")
            if main_points is not None and not isinstance(main_points, list):
                raise ValidationError("Summary 'main_points' must be a list.")
            return {
                "summary": summary,
                "main_points": list(main_points or []),
            }

        if analysis_type == AnalysisType.ACTION_ITEMS:
            if not isinstance(content, list):
                raise ValidationError("Action items analysis must be a JSON array.")
            for item in content:
                if not isinstance(item, dict) or "task" not in item:
                    raise ValidationError("Each action item must be an object with 'task'.")
            return content

        if analysis_type == AnalysisType.TOPICS:
            if not isinstance(content, list):
                raise ValidationError("Topics analysis must be a JSON array.")
            return [str(topic) for topic in content]

        raise ValidationError(f"Unsupported analysis type: {analysis_type}")
