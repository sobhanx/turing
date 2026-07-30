from __future__ import annotations

"""Smoke tests for Speech Center demo UI (presentation layer only)."""

import io

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from turing.domain.enums import JobStatus, TranscriptStatus, UseCase
from turing.models import Organization, ProcessingJob, Speaker, Transcript, TranscriptSegment
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
    job = ProcessingJob(status=JobStatus.QUEUED, ingest_status="pending", attempt_count=0)
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
    assert "Import Meeting" in content
    assert "Send to Transcription" in content
    assert "View Status" in content
    assert "Speech Center" in content
    assert reverse("speech_center:upload_media") in content
    assert reverse("speech_center:meetings") in content
    assert reverse("admin:turing_mediaasset_add") not in content


@pytest.mark.django_db
def test_meetings_foundation_page(sc_client):
    resp = sc_client.get(reverse("speech_center:meetings"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Meetings" in body
    assert "Alocom" in body
    assert "Zoom" in body
    assert "Teams" in body
    assert "Scheduled" in body
    assert "Coming soon" in body


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
    assert "Audio uploaded" in body
    assert "Speech recognition" in body
    assert "Processing pipeline" in body or "sc-pipeline" in body
    assert "data-sc-poll" in body


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
    assert "View Words" in body
    assert "View Analysis" in body
    assert "Open Intelligence" in body
    assert "Export PDF" in body
    assert "Export DOCX" in body
    assert "admin/turing/transcriptsegment/" in body


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
