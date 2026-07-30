from __future__ import annotations

"""Smoke tests for Speech Center demo UI (presentation layer only)."""

import io

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from turing.domain.enums import JobStatus, TranscriptStatus, UseCase
from turing.models import Organization, ProcessingJob, Transcript
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
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
    assert "Upload Content" in content or "Upload Media" in content
    assert "Send to Transcription" in content
    assert "View Status" in content
    assert "Speech Center" in content
    assert reverse("speech_center:upload_media") in content
    assert reverse("admin:turing_mediaasset_add") not in content


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


@pytest.mark.django_db
def test_anonymous_redirected_to_login(client):
    resp = client.get(reverse("speech_center:dashboard"))
    assert resp.status_code == 302
    assert "/admin/login" in resp.url or "login" in resp.url
