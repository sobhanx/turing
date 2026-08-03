"""Speech Center demo views — thin wrappers over existing services/models."""

from __future__ import annotations

from django.contrib import messages
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
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
    can_show_cancel,
    can_show_retry,
    cancelled_by_user_label,
    format_duration_ms,
    job_display_status,
    job_elapsed_seconds,
    job_pipeline_steps,
    job_progress_pct,
    job_timing_line,
    job_timing_start,
)
from turing.ui.speech_center.recorder import recorder_client_config

ANALYSIS_PENDING_LABEL = gettext_lazy("Preparing analysis…")
ANALYSIS_GENERATING_LABEL = gettext_lazy("Generating AI insights…")
ANALYSIS_FAILED_LABEL = gettext_lazy("AI analysis failed.")
ANALYSIS_IDLE_TITLE = gettext_lazy("AI insights (optional)")
ANALYSIS_IDLE_BODY = gettext_lazy(
    "Your transcript is ready. Generate summary, keywords, and action items when you need them."
)
ANALYSIS_STATE_IDLE = gettext_lazy("Not generated yet")
ANALYSIS_STATE_GENERATING = gettext_lazy("Generating")
ANALYSIS_STATE_READY = gettext_lazy("Completed")
ANALYSIS_STATE_FAILED = gettext_lazy("Failed")
ANALYSIS_EMPTY_LABEL = "—"
ANALYSIS_POLL_SECONDS = 5


def _format_action_item(item) -> str:
    if isinstance(item, dict):
        task = str(item.get("task") or "").strip()
        if not task:
            return ""
        owner = str(item.get("owner") or "").strip()
        deadline = str(item.get("deadline") or "").strip()
        suffix_parts = [p for p in (owner, deadline) if p]
        if suffix_parts:
            return f"{task} ({', '.join(suffix_parts)})"
        return task
    return str(item).strip() if item else ""


def _normalize_analysis_contents(
    analyses: dict,
) -> dict[str, object | None]:
    """
    Collapse latest-per-type TranscriptAnalysis rows into content payloads.

    Output shape::

        {
            "summary": {"summary": "...", "main_points": [...]} | None,
            "topics": [...] | None,
            "action_items": [...] | None,
        }
    """
    normalized: dict[str, object | None] = {
        AnalysisType.SUMMARY.value: None,
        AnalysisType.TOPICS.value: None,
        AnalysisType.ACTION_ITEMS.value: None,
    }
    if not analyses:
        return normalized
    for key in list(normalized.keys()):
        row = analyses.get(key)
        if row is None:
            # Tolerate enum-keyed dicts from older callers.
            enum_key = getattr(AnalysisType, key.upper(), None)
            if enum_key is not None:
                row = analyses.get(enum_key)
        if row is None:
            continue
        if hasattr(row, "content"):
            normalized[key] = row.content
        else:
            normalized[key] = row
    return normalized


def _summary_display_text(content) -> str:
    """UI text from summary content: analyses['summary']['summary']."""
    if isinstance(content, str):
        # Tolerate accidentally double-encoded JSON payloads.
        raw = content.strip()
        if raw.startswith("{"):
            try:
                import json

                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return raw
            if isinstance(parsed, dict):
                content = parsed
            else:
                return raw
        else:
            return raw
    if not isinstance(content, dict):
        return ""
    return str(content.get("summary") or "").strip()


def _topics_display_text(content) -> str:
    """UI text from topics content list (empty list → blank)."""
    if not isinstance(content, list):
        return ""
    return "\n".join(str(item).strip() for item in content if str(item).strip())


def _actions_display_text(content) -> str:
    """UI text from action_items content list."""
    if not isinstance(content, list):
        return ""
    lines = [_format_action_item(item) for item in content]
    return "\n".join(line for line in lines if line)


def _analysis_text(row) -> str:
    """Map a TranscriptAnalysis row's content JSON to display text for the demo UI."""
    if row is None:
        return ""
    content = getattr(row, "content", None)
    if isinstance(content, dict) and "summary" in content:
        return _summary_display_text(content)
    if isinstance(content, list):
        if content and isinstance(content[0], dict):
            return _actions_display_text(content)
        return _topics_display_text(content)
    if isinstance(content, dict):
        return _summary_display_text(content)
    return str(content) if content else ""


