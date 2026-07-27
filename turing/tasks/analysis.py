from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="turing.tasks.analysis.generate_transcript_analysis",
    acks_late=True,
    max_retries=0,
)
def generate_transcript_analysis(self, transcript_id: str) -> str:
    """Generate default AI analyses for a completed transcript."""
    from turing.domain.exceptions import NotFoundError, ProviderError, TuringError
    from turing.models import Transcript
    from turing.services.transcript_analysis import TranscriptAnalysisService

    try:
        transcript = Transcript.objects.select_related("organization").get(pk=transcript_id)
    except Transcript.DoesNotExist as exc:
        raise NotFoundError(f"Transcript '{transcript_id}' not found.") from exc

    service = TranscriptAnalysisService()
    try:
        analyses = service.generate_default_suite(transcript)
    except ProviderError:
        logger.exception("AI analysis provider error for transcript %s", transcript_id)
        raise
    except TuringError:
        logger.exception("AI analysis aborted for transcript %s", transcript_id)
        raise
    except Exception:
        logger.exception("AI analysis unexpected error for transcript %s", transcript_id)
        raise

    return f"created:{len(analyses)}"
