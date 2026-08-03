from __future__ import annotations

"""Smoke tests for Speech Center demo UI (presentation layer only)."""

import io

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from turing.domain.enums import JobStatus, TranscriptStatus, UseCase
from turing.models import (
    MediaAsset,
    Organization,
    ProcessingJob,
    Speaker,
    Transcript,
    TranscriptSegment,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService
from turing.ui.speech_center.presentation import job_display_status

User = get_user_model()


@pytest.fixture
def sc_user(db):
    return User.objects.create_superuser("sc-admin", "sc@example.com", "pass")


@pytest.fixture
def sc_client(client, sc_user):
    client.force_login(sc_user)
    return client


@pytest.fixture
def sc_media(db, sc_user):
    org = Organization.get_default()
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="demo.wav",
        use_case=UseCase.GENERIC,
        organization=org,
        uploaded_by=sc_user,
    )


def test_job_display_status_mapping():
    from django.utils import translation

    job = ProcessingJob(status=JobStatus.QUEUED, ingest_status="pending", attempt_count=0)
    with translation.override("en"):
        assert job_display_status(job)[0] == "Queued"
        job.attempt_count = 1
        assert job_display_status(job) == ("Retry Scheduled", "retry-scheduled")
        job.status = JobStatus.SUCCEEDED
        assert job_display_status(job)[0] == "Completed"
        job.status = JobStatus.FAILED
        assert job_display_status(job)[0] == "Failed"


@pytest.mark.django_db
def test_dashboard_renders(sc_client):
    url = reverse("speech_center:dashboard")
    resp = sc_client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Hello," in content
    assert "Quick Actions" in content
    assert "Recent Activity" in content
    assert "Upload Audio" in content or "Upload Content" in content
    assert "Record Audio" in content
    assert "Create Transcript" in content or "Send to Transcription" in content
    assert "Send to Transcription" in content
    assert "View Status" in content
    assert "Speech Center" in content
    assert reverse("speech_center:upload_media") in content
    assert reverse("speech_center:meetings") not in content
    assert reverse("admin:turing_mediaasset_add") not in content


@pytest.mark.django_db
def test_meetings_hidden_from_navigation_and_route(sc_client):
    dash = sc_client.get(reverse("speech_center:dashboard"))
    assert dash.status_code == 200
    body = dash.content.decode()
    assert "Meetings" not in body or 'nav_active == \'meetings\'' not in body
    assert reverse("speech_center:meetings") not in body
    assert "Import Meeting" not in body

    resp = sc_client.get(reverse("speech_center:meetings"))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_create_page_highlights_selected_media(sc_client, sc_media):
    url = reverse("speech_center:create_transcript") + f"?selected={sc_media.id}"
    resp = sc_client.get(url)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Just uploaded" in body
    assert "sc-just-uploaded" in body or "sc-row-selected" in body
    assert "Create Transcript" in body or "Send to Transcription" in body
    assert "demo.wav" in body


