from __future__ import annotations

"""Phase B — Voice Recorder plugs into existing Speech Center upload path."""

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from turing.conf import get_turing_settings
from turing.domain.enums import TuringRole, UseCase
from turing.models import MediaAsset, Organization, TuringMembership
from turing.services.media import MediaService
from turing.ui.speech_center.recorder.hooks import recorder_client_config

User = get_user_model()
STATIC_RECORDER = (
    Path(__file__).resolve().parents[2] / "turing" / "static" / "speech_center" / "recorder"
)


@pytest.fixture
def sc_user(db):
    return User.objects.create_superuser("rec-admin", "rec@example.com", "pass")


@pytest.fixture
def sc_client(client, sc_user):
    client.force_login(sc_user)
    return client


def _tiny_webm() -> bytes:
    # Minimal-ish bytes; validation cares about extension/MIME/size, not decode.
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def test_recorder_static_modules_exist():
    for name in ("recorder.js", "waveform.js", "uploader.js", "boot.js"):
        path = STATIC_RECORDER / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        # Client must not invent a second upload API — only FormData to Speech Center.
        assert "/api/turing" not in text
        assert "create_from_upload" not in text
    # Browser fallback + permission surfaces
    recorder_js = (STATIC_RECORDER / "recorder.js").read_text(encoding="utf-8")
    assert "isSupported" in recorder_js
    assert "denied" in recorder_js
    assert "audio/webm" in recorder_js
    assert "audio/ogg" in recorder_js


def test_recorder_client_config_prefers_webm_then_ogg():
    cfg = recorder_client_config()
    assert cfg["maxUploadBytes"] > 0
    mimes = cfg["preferredMimeTypes"]
    assert mimes[0].startswith("audio/webm")
    assert any(m.startswith("audio/ogg") for m in mimes)


