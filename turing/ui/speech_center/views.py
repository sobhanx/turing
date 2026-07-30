"""Speech Center demo views — thin wrappers over existing services/models."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from turing.auth.roles import user_has_capability
from turing.auth.tenancy import (
    organization_ids_for,
    scope_by_organization,
    user_is_global_bypass,
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
from turing.services.transcript import TranscriptService
from turing.ui.speech_center.auth import require_turing_capability
from turing.ui.speech_center.presentation import (
    can_show_retry,
    format_duration_ms,
    job_display_status,
    job_pipeline_steps,
    job_progress_pct,
)
from turing.ui.speech_center.recorder import recorder_client_config


def _analysis_text(row) -> str:
    if row is None:
        return ""
    content = getattr(row, "content", None)
    if isinstance(content, dict):
        for key in ("summary", "text", "topics", "action_items", "items"):
            val = content.get(key)
            if isinstance(val, list):
                return "\n".join(str(x) for x in val if x)
            if val:
                return str(val)
        return ""
    return str(content) if content else ""


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
    """Product landing page: welcome, quick actions, recent activity."""
    recent_media = list(_scoped_media(request.user)[:6])
    recent_jobs = []
    for job in _scoped_jobs(request.user)[:6]:
        label, css = job_display_status(job)
        recent_jobs.append(
            {
                "job": job,
                "status_label": label,
                "status_css": css,
                "progress_pct": job_progress_pct(job),
                "pipeline": job_pipeline_steps(job),
                "media_name": (
                    (job.media.original_filename if job.media else "")
                    or (job.media.object_key if job.media else "")
                    or str(job.media_id)
                ),
            }
        )
    recent_transcripts = []
    for transcript in _scoped_transcripts(request.user)[:6]:
        media = transcript.media
        recent_transcripts.append(
            {
                "transcript": transcript,
                "media_name": (
                    (media.original_filename if media else "")
                    or (media.object_key if media else "")
                    or str(transcript.media_id)
                ),
            }
        )
    user = request.user
    display_name = (
        (getattr(user, "get_full_name", lambda: "")() or "").strip()
        or getattr(user, "get_username", lambda: "")()
        or "there"
    )
    return render(
        request,
        "speech_center/dashboard.html",
        {
            "page_title": "Speech Center",
            "nav_active": "dashboard",
            "welcome_name": display_name,
            "upload_url": reverse("speech_center:upload_media"),
            "record_url": reverse("speech_center:upload_media") + "?tab=record",
            "create_url": reverse("speech_center:create_transcript"),
            "queue_url": reverse("speech_center:queue"),
            "transcripts_url": reverse("speech_center:transcripts"),
            "meetings_url": reverse("speech_center:meetings"),
            "recent_media": recent_media,
            "recent_jobs": recent_jobs,
            "recent_transcripts": recent_transcripts,
        },
    )


@staff_member_required
@require_turing_capability("view_transcript")
@require_GET
def meetings(request):
    """
    Meetings foundation UI only — no provider integration.

    Placeholder cards for future Alocom / Zoom / Teams wiring.
    """
    return render(
        request,
        "speech_center/meetings.html",
        {
            "page_title": "Meetings",
            "nav_active": "meetings",
            "providers": [
                {"code": "alocom", "name": "Alocom", "status": "Coming soon"},
                {"code": "zoom", "name": "Zoom", "status": "Coming soon"},
                {"code": "teams", "name": "Teams", "status": "Coming soon"},
            ],
            "sample_statuses": [
                {"label": "Scheduled", "css": "queued"},
                {"label": "Processing", "css": "processing"},
                {"label": "Completed", "css": "completed"},
            ],
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
    selected_media = None
    if selected_media_id:
        # Surface the just-uploaded asset at the top of the list.
        selected_rows = [m for m in media_list if str(m.id) == selected_media_id]
        other_rows = [m for m in media_list if str(m.id) != selected_media_id]
        media_list = selected_rows + other_rows
        selected_media = selected_rows[0] if selected_rows else None

    return render(
        request,
        "speech_center/create_transcript.html",
        {
            "page_title": "Create Transcript",
            "nav_active": "create",
            "media_list": media_list,
            "selected_media_id": selected_media_id,
            "selected_media": selected_media,
            "selected_duration": format_duration_ms(
                getattr(selected_media, "duration_ms", None) if selected_media else None
            ),
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
                "pipeline": job_pipeline_steps(job),
                "progress_pct": job_progress_pct(job),
            }
        )
    return render(
        request,
        "speech_center/queue.html",
        {
            "page_title": "Processing Queue",
            "nav_active": "queue",
            "jobs": jobs,
            "poll_seconds": 8,
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
    Transcript viewer using SpeechCenterService context.

    Layout is presentation-only; segment/speaker data come from existing models.
    """
    transcript = get_object_or_404(_scoped_transcripts(request.user), pk=transcript_id)
    context_payload = SpeechCenterService().get_transcript_context(
        transcript,
        user=request.user,
    )
    analyses = context_payload.get("analyses") or {}
    summary_text = _analysis_text(analyses.get(AnalysisType.SUMMARY)) or "—"
    topics_text = _analysis_text(analyses.get(AnalysisType.TOPICS)) or "—"
    actions_text = _analysis_text(analyses.get(AnalysisType.ACTION_ITEMS)) or "—"

    tid = str(transcript.pk)
    media = context_payload["media"]
    # Prefetched on transcript in get_transcript_context — read-only UI use.
    segments = []
    for seg in context_payload["transcript"].segments.all():
        speaker = seg.speaker
        segments.append(
            {
                "start_display": format_duration_ms(seg.start_ms),
                "text": seg.text,
                "speaker_id": str(speaker.id) if speaker is not None else "",
                "speaker_label": (
                    speaker.speaker_label if speaker is not None else "—"
                ),
                "speaker_name": (
                    speaker.speaker_name if speaker is not None else ""
                ),
                "display_name": (
                    speaker.resolved_name if speaker is not None else "—"
                ),
            }
        )
    can_edit_speakers = user_is_global_bypass(request.user) or user_has_capability(
        request.user,
        "edit_transcript",
        organization=transcript.organization,
    )
    can_edit_transcript = can_edit_speakers
    speaker_api_base = reverse("turing-speakers-detail", args=["00000000-0000-0000-0000-000000000000"])
    speaker_api_base = speaker_api_base.rsplit("/", 2)[0] + "/"
    transcript_edit_url = reverse(
        "turing-transcripts-edit-body",
        args=[transcript.pk],
    )
    transcript_edit_body = TranscriptService().format_editor_body(transcript)
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
            "segments": segments,
            "summary_text": summary_text,
            "topics_text": topics_text,
            "actions_text": actions_text,
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
            "can_edit_speakers": can_edit_speakers,
            "can_edit_transcript": can_edit_transcript,
            "transcript_edit_body": transcript_edit_body,
            "speaker_edit_config_json": {
                "speakersApiBase": speaker_api_base,
                "canEdit": can_edit_speakers,
                "csrfToken": get_token(request),
            },
            "transcript_edit_config_json": {
                "editBodyUrl": transcript_edit_url,
                "canEdit": can_edit_transcript,
                "csrfToken": get_token(request),
            },
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