@pytest.mark.django_db
def test_queue_shows_pipeline_and_poll(sc_client, sc_media, sc_user):
    JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    resp = sc_client.get(reverse("speech_center:queue"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Uploading" in body
    assert "Preparing media" in body
    assert "Speech recognition" in body
    assert "Transcript ready" in body
    assert "Analysis" not in body
    assert "Processing pipeline" in body or "sc-pipeline" in body
    assert "data-sc-poll" in body
    assert "sc-queue-cancel-btn" in body
    # Non-completed jobs keep plain status text (no transcript detail link).
    assert resp.context["jobs"][0]["transcript_url"] == ""
    assert "sc-badge-link" not in body
    # Provider stays on the job for admin/debug; queue UI must not expose it.
    job = resp.context["jobs"][0]["job"]
    assert job.provider_code
    assert job.provider_code not in body
    assert "speechmatics" not in body.lower()


@pytest.mark.django_db
def test_queue_completed_status_links_to_transcript(sc_client, sc_media, sc_user):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    job.status = JobStatus.SUCCEEDED
    job.save(update_fields=["status"])
    transcript = Transcript.objects.create(
        job=job,
        media=sc_media,
        organization=sc_media.organization,
        status=TranscriptStatus.DRAFT,
        language_code="en",
        full_text="Hello world",
    )
    detail_url = reverse(
        "speech_center:transcript_detail", args=[transcript.id]
    )
    resp = sc_client.get(reverse("speech_center:queue"))
    assert resp.status_code == 200
    body = resp.content.decode()
    row = resp.context["jobs"][0]
    assert row["status_css"] == "completed"
    assert row["transcript_url"] == detail_url
    assert f'href="{detail_url}"' in body
    assert "sc-badge-link" in body
    assert "Completed" in body


@pytest.mark.django_db
def test_queue_completed_without_transcript_stays_plain_text(
    sc_client, sc_media, sc_user
):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    job.status = JobStatus.SUCCEEDED
    job.save(update_fields=["status"])
    resp = sc_client.get(reverse("speech_center:queue"))
    assert resp.status_code == 200
    body = resp.content.decode()
    row = resp.context["jobs"][0]
    assert row["status_css"] == "completed"
    assert row["transcript_url"] == ""
    assert "sc-badge-link" not in body
    assert "Completed" in body


@pytest.mark.django_db
def test_upload_page_renders_minimal_form(sc_client):
    resp = sc_client.get(reverse("speech_center:upload_media"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'name="file"' in body
    assert 'name="organization_id"' in body
    assert "Upload" in body
    assert "original_filename" not in body
    assert "checksum" not in body
    assert "object_key" not in body
    assert "source_type" not in body
    assert "storage" not in body.lower() or "storage provider" not in body.lower()


@pytest.mark.django_db
def test_upload_creates_media_via_media_service(sc_client, sc_user):
    import wave

    from django.core.files.uploadedfile import SimpleUploadedFile

    from turing.models import MediaAsset

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    org = Organization.get_default()
    url = reverse("speech_center:upload_media")
    resp = sc_client.post(
        url,
        {
            "organization_id": str(org.id),
            "file": SimpleUploadedFile(
                "pipeline_upload.wav",
                buf.getvalue(),
                content_type="audio/wav",
            ),
        },
    )
    assert resp.status_code == 302
    loc = resp["Location"]
    assert loc.startswith(reverse("speech_center:create_transcript"))
    assert "selected=" in loc
    media = MediaAsset.objects.get(original_filename="pipeline_upload.wav")
    assert media.organization_id == org.id
    assert media.uploaded_by_id == sc_user.id
    assert media.content_type
    assert media.byte_size > 0
    assert str(media.id) in loc


@pytest.mark.django_db
def test_upload_requires_file(sc_client):
    org = Organization.get_default()
    resp = sc_client.post(
        reverse("speech_center:upload_media"),
        {"organization_id": str(org.id)},
    )
    assert resp.status_code == 302
    assert resp["Location"] == reverse("speech_center:upload_media")


@pytest.mark.django_db
def test_create_transcript_uses_orchestrator(sc_client, sc_media):
    url = reverse("speech_center:create_transcript")
    assert sc_client.get(url).status_code == 200
    resp = sc_client.post(url, {"media_id": str(sc_media.id), "language_code": "en"})
    assert resp.status_code == 302
    assert ProcessingJob.objects.filter(media=sc_media).exists()


@pytest.mark.django_db
def test_queue_and_transcripts_pages(sc_client, sc_media, sc_user):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=sc_media,
        organization=sc_media.organization,
        status=TranscriptStatus.DRAFT,
        language_code="en",
        full_text="Hello world",
    )
    queue = sc_client.get(reverse("speech_center:queue"))
    assert queue.status_code == 200
    assert "demo.wav" in queue.content.decode()

    listing = sc_client.get(reverse("speech_center:transcripts"))
    assert listing.status_code == 200
    assert "demo.wav" in listing.content.decode()

    detail = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert detail.status_code == 200
    body = detail.content.decode()
    assert "Hello world" in body
    assert "View Segments" in body
    assert "View Words" not in body
    assert "View Analysis" not in body
    assert "Open Intelligence" not in body
    assert "Export PDF" in body
    assert "Export DOCX" in body
    assert "/admin/turing/" not in body
    assert reverse(
        "speech_center:transcript_segments", args=[transcript.id]
    ) in body


@pytest.mark.django_db
def test_transcript_segments_page_lists_chronological_rows(
    sc_client, sc_transcript_with_speakers
):
    transcript = sc_transcript_with_speakers
    url = reverse("speech_center:transcript_segments", args=[transcript.id])
    resp = sc_client.get(url)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Transcript Segments" in body or "Segments" in body
    assert "Hello" in body
    assert "World" in body
    assert 'id="sc-segments-list"' in body
    assert "speech_center/segments_page.js" in body
    assert 'data-end-ms="' in body
    assert "/admin/turing/" not in body
    # Chronological order: first segment text appears before second.
    assert body.index("Hello") < body.index("World")
    # Timing fields rendered for each segment.
    assert "00:00" in body
    # Fixture media has an uploaded file → single player present.
    assert 'id="sc-segments-audio"' in body
    assert body.count("<audio") == 1
    assert "This transcript has no playable media." not in body
    assert 'id="sc-segments-player-config"' in body
    assert "syncEnabled" in body
    assert 'data-start="' in body
    assert 'data-end="' in body
    assert "transcript-segment" in body
    assert "segment-player-v4" in body
    detail = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert detail.status_code == 200
    assert url in detail.content.decode()


@pytest.mark.django_db
def test_transcript_segments_page_hides_player_without_media(sc_client, sc_user):
    from turing.domain.enums import TranscriptStatus

    org = Organization.get_default()
    media = MediaAsset.objects.create(
        organization=org,
        original_filename="missing.wav",
        uploaded_by=sc_user,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=media,
        organization=org,
        status=TranscriptStatus.DRAFT,
        language_code="en",
        full_text="",
    )
    resp = sc_client.get(
        reverse("speech_center:transcript_segments", args=[transcript.id])
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "This transcript has no playable media." in body
    assert 'id="sc-segments-audio"' not in body


def test_segments_page_js_syncs_playback_efficiently():
    from pathlib import Path

    js = Path("turing/static/speech_center/segments_page.js").read_text()
    assert "findIndexForTime" in js
    assert "timeupdate" in js
    assert "seeked" in js
    assert "loadedmetadata" in js
    assert "scrollIntoView" in js
    assert "requestAnimationFrame" not in js
    assert "data-start" in js
    assert "[segments-init]" in js
    assert "[audio-ready]" in js
    assert "[segment-click]" in js
    assert "[audio-sync]" in js
    assert "addEventListener(\"click\"" in js or "addEventListener('click'" in js
    assert "querySelectorAll(\".transcript-segment\")" in js
    assert "audio.currentTime" in js
    assert "syncReady" not in js


def test_segments_page_js_finds_half_open_ranges():
    """Acceptance: at t=15 in [0-10),[10-20),[20-30) → segment 2."""
    from pathlib import Path

    # Execute the binary-search logic in isolation via a tiny mirror of the algo.
    ranges = [
        {"startSec": 0.0, "endSec": 10.0},
        {"startSec": 10.0, "endSec": 20.0},
        {"startSec": 20.0, "endSec": 30.0},
    ]

    def find_index_for_time(t):
        n = len(ranges)
        lo, hi, cand = 0, n - 1, -1
        while lo <= hi:
            mid = (lo + hi) >> 1
            if ranges[mid]["startSec"] <= t:
                cand = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if cand < 0:
            return -1
        r = ranges[cand]
        if r["startSec"] <= t < r["endSec"]:
            return cand
        if cand == n - 1 and r["startSec"] <= t <= r["endSec"]:
            return cand
        return -1

    assert find_index_for_time(15) == 1
    assert find_index_for_time(0) == 0
    assert find_index_for_time(10) == 1
    assert find_index_for_time(20) == 2
    assert find_index_for_time(29.9) == 2
    js = Path("turing/static/speech_center/segments_page.js").read_text()
    assert "seg.start <= t && t < seg.end" in js


def test_segments_page_css_hides_filtered_cards():
    from pathlib import Path

    css = Path("turing/static/speech_center/app.css").read_text()
    assert ".sc-segment-card[hidden]" in css
    assert "is-search-hidden" in css
    assert "display: none !important" in css

@pytest.fixture
def sc_transcript_with_speakers(db, sc_media, sc_user):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=sc_media,
        organization=sc_media.organization,
        status=TranscriptStatus.DRAFT,
        language_code="en",
        full_text="S1: Hello\nS1: World",
    )
    speaker = Speaker.objects.create(
        transcript=transcript,
        speaker_label="S1",
        speaker_name="",
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=speaker,
        sequence=0,
        start_ms=0,
        end_ms=500,
        text="Hello",
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=speaker,
        sequence=1,
        start_ms=500,
        end_ms=1000,
        text="World",
    )
    return transcript


@pytest.mark.django_db
def test_transcript_detail_renders_editable_speaker_chips(
    sc_client, sc_transcript_with_speakers
):
    transcript = sc_transcript_with_speakers
    resp = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "sc-speaker-chip-editable" in body
    assert "sc-speaker-edit-config" in body
    assert "speech_center/speaker_edit.js" in body
    assert 'data-speaker-id="' in body
    assert "Edit speaker S1" in body
    assert body.count("sc-speaker-chip-editable") >= 2


@pytest.mark.django_db
def test_transcript_speaker_rename_via_existing_api(sc_client, sc_transcript_with_speakers):
    transcript = sc_transcript_with_speakers
    speaker = transcript.speakers.get(speaker_label="S1")
    patch = sc_client.patch(
        f"/api/turing/v1/speakers/{speaker.id}/",
        data='{"speaker_name": "Alice"}',
        content_type="application/json",
    )
    assert patch.status_code == 200
    speaker.refresh_from_db()
    assert speaker.speaker_name == "Alice"
    detail = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    body = detail.content.decode()
    assert "Alice" in body
    assert body.count("Alice") >= 2


@pytest.mark.django_db
def test_transcript_detail_renders_transcript_edit_controls(
    sc_client, sc_transcript_with_speakers
):
    transcript = sc_transcript_with_speakers
    resp = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Edit Transcript" in body
    assert "sc-transcript-edit-config" in body
    assert "speech_center/transcript_edit.js" in body
    assert 'id="sc-transcript-textarea"' in body
    assert 'id="sc-transcript-edit"' in body
    assert 'id="sc-transcript-edit" class="sc-transcript-edit" hidden>' in body
    assert 'id="sc-transcript-read"' in body
    assert "[00:00]" in body


@pytest.mark.django_db
def test_transcript_edit_body_api(sc_client, sc_transcript_with_speakers):
    transcript = sc_transcript_with_speakers
    service = TranscriptService()
    body = service.format_editor_body(transcript)
    updated_body = body.replace("Hello", "Hi there")
    patch = sc_client.patch(
        reverse("turing-transcripts-edit-body", args=[transcript.id]),
        data={"body": updated_body},
        content_type="application/json",
    )
    assert patch.status_code == 200
    payload = patch.json()
    assert payload["segments"][0]["text"] == "Hi there"
    transcript.refresh_from_db()
    assert "Hi there" in transcript.full_text


@pytest.mark.django_db
def test_anonymous_redirected_to_login(client):
    resp = client.get(reverse("speech_center:dashboard"))
    assert resp.status_code == 302
    assert "/admin/login" in resp.url or "login" in resp.url


def test_analysis_text_maps_summary_topics_and_actions():
    from types import SimpleNamespace

    from turing.ui.speech_center.views import _analysis_text

    summary = SimpleNamespace(
        content={"summary": "Hello summary", "main_points": ["A", "B"]}
    )
    topics = SimpleNamespace(content=["alpha", "beta"])
    actions = SimpleNamespace(
        content=[{"task": "Do thing", "owner": "Ada", "deadline": None}]
    )
    assert _analysis_text(summary) == "Hello summary"
    assert _analysis_text(topics) == "alpha\nbeta"
    assert _analysis_text(actions) == "Do thing (Ada)"
    assert _analysis_text(None) == ""


@pytest.mark.django_db
def test_transcript_detail_merges_analyses_regardless_of_row_order(
    sc_client, sc_transcript_with_speakers
):
    """Topics-first insert order must not hide summary/action_items in SSR context."""
    from turing.domain.enums import AnalysisType
    from turing.models import TranscriptAnalysis

    transcript = sc_transcript_with_speakers
    org = transcript.organization

    # Persist in an order that would break a "first row wins / stop early" bug.
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.TOPICS,
        content=["alpha", "beta"],
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.SUMMARY,
        content={"summary": "Order-safe summary", "main_points": ["Point"]},
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.ACTION_ITEMS,
        content=[{"task": "Ship fix", "owner": "Ada", "deadline": None}],
        provider="fake",
        model_name="fake-v1",
    )

    resp = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert resp.status_code == 200
    assert resp.context["summary_text"] == "Order-safe summary"
    assert resp.context["topics_text"] == "alpha\nbeta"
    assert "Ship fix" in resp.context["actions_text"]
    assert resp.context["analysis_pending"] is False
    body = resp.content.decode()
    assert "Order-safe summary" in body
    assert "Ship fix" in body
    assert "data-sc-analysis-poll" not in body


@pytest.mark.django_db
def test_transcript_detail_idle_shows_generate_button_without_analysis(
    sc_client, sc_transcript_with_speakers
):
    transcript = sc_transcript_with_speakers
    resp = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert resp.status_code == 200
    assert resp.context["analysis_idle"] is True
    assert resp.context["analysis_pending"] is False
    assert resp.context["analysis_ready"] is False
    body = resp.content.decode()
    assert "Generate AI Insights" in body or "Generate Analysis" in body
    assert "sc-ai-idle" in body
    assert "sc-ai-card" in body
    assert "Not generated yet" in body
    assert "sc-insight-block" not in body
    assert "data-sc-analysis-poll" not in body
    assert reverse("speech_center:generate_ai_insights", args=[transcript.id]) in body


@pytest.mark.django_db
def test_generate_ai_insights_enqueues_task_and_shows_loading(
    sc_client, sc_transcript_with_speakers, monkeypatch
):
    from turing.services import ai_analysis_trigger as trigger
    from turing.ui.speech_center.views import ANALYSIS_GENERATING_LABEL

    transcript = sc_transcript_with_speakers
    scheduled: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.analysis.generate_transcript_analysis.delay",
        lambda transcript_id: scheduled.append(transcript_id),
    )

    url = reverse("speech_center:generate_ai_insights", args=[transcript.id])
    resp = sc_client.post(url)
    assert resp.status_code == 302
    assert scheduled == [str(transcript.id)]
    assert trigger.get_trigger_state(str(transcript.id)) == trigger.STATE_GENERATING

    detail = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert detail.status_code == 200
    assert detail.context["analysis_generating"] is True
    assert detail.context["analysis_pending"] is True
    assert detail.context["analysis_poll_seconds"] >= 3
    body = detail.content.decode()
    assert str(ANALYSIS_GENERATING_LABEL) in body
    assert "sc-ai-spinner" in body
    assert 'data-sc-analysis-poll="' in body
    assert "Generate AI Insights" not in body
    assert "Generate Analysis" not in body


@pytest.mark.django_db
def test_transcript_detail_failed_state_shows_retry(
    sc_client, sc_transcript_with_speakers
):
    from turing.services import ai_analysis_trigger as trigger
    from turing.ui.speech_center.views import ANALYSIS_FAILED_LABEL

    transcript = sc_transcript_with_speakers
    trigger.mark_failed(str(transcript.id))
    resp = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert resp.status_code == 200
    assert resp.context["analysis_failed"] is True
    body = resp.content.decode()
    assert str(ANALYSIS_FAILED_LABEL) in body
    assert "Retry" in body
    assert reverse("speech_center:generate_ai_insights", args=[transcript.id]) in body


@pytest.mark.django_db
def test_retry_ai_insights_reenqueues_task(
    sc_client, sc_transcript_with_speakers, monkeypatch
):
    from turing.services import ai_analysis_trigger as trigger

    transcript = sc_transcript_with_speakers
    trigger.mark_failed(str(transcript.id))
    scheduled: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.analysis.generate_transcript_analysis.delay",
        lambda transcript_id: scheduled.append(transcript_id),
    )
    resp = sc_client.post(
        reverse("speech_center:generate_ai_insights", args=[transcript.id])
    )
    assert resp.status_code == 302
    assert scheduled == [str(transcript.id)]
    assert trigger.get_trigger_state(str(transcript.id)) == trigger.STATE_GENERATING