@pytest.mark.django_db
def test_upload_page_has_file_and_record_tabs(sc_client):
    resp = sc_client.get(reverse("speech_center:upload_media"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Upload File" in body
    assert "Record Audio" in body
    assert 'id="sc-panel-record"' in body
    assert "speech_center/recorder/recorder.js" in body
    assert "speech_center/recorder/waveform.js" in body
    assert "speech_center/recorder/uploader.js" in body
    assert "sc-recorder-config" in body
    assert "Permission" in body or "Microphone" in body
    assert "Start Recording" in body
    assert "Save &amp; Upload" in body or "Save & Upload" in body


@pytest.mark.django_db
def test_upload_recorded_webm_creates_media_asset(sc_client, sc_user):
    org = Organization.get_default()
    url = reverse("speech_center:upload_media")
    resp = sc_client.post(
        url,
        {
            "organization_id": str(org.id),
            "file": SimpleUploadedFile(
                "recording-test.webm",
                _tiny_webm(),
                content_type="audio/webm",
            ),
        },
    )
    assert resp.status_code == 302
    assert resp["Location"] == reverse("speech_center:create_transcript")
    media = MediaAsset.objects.get(original_filename="recording-test.webm")
    assert media.organization_id == org.id
    assert media.uploaded_by_id == sc_user.id
    assert media.byte_size > 0
    assert media.object_key
    assert media.file.name
    assert media.source_type == "upload"


@pytest.mark.django_db
def test_upload_recorded_ogg_fallback(sc_client, sc_user):
    org = Organization.get_default()
    resp = sc_client.post(
        reverse("speech_center:upload_media"),
        {
            "organization_id": str(org.id),
            "file": SimpleUploadedFile(
                "recording-test.ogg",
                b"OggS" + b"\x00" * 32,
                content_type="audio/ogg",
            ),
        },
    )
    assert resp.status_code == 302
    media = MediaAsset.objects.get(original_filename="recording-test.ogg")
    assert media.organization_id == org.id
    assert media.uploaded_by_id == sc_user.id


@pytest.mark.django_db
def test_recorded_upload_assigns_selected_organization(sc_client, sc_user):
    other = Organization.objects.create(name="Recorder Org", slug="recorder-org")
    resp = sc_client.post(
        reverse("speech_center:upload_media"),
        {
            "organization_id": str(other.id),
            "file": SimpleUploadedFile(
                "org-recording.webm",
                _tiny_webm(),
                content_type="audio/webm",
            ),
        },
    )
    assert resp.status_code == 302
    media = MediaAsset.objects.get(original_filename="org-recording.webm")
    assert media.organization_id == other.id


@pytest.mark.django_db
def test_recorded_upload_requires_organization(sc_client):
    resp = sc_client.post(
        reverse("speech_center:upload_media"),
        {
            "file": SimpleUploadedFile(
                "no-org.webm",
                _tiny_webm(),
                content_type="audio/webm",
            ),
        },
    )
    assert resp.status_code == 302
    assert resp["Location"] == reverse("speech_center:upload_media")
    assert not MediaAsset.objects.filter(original_filename="no-org.webm").exists()


@pytest.mark.django_db
def test_recorder_upload_denied_without_capability(client, db):
    user = User.objects.create_user("viewer-only", password="pass", is_staff=True)
    org = Organization.get_default()
    TuringMembership.objects.create(
        user=user, organization=org, role=TuringRole.VIEWER, is_active=True
    )
    client.force_login(user)
    resp = client.get(reverse("speech_center:upload_media"))
    assert resp.status_code == 403
    resp = client.post(
        reverse("speech_center:upload_media"),
        {
            "organization_id": str(org.id),
            "file": SimpleUploadedFile(
                "denied.webm",
                _tiny_webm(),
                content_type="audio/webm",
            ),
        },
    )
    assert resp.status_code == 403
    assert not MediaAsset.objects.filter(original_filename="denied.webm").exists()


@pytest.mark.django_db
def test_large_recording_rejected_by_existing_validation(sc_client):
    """Oversized recordings fail via MediaService validation — same as file upload."""
    from types import SimpleNamespace

    from turing.domain.exceptions import ValidationError

    org = Organization.get_default()
    max_bytes = 128
    base = get_turing_settings()
    fake = SimpleNamespace(**{**base.__dict__, "max_upload_bytes": max_bytes})
    big = b"\x1a\x45\xdf\xa3" + b"\x00" * 500
    with patch("turing.services.media.get_turing_settings", return_value=fake), patch(
        "turing.media.validation.get_turing_settings", return_value=fake
    ):
        resp = sc_client.post(
            reverse("speech_center:upload_media"),
            {
                "organization_id": str(org.id),
                "file": SimpleUploadedFile(
                    "huge.webm",
                    big,
                    content_type="audio/webm",
                ),
            },
        )
    assert resp.status_code == 302
    assert resp["Location"] == reverse("speech_center:upload_media")
    assert not MediaAsset.objects.filter(original_filename="huge.webm").exists()

    with patch("turing.services.media.get_turing_settings", return_value=fake), patch(
        "turing.media.validation.get_turing_settings", return_value=fake
    ):
        with pytest.raises(ValidationError):
            MediaService().create_from_upload(
                uploaded_file=io.BytesIO(big),
                filename="huge2.webm",
                content_type="audio/webm",
                organization=org,
            )


@pytest.mark.django_db
def test_pipeline_integration_recorded_media_can_create_job(sc_client, sc_user):
    from turing.models import ProcessingJob
    from turing.services.job_orchestrator import JobOrchestrator

    org = Organization.get_default()
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_tiny_webm()),
        filename="pipeline-rec.webm",
        content_type="audio/webm",
        use_case=UseCase.GENERIC,
        organization=org,
        uploaded_by=sc_user,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    assert ProcessingJob.objects.filter(pk=job.pk, media=media).exists()
    assert job.organization_id == org.id


def test_uploader_js_posts_same_form_fields():
    text = (STATIC_RECORDER / "uploader.js").read_text(encoding="utf-8")
    assert 'append("organization_id"' in text or "append('organization_id'" in text
    assert 'append("file"' in text or "append('file'" in text
    assert "csrfmiddlewaretoken" in text or "X-CSRFToken" in text
    for step in (
        "preparing",
        "uploading",
        "queued",
        "processing",
        "transcript",
        "completed",
    ):
        assert step in text


def test_boot_js_handles_cancel_and_permission_denied():
    text = (STATIC_RECORDER / "boot.js").read_text(encoding="utf-8")
    assert "sc-rec-delete" in text
    assert "Permission denied" in text or "permission denied" in text.lower()
    assert "unsupported" in text.lower()
    assert "deleteRecording" in text
