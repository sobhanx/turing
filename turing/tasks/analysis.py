from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="turing.tasks.analysis.generate_transcript_analysis",
    acks_late=True,
    max_retries=5,
)
def generate_transcript_analysis(self, transcript_id: str) -> str:
    """Generate default AI analyses for a completed transcript."""
    from turing.domain.exceptions import NotFoundError, ProviderError, TuringError
    from turing.models import Transcript
    from turing.services.ai_analysis_trigger import clear_state, mark_failed, mark_generating
    from turing.services.transcript_analysis import TranscriptAnalysisService

    mark_generating(transcript_id)

    try:
        transcript = Transcript.objects.select_related("organization").get(pk=transcript_id)
    except Transcript.DoesNotExist as exc:
        clear_state(transcript_id)
        raise NotFoundError(f"Transcript '{transcript_id}' not found.") from exc

    service = TranscriptAnalysisService()
    try:
        analyses = service.generate_default_suite(transcript)
    except ProviderError as exc:
        logger.exception("AI analysis provider error for transcript %s", transcript_id)
        if getattr(exc, "retryable", False) and self.request.retries < self.max_retries:
            countdown = min(300, 2 ** int(self.request.retries))
            raise self.retry(exc=exc, countdown=countdown)
        mark_failed(transcript_id)
        raise
    except TuringError:
        logger.exception("AI analysis aborted for transcript %s", transcript_id)
        mark_failed(transcript_id)
        raise
    except Exception as exc:
        logger.exception("AI analysis unexpected error for transcript %s", transcript_id)
        if self.request.retries < self.max_retries:
            countdown = min(300, 2 ** int(self.request.retries))
            raise self.retry(exc=exc, countdown=countdown)
        mark_failed(transcript_id)
        raise

    clear_state(transcript_id)
    return f"created:{len(analyses)}"