def _scoped_jobs(user):
    return scope_by_organization(
        ProcessingJob.objects.select_related(
            "media", "organization", "transcript"
        ).order_by("-created_at"),
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
            "page_title": _("Speech Center"),
            "nav_active": "dashboard",
            "welcome_name": display_name,
            "upload_url": reverse("speech_center:upload_media"),
            "record_url": reverse("speech_center:upload_media") + "?tab=record",
            "create_url": reverse("speech_center:create_transcript"),
            "queue_url": reverse("speech_center:queue"),
            "transcripts_url": reverse("speech_center:transcripts"),
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
    Meetings foundation UI — hidden from normal product navigation.

    Kept for future meeting-link / live-transcript integrations; not part of
    the current upload → transcription → optional AI analysis scope.
    """
    from django.http import Http404

    raise Http404("Meetings is not available in the current Speech Center.")


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
            messages.error(request, _("Please choose an audio file to upload."))
            return redirect("speech_center:upload_media")
        if not organization_id:
            messages.error(request, _("Please select an organization."))
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
                _("Recording uploaded successfully: %(name)s.") % {"name": display_name},
            )
        else:
            messages.success(
                request,
                _("Uploaded %(name)s.") % {"name": display_name},
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
            "page_title": _("Upload Content"),
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
            _("Transcription job created (%(job_id)s).") % {"job_id": job.id},
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
            "page_title": _("Create Transcript"),
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
        start = job_timing_start(job)
        elapsed = job_elapsed_seconds(job)
        terminal = css in {"completed", "failed", "cancelled"}
        transcript_url = ""
        if css == "completed":
            transcript = getattr(job, "transcript", None)
            if transcript is not None:
                transcript_url = reverse(
                    "speech_center:transcript_detail",
                    args=[transcript.pk],
                )
        jobs.append(
            {
                "job": job,
                "status_label": label,
                "status_css": css,
                "show_retry": can_show_retry(job),
                "show_cancel": can_show_cancel(job),
                "pipeline": job_pipeline_steps(job),
                "progress_pct": job_progress_pct(job),
                "timing_line": job_timing_line(job, label),
                "timing_since_iso": start.isoformat() if start and not terminal else "",
                "timing_elapsed_seconds": elapsed if elapsed is not None else "",
                "timing_is_terminal": terminal,
                "cancel_message": cancelled_by_user_label(job) if css == "cancelled" else "",
                "transcript_url": transcript_url,
                "media_duration": (
                    format_duration_ms(job.media.duration_ms)
                    if job.media_id and job.media.duration_ms is not None
                    else ""
                ),
            }
        )
    return render(
        request,
        "speech_center/queue.html",
        {
            "page_title": _("Processing Queue"),
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
        messages.success(request, _("Retry scheduled for job %(job_id)s.") % {"job_id": job.id})
    except (JobStateError, PermissionDeniedError, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect("speech_center:queue")


@staff_member_required
@require_turing_capability("manage_jobs")
@require_POST
def cancel_job(request, job_id):
    """Cancel via existing JobOrchestrator.cancel (keeps history, no delete)."""
    job = get_object_or_404(_scoped_jobs(request.user), pk=job_id)
    try:
        JobOrchestrator().cancel(job)
        messages.success(
            request,
            _("Job %(job_id)s cancelled.") % {"job_id": job.id},
        )
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
            "page_title": _("Transcripts"),
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
    tid = str(transcript.pk)
    context_payload = SpeechCenterService().get_transcript_context(
        transcript,
        user=request.user,
    )
    available = context_payload.get("analyses") or {}
    analyses = _normalize_analysis_contents(available)

    from turing.services.ai_analysis_trigger import resolve_ui_state

    analysis_ui_state = resolve_ui_state(available, tid)

    summary_row = available.get(AnalysisType.SUMMARY.value)
    topics_row = available.get(AnalysisType.TOPICS.value)
    actions_row = available.get(AnalysisType.ACTION_ITEMS.value)
    # Enum-keyed dicts from older callers.
    if summary_row is None:
        summary_row = available.get(AnalysisType.SUMMARY)
    if topics_row is None:
        topics_row = available.get(AnalysisType.TOPICS)
    if actions_row is None:
        actions_row = available.get(AnalysisType.ACTION_ITEMS)

    summary_pending = summary_row is None
    topics_pending = topics_row is None
    actions_pending = actions_row is None
    analysis_ready = analysis_ui_state == "ready"
    analysis_generating = analysis_ui_state == "generating"
    analysis_failed = analysis_ui_state == "failed"
    analysis_idle = analysis_ui_state == "idle"
    # Legacy flag: poll while generating (auto or manual).
    analysis_pending = analysis_generating

    # Pending (no row yet) vs completed-but-empty (row exists, empty content).
    summary_text = (
        ANALYSIS_PENDING_LABEL
        if summary_pending and analysis_generating
        else (
            ANALYSIS_EMPTY_LABEL
            if summary_pending
            else (_summary_display_text(analyses.get(AnalysisType.SUMMARY.value)) or ANALYSIS_EMPTY_LABEL)
        )
    )
    topics_text = (
        ANALYSIS_PENDING_LABEL
        if topics_pending and analysis_generating
        else (
            ANALYSIS_EMPTY_LABEL
            if topics_pending
            else (_topics_display_text(analyses.get(AnalysisType.TOPICS.value)) or ANALYSIS_EMPTY_LABEL)
        )
    )
    actions_text = (
        ANALYSIS_PENDING_LABEL
        if actions_pending and analysis_generating
        else (
            ANALYSIS_EMPTY_LABEL
            if actions_pending
            else (_actions_display_text(analyses.get(AnalysisType.ACTION_ITEMS.value)) or ANALYSIS_EMPTY_LABEL)
        )
    )

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
    can_generate_analysis = user_is_global_bypass(request.user) or user_has_capability(
        request.user,
        "view_transcript",
        organization=transcript.organization,
    )
    speaker_api_base = reverse("turing-speakers-detail", args=["00000000-0000-0000-0000-000000000000"])
    speaker_api_base = speaker_api_base.rsplit("/", 2)[0] + "/"
    transcript_edit_url = reverse(
        "turing-transcripts-edit-body",
        args=[transcript.pk],
    )
    transcript_edit_body = TranscriptService().format_editor_body(transcript)
    generate_analysis_url = reverse(
        "speech_center:generate_ai_insights",
        args=[transcript.pk],
    )
    return render(
        request,
        "speech_center/transcript_detail.html",
        {
            "page_title": _("Transcript"),
            "nav_active": "transcripts",
            "transcript": context_payload["transcript"],
            "media": media,
            "speakers": context_payload["speakers"],
            "segments": segments,
            "summary_text": summary_text,
            "topics_text": topics_text,
            "actions_text": actions_text,
            "summary_pending": summary_pending and analysis_generating,
            "topics_pending": topics_pending and analysis_generating,
            "actions_pending": actions_pending and analysis_generating,
            "analysis_pending": analysis_pending,
            "analysis_ui_state": analysis_ui_state,
            "analysis_ready": analysis_ready,
            "analysis_generating": analysis_generating,
            "analysis_failed": analysis_failed,
            "analysis_idle": analysis_idle,
            "analysis_generating_label": ANALYSIS_GENERATING_LABEL,
            "analysis_failed_label": ANALYSIS_FAILED_LABEL,
            "analysis_idle_title": ANALYSIS_IDLE_TITLE,
            "analysis_idle_body": ANALYSIS_IDLE_BODY,
            "analysis_state_label": (
                ANALYSIS_STATE_GENERATING
                if analysis_generating
                else ANALYSIS_STATE_FAILED
                if analysis_failed
                else ANALYSIS_STATE_READY
                if analysis_ready
                else ANALYSIS_STATE_IDLE
            ),
            "can_generate_analysis": can_generate_analysis,
            "generate_analysis_url": generate_analysis_url,
            "analysis_poll_seconds": ANALYSIS_POLL_SECONDS if analysis_pending else 0,
            "duration_display": format_duration_ms(
                getattr(media, "duration_ms", None) if media else None
            ),
            "segments_url": reverse(
                "speech_center:transcript_segments",
                args=[transcript.pk],
            ),
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
def transcript_segments(request, transcript_id):
    """
    Product-facing chronological segment list.

    Read-only presentation of existing TranscriptSegment rows — no admin UI.
    """
    from turing.models import TranscriptSegment

    transcript = get_object_or_404(_scoped_transcripts(request.user), pk=transcript_id)
    media = transcript.media
    qs = (
        TranscriptSegment.objects.filter(transcript=transcript)
        .select_related("speaker")
        .order_by("sequence", "start_ms")
    )
    rows = []
    sync_enabled = False
    for seg in qs:
        speaker = seg.speaker
        start_ms = int(seg.start_ms or 0)
        end_ms = int(seg.end_ms or 0)
        if end_ms < start_ms:
            end_ms = start_ms
        duration_ms = max(0, end_ms - start_ms)
        text = seg.text or ""
        has_timing = end_ms > start_ms or start_ms > 0
        if has_timing:
            sync_enabled = True
        rows.append(
            {
                "id": str(seg.id),
                "sequence": seg.sequence,
                "speaker_name": (
                    speaker.resolved_name
                    if speaker is not None
                    else "—"
                ),
                "speaker_label": (
                    speaker.speaker_label if speaker is not None else ""
                ),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "start_sec": start_ms / 1000.0,
                "end_sec": end_ms / 1000.0,
                "start_display": format_duration_ms(start_ms),
                "end_display": format_duration_ms(end_ms),
                "duration_display": format_duration_ms(duration_ms),
                "text": text,
                "is_long": len(text) > 280,
                "has_timing": has_timing,
            }
        )

    media_url = ""
    if media is not None and getattr(media, "file", None):
        try:
            if media.file:
                media_url = media.file.url
        except (ValueError, OSError):
            media_url = ""

    # Sync needs both playable media and at least one timed segment.
    sync_enabled = bool(media_url) and sync_enabled

    return render(
        request,
        "speech_center/transcript_segments.html",
        {
            "page_title": _("Transcript Segments"),
            "nav_active": "transcripts",
            "transcript": transcript,
            "media": media,
            "media_name": (
                (media.original_filename if media else "")
                or (media.object_key if media else "")
                or str(transcript.media_id)
            ),
            "media_url": media_url,
            "sync_enabled": sync_enabled,
            "duration_display": format_duration_ms(
                getattr(media, "duration_ms", None) if media else None
            ),
            "segment_rows": rows,
            "segment_count": len(rows),
            "detail_url": reverse(
                "speech_center:transcript_detail",
                args=[transcript.pk],
            ),
            "segments_player_config_json": {
                "mediaUrl": media_url,
                "syncEnabled": sync_enabled,
                "segments": [
                    {
                        "id": row["id"],
                        "startSec": row["start_sec"],
                        "endSec": row["end_sec"],
                        "hasTiming": row["has_timing"],
                    }
                    for row in rows
                ],
            },
        },
    )


@staff_member_required
@require_turing_capability("view_transcript")
@require_POST
def generate_ai_insights(request, transcript_id):
    """Enqueue AI analysis for a transcript (manual / retry). Non-blocking."""
    from turing.services.ai_analysis_trigger import (
        enqueue_transcript_analysis,
        has_analysis_rows,
        resolve_ui_state,
    )
    from turing.services.speech_center import SpeechCenterService

    transcript = get_object_or_404(_scoped_transcripts(request.user), pk=transcript_id)
    available = SpeechCenterService().get_available_intelligence(transcript) or {}
    if has_analysis_rows(available):
        return redirect("speech_center:transcript_detail", transcript_id=transcript.pk)

    state = resolve_ui_state(available, str(transcript.pk))
    if state != "generating":
        enqueue_transcript_analysis(transcript)

    return redirect("speech_center:transcript_detail", transcript_id=transcript.pk)


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
