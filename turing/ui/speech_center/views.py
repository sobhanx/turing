"""Speech Center demo views — thin wrappers over existing services/models."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from turing.auth.tenancy import scope_by_organization
from turing.domain.enums import AnalysisType
from turing.domain.exceptions import JobStateError, PermissionDeniedError, ValidationError
from turing.models import MediaAsset, ProcessingJob, Transcript
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.speech_center import SpeechCenterService
from turing.ui.speech_center.presentation import (
    can_show_retry,
    format_duration_ms,
    job_display_status,
)


def _scoped_jobs(user):
    return scope_by_organization(
        ProcessingJob.objects.select_related("media", "organization").order_by("-created_at"),
        user,
    )


def _scoped_media(user):
    return scope_by_organization(
        MediaAsset.objects.select_related("organization").order_by("-created_at"),
        user,
    )


def _scoped_transcripts(user):
    return scope_by_organization(
        Transcript.objects.select_related("media", "organization", "job").order_by(
            "-created_at"
        ),
        user,
    )


@staff_member_required
@require_GET
def dashboard(request):
    """Minimal four-card entry page."""
    return render(
        request,
        "speech_center/dashboard.html",
        {
            "page_title": "Speech Center",
            "nav_active": "dashboard",
            "upload_url": reverse("admin:turing_mediaasset_add"),
            "create_url": reverse("speech_center:create_transcript"),
            "queue_url": reverse("speech_center:queue"),
            "transcripts_url": reverse("speech_center:transcripts"),
        },
    )


@staff_member_required
@require_http_methods(["GET", "POST"])
def create_transcript(request):
    """
    Send media to transcription via existing JobOrchestrator.

    Does not reimplement upload or STT — only creates/enqueues jobs.
    """
    media_qs = _scoped_media(request.user)
    if request.method == "POST":
        media_id = request.POST.get("media_id")
        language_code = (request.POST.get("language_code") or "").strip()
        media = get_object_or_404(media_qs, pk=media_id)
        try:
            job = JobOrchestrator().create_transcription_job(
                media=media,
                created_by=request.user,
                language_code=language_code,
            )
        except (PermissionDeniedError, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("speech_center:create_transcript")
        messages.success(
            request,
            f"Transcription job created ({job.id}).",
        )
        return redirect("speech_center:queue")

    from turing.conf import get_turing_settings

    settings = get_turing_settings()
    return render(
        request,
        "speech_center/create_transcript.html",
        {
            "page_title": "Create Transcript",
            "nav_active": "create",
            "media_list": media_qs[:100],
            "default_language": settings.default_language or "",
        },
    )


@staff_member_required
@require_GET
def queue(request):
    """Processing queue from existing ProcessingJob rows."""
    jobs = []
    for job in _scoped_jobs(request.user)[:100]:
        label, css = job_display_status(job)
        jobs.append(
            {
                "job": job,
                "status_label": label,
                "status_css": css,
                "show_retry": can_show_retry(job),
            }
        )
    return render(
        request,
        "speech_center/queue.html",
        {
            "page_title": "Processing Queue",
            "nav_active": "queue",
            "jobs": jobs,
        },
    )


@staff_member_required
@require_POST
def retry_job(request, job_id):
    """Retry via existing JobOrchestrator.retry."""
    job = get_object_or_404(_scoped_jobs(request.user), pk=job_id)
    try:
        JobOrchestrator().retry(job)
        messages.success(request, f"Retry scheduled for job {job.id}.")
    except (JobStateError, PermissionDeniedError, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect("speech_center:queue")


@staff_member_required
@require_GET
def transcripts(request):
    """Transcript list from existing Transcript model."""
    rows = []
    for transcript in _scoped_transcripts(request.user)[:100]:
        media = transcript.media
        rows.append(
            {
                "transcript": transcript,
                "media_name": (
                    (media.original_filename if media else "")
                    or (media.object_key if media else "")
                    or str(transcript.media_id)
                ),
                "duration": format_duration_ms(
                    media.duration_ms if media else None
                ),
            }
        )
    return render(
        request,
        "speech_center/transcripts.html",
        {
            "page_title": "Transcripts",
            "nav_active": "transcripts",
            "rows": rows,
        },
    )


@staff_member_required
@require_GET
def transcript_detail(request, transcript_id):
    """
    Simple transcript page using SpeechCenterService context.

    Segment / word / analysis buttons link to existing Admin browsers.
    """
    transcript = get_object_or_404(_scoped_transcripts(request.user), pk=transcript_id)
    context_payload = SpeechCenterService().get_transcript_context(
        transcript,
        user=request.user,
    )
    analyses = context_payload.get("analyses") or {}
    summary_row = analyses.get(AnalysisType.SUMMARY)
    summary_text = ""
    if summary_row is not None:
        content = getattr(summary_row, "content", None)
        if isinstance(content, dict):
            summary_text = str(content.get("summary") or "")
        elif content:
            summary_text = str(content)

    tid = str(transcript.pk)
    media = context_payload["media"]
    analysis_url = (
        reverse("admin:turing_transcriptanalysis_changelist")
        + f"?transcript__id__exact={tid}"
    )
    return render(
        request,
        "speech_center/transcript_detail.html",
        {
            "page_title": "Transcript",
            "nav_active": "transcripts",
            "transcript": context_payload["transcript"],
            "media": media,
            "speakers": context_payload["speakers"],
            "summary_text": summary_text or "—",
            "duration_display": format_duration_ms(
                getattr(media, "duration_ms", None) if media else None
            ),
            "segments_url": (
                reverse("admin:turing_transcriptsegment_changelist")
                + f"?transcript__id__exact={tid}"
            ),
            "words_url": (
                reverse("admin:turing_transcriptword_changelist")
                + f"?segment__transcript__id__exact={tid}"
            ),
            "analysis_url": analysis_url,
            "intelligence_url": analysis_url,
        },
    )
