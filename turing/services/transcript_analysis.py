from __future__ import annotations

import logging
import time
from typing import Iterable

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from turing.ai.registry import AIProviderRegistry
from turing.ai.types import AnalysisResult, TranscriptInput, TranscriptSegmentInput
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
        requested = [str(item) for item in analysis_types]
        # Preserve order, drop duplicates.
        requested_unique = list(dict.fromkeys(requested))
        created: list[TranscriptAnalysis] = []
        total_started = time.perf_counter()
        llm_duration = 0.0

        use_suite = self._should_use_suite(requested_unique)
        try:
            if use_suite:
                llm_started = time.perf_counter()
                try:
                    suite = provider.analyze_suite(transcript_input)
                except ProviderError:
                    llm_duration = time.perf_counter() - llm_started
                    raise
                except Exception as exc:  # noqa: BLE001
                    llm_duration = time.perf_counter() - llm_started
                    raise ProviderError(
                        f"AI provider suite failed: {exc}",
                        code="PROVIDER_RESPONSE",
                        retryable=False,
                    ) from exc
                llm_duration = time.perf_counter() - llm_started
                results_by_type = {
                    str(key): value for key, value in (suite or {}).items()
                }
                for analysis_type in requested_unique:
                    result = results_by_type.get(analysis_type)
                    if result is None:
                        raise ProviderError(
                            f"AI suite response missing '{analysis_type}'.",
                            code="PROVIDER_RESPONSE",
                            retryable=False,
                        )
                    created.append(
                        self._persist_analysis(
                            transcript=transcript,
                            analysis_type=analysis_type,
                            result=result,
                            provider_code=provider_code,
                        )
                    )
            else:
                for analysis_type in requested_unique:
                    type_started = time.perf_counter()
                    try:
                        result = provider.analyze(transcript_input, analysis_type)
                    except ProviderError:
                        llm_duration += time.perf_counter() - type_started
                        raise
                    except Exception as exc:  # noqa: BLE001
                        llm_duration += time.perf_counter() - type_started
                        raise ProviderError(
                            f"AI provider failed for {analysis_type}: {exc}",
                            code="PROVIDER_RESPONSE",
                            retryable=False,
                        ) from exc
                    llm_duration += time.perf_counter() - type_started
                    created.append(
                        self._persist_analysis(
                            transcript=transcript,
                            analysis_type=analysis_type,
                            result=result,
                            provider_code=provider_code,
                        )
                    )
        except ProviderError:
            logger.warning(
                "[ANALYSIS-TIMING] transcript_id=%s llm_request_duration=%.3f "
                "total_analysis_duration=%.3f status=failed suite=%s",
                transcript.pk,
                llm_duration,
                time.perf_counter() - total_started,
                use_suite,
            )
            raise

        total_duration = time.perf_counter() - total_started
        logger.warning(
            "[ANALYSIS-TIMING] transcript_id=%s llm_request_duration=%.3f "
            "total_analysis_duration=%.3f status=ok suite=%s created=%s",
            transcript.pk,
            llm_duration,
            total_duration,
            use_suite,
            len(created),
        )

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

    def _should_use_suite(self, requested: list[str]) -> bool:
        """Use one provider suite call when generating multiple default types."""
        if len(requested) < 2:
            return False
        allowed = {choice.value for choice in AnalysisType}
        return all(item in allowed for item in requested)

    def _persist_analysis(
        self,
        *,
        transcript: Transcript,
        analysis_type: str,
        result: AnalysisResult,
        provider_code: str,
    ) -> TranscriptAnalysis:
        content = self._validate_content(analysis_type, result.content)
        return TranscriptAnalysis.objects.create(
            transcript=transcript,
            organization=transcript.organization,
            analysis_type=analysis_type,
            content=content,
            provider=provider_code,
            model_name=result.model_name or "",
        )

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
            from turing.ai.providers.openai import _limit_main_points, _limit_sentences

            return {
                "summary": _limit_sentences(summary),
                "main_points": _limit_main_points(list(main_points or [])),
            }

        if analysis_type == AnalysisType.ACTION_ITEMS:
            if not isinstance(content, list):
                raise ValidationError("Action items analysis must be a JSON array.")
            for item in content:
                if not isinstance(item, dict) or "task" not in item:
                    raise ValidationError("Each action item must be an object with 'task'.")
            from turing.ai.providers.openai import _limit_action_items

            return _limit_action_items(content)

        if analysis_type == AnalysisType.TOPICS:
            if not isinstance(content, list):
                raise ValidationError("Topics analysis must be a JSON array.")
            from turing.ai.providers.openai import _limit_topics

            return _limit_topics(content)

        raise ValidationError(f"Unsupported analysis type: {analysis_type}")