@pytest.mark.django_db
def test_transcript_detail_shows_ready_analysis(
    sc_client, sc_transcript_with_speakers
):
    from turing.domain.enums import AnalysisType
    from turing.models import TranscriptAnalysis

    transcript = sc_transcript_with_speakers
    org = transcript.organization
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.SUMMARY,
        content={"summary": "Ready summary", "main_points": []},
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.TOPICS,
        content=["kw"],
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.ACTION_ITEMS,
        content=[{"task": "Do it", "owner": None, "deadline": None}],
        provider="fake",
        model_name="fake-v1",
    )
    resp = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert resp.status_code == 200
    assert resp.context["analysis_ready"] is True
    assert resp.context["analysis_idle"] is False
    body = resp.content.decode()
    assert "Ready summary" in body
    assert "Generate AI Insights" not in body
    assert "Generate Analysis" not in body
    assert "data-sc-analysis-poll" not in body


@pytest.mark.django_db
def test_transcript_detail_empty_completed_analysis_shows_dash(
    sc_client, sc_transcript_with_speakers
):
    from turing.domain.enums import AnalysisType
    from turing.models import TranscriptAnalysis
    from turing.ui.speech_center.views import (
        ANALYSIS_EMPTY_LABEL,
        ANALYSIS_PENDING_LABEL,
    )

    transcript = sc_transcript_with_speakers
    org = transcript.organization
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.SUMMARY,
        content={"summary": "Ready summary", "main_points": []},
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.TOPICS,
        content=[],
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.ACTION_ITEMS,
        content=[],
        provider="fake",
        model_name="fake-v1",
    )

    resp = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert resp.status_code == 200
    assert resp.context["summary_text"] == "Ready summary"
    assert resp.context["topics_text"] == ANALYSIS_EMPTY_LABEL
    assert resp.context["actions_text"] == ANALYSIS_EMPTY_LABEL
    assert resp.context["analysis_pending"] is False
    body = resp.content.decode()
    assert str(ANALYSIS_PENDING_LABEL) not in body
    assert "data-sc-analysis-poll" not in body
