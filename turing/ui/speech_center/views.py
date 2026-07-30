"""Speech Center demo views — thin wrappers over existing services/models."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from turing.auth.tenancy import (
    organization_ids_for,
    scope_by_organization,
    user_sees_all_organizations,
)
from turing.domain.enums import AnalysisType, UseCase
from turing.domain.exceptions import (
    JobStateError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from turing.models import MediaAsset, Organization, ProcessingJob, Transcript
from turing.services.export import ExportService
from turing.services.export.service import ensure_supported_format
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.speech_center import SpeechCenterService
from turing.ui.speech_center.auth import require_turing_capability
from turing.ui.speech_center.presentation import (
    can_show_retry,
    format_duration_ms,
    job_display_status,
)
from turing.ui.speech_center.recorder import recorder_client_config


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


def _organizations_for_upload(user):
    qs = Organization.objects.filter(is_active=True).order_by("name")
    if user_sees_all_organizations(user):
        return qs
    org_ids = organization_ids_for(user)
    if not org_ids:
        return Organization.objects.none()
    return qs.filter(pk__in=org_ids)


@staff_member_required
@require_turing_capability("view_transcript")
@require_GET
def dashboard(request):
    """Minimal four-card entry page."""
    return render(
        request,
        "speech_center/dashboard.html",
        {
            "page_title": "Speech Center",
            "nav_active": "dashboard",
            "upload_url": reverse("speech_center:upload_media"),
            "create_url": reverse("speech_center:create_transcript"),
            "queue_url": reverse("speech_center:queue"),
            "transcripts_url": reverse("speech_center:transcripts"),
        },
    )


@staff_member_required
@require_turing_capability("upload_media")
@require_http_methods(["GET", "POST"])
def upload_media(request):
    """
    Speech Center media upload — thin wrapper over MediaService.create_from_upload.

    UI collects only file + organization; filename / mime / size come from the file.
    """
    organizations = _organizations_for_upload(request.user)
    default_org = organizations.first()

    if request.method == "POST":
        uploaded = request.FILES.get("file")
        organization_id = (request.POST.get("organization_id") or "").strip()
        if not uploaded:
            messages.error(request, "Please choose an audio file to upload.")
            return redirect("speech_center:upload_media")
        if not organization_id:
            messages.error(request, "Please select an organization.")
            return redirect("speech_center:upload_media")

        filename = (getattr(uploaded, "name", None) or "").rsplit("/", 1)[-1].strip()
        content_type = getattr(uploaded, "content_type", "") or ""
        try:
            media = MediaService().create_from_upload(
                uploaded_file=uploaded,
                filename=filename,
                content_type=content_type,
                use_case=UseCase.GENERIC,
                organization_id=organization_id,
                uploaded_by=request.user,
            )
        except (PermissionDeniedError, ValidationError, NotFoundError) as exc:
            messages.error(request, str(exc))
            return redirect("speech_center:upload_media")

        display_name = media.original_filename or str(media.id)
        is_recorder = (
            (request.POST.get("upload_source") or "").strip().lower() == "recorder"
        )
        if is_recorder:
            messages.success(
                request,
                f"Recording uploaded successfully: {display_name}.",
            )
        else:
            messages.success(
                request,
                f"Uploaded {display_name}.",
            )
        # Preselect on Create Transcript so the new asset is obvious.
        create_url = reverse("speech_center:create_transcript")
        return redirect(f"{create_url}?selected={media.id}")

    recorder_cfg = recorder_client_config()
    recorder_cfg.update(
        {
            "uploadUrl": reverse("speech_center:upload_media"),
            "redirectUrl": reverse("speech_center:create_transcript"),
            "csrfToken": get_token(request),
        }
    )

    return render(
        request,
        "speech_center/upload.html",
        {
            "page_title": "Upload Content",
            "nav_active": "upload",
            "organizations": organizations,
            "default_organization_id": str(default_org.pk) if default_org else "",
            "recorder_config_json": recorder_cfg,
        },
    )


@staff_member_required
@require_turing_capability("manage_jobs")
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
    selected_media_id = (request.GET.get("selected") or "").strip()
    media_list = list(media_qs[:100])
    if selected_media_id:
        # Surface the just-uploaded asset at the top of the list.
        selected_rows = [m for m in media_list if str(m.id) == selected_media_id]
        other_rows = [m for m in media_list if str(m.id) != selected_media_id]
        media_list = selected_rows + other_rows

    return render(
        request,
        "speech_center/create_transcript.html",
        {
            "page_title": "Create Transcript",
            "nav_active": "create",
            "media_list": media_list,
            "selected_media_id": selected_media_id,
            "default_language": settings.default_language or "",
        },
    )


@staff_member_required
@require_turing_capability("view_transcript")
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
@require_turing_capability("manage_jobs")
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
@require_turing_capability("view_transcript")
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
@require_turing_capability("view_transcript")
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
            "export_pdf_url": reverse(
                "speech_center:export_transcript",
                args=[transcript.pk, "pdf"],
            ),
            "export_docx_url": reverse(
                "speech_center:export_transcript",
                args=[transcript.pk, "docx"],
            ),
        },
    )


@staff_member_required
@require_turing_capability("view_transcript")
@require_GET
def export_transcript(request, transcript_id, fmt: str):
    """Stream an on-demand transcript export (PDF/DOCX/…)."""
    from django.http import StreamingHttpResponse

    transcript = get_object_or_404(_scoped_transcripts(request.user), pk=transcript_id)
    try:
        format_code = ensure_supported_format(fmt)
        result = ExportService().export_transcript(
            transcript,
            format_code,
            user=request.user,
        )
    except (PermissionDeniedError, ValidationError, NotFoundError) as exc:
        messages.error(request, str(exc))
        return redirect("speech_center:transcript_detail", transcript_id)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("speech_center:transcript_detail", transcript_id)

    response = StreamingHttpResponse(
        streaming_content=result.chunks,
        content_type=result.content_type,
    )
    response["Content-Disposition"] = f'attachment; filename="{result.filename}"'
    response["Cache-Control"] = "no-store"
    return response
