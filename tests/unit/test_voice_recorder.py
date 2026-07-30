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
    media = MediaAsset.objects.get(original_filename="recording-test.webm")
    assert resp["Location"].startswith(reverse("speech_center:create_transcript"))
    assert f"selected={media.id}" in resp["Location"]
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
    assert "upload_source" in text
    assert "csrfmiddlewaretoken" in text or "X-CSRFToken" in text
    for step in ("preparing", "uploading", "complete", "redirecting"):
        assert step in text


def test_boot_js_handles_cancel_and_permission_denied():
    text = (STATIC_RECORDER / "boot.js").read_text(encoding="utf-8")
    assert "sc-rec-delete" in text
    assert "Permission denied" in text or "permission denied" in text.lower()
    assert "unsupported" in text.lower()
    assert "deleteRecording" in text
    assert "revokePlaybackUrl" in text or "revokeObjectURL" in text
    assert "Recording uploaded successfully" in text
    assert "redirecting" in text


def test_media_recorder_has_no_aggressive_timeslice():
    """Multi-minute WebM must not be sliced every 250ms (playback corruption)."""
    text = (STATIC_RECORDER / "recorder.js").read_text(encoding="utf-8")
    assert "start(250)" not in text
    assert "start( 250 )" not in text
    assert "mediaRecorder.start()" in text
    assert "ondataavailable" in text
    assert "onstop" in text
    assert "new Blob(self.chunks" in text or "new Blob(self.chunks," in text
    # requestData-before-stop caused empty/partial blobs — must not be called.
    assert ".requestData()" not in text
    assert "validateBlob" in text


def test_blob_validation_rejects_tiny_multi_minute_recording():
    """Mirror client rules: 03:29 at 260 bytes must fail validation."""
    text = (STATIC_RECORDER / "recorder.js").read_text(encoding="utf-8")
    assert "MIN_BLOB_BYTES = 1024" in text
    assert "MIN_BYTES_PER_SEC = 500" in text

    min_blob = 1024
    min_bps = 500

    def min_expected(duration_ms: int) -> int:
        sec = max(0, duration_ms) / 1000
        return max(min_blob, int(sec * min_bps))

    def validate(size: int, duration_ms: int) -> bool:
        return size >= min_expected(duration_ms)

    duration_3m29 = (3 * 60 + 29) * 1000
    assert min_expected(duration_3m29) > 260
    assert not validate(260, duration_3m29)
    assert not validate(0, duration_3m29)
    # Healthy multi-minute Opus speech is typically hundreds of KB+.
    assert validate(500_000, duration_3m29)
    # Short clip still needs more than a bare header.
    assert not validate(260, 1_000)
    assert validate(2_000, 1_000)


def test_mime_fallback_order_unchanged():
    cfg = recorder_client_config()
    assert cfg["preferredMimeTypes"][0].startswith("audio/webm")
    assert any(m.startswith("audio/ogg") for m in cfg["preferredMimeTypes"])
    assert "debug" in cfg


def test_boot_blocks_upload_of_invalid_blob():
    boot = (STATIC_RECORDER / "boot.js").read_text(encoding="utf-8")
    assert "assertUploadableBlob" in boot
    assert "validateBlob" in boot
    assert "[TuringRecorder]" in boot


def test_multi_minute_recording_builds_single_coherent_blob():
    """
    Simulate long-recording chunk collection without timeslice:

    Without start(timeslice), browsers typically emit one (or few) final
    chunk(s) on stop — concatenating those yields a coherent container.
    Aggressive 250ms slicing would produce hundreds of fragments.
    """
    # Simulate ~3 minutes at 250ms timeslice (the old bug): 720 fragments.
    timesliced_chunk_count = int((3 * 60 * 1000) / 250)
    assert timesliced_chunk_count == 720

    # Fixed flow: one final payload (optionally + requestData flush).
    final_chunks = [b"\x1a\x45\xdf\xa3" + (b"\x00" * 4096)]  # header-ish + payload
    blob_bytes = b"".join(final_chunks)
    assert len(final_chunks) < 10  # coherent stop emission, not hundreds of slices
    assert len(blob_bytes) > 0
    # MIME preference unchanged in source
    recorder_js = (STATIC_RECORDER / "recorder.js").read_text(encoding="utf-8")
    assert "audio/webm;codecs=opus" in recorder_js
    assert "audio/ogg" in recorder_js


def test_playback_uses_original_recorded_blob_not_reencode():
    boot = (STATIC_RECORDER / "boot.js").read_text(encoding="utf-8")
    # syncPlayback receives the stop() blob / recorder.blob — not exportBlob output.
    assert "function syncPlayback" in boot
    assert "createObjectURL(blob)" in boot
    assert "audioEl.load()" in boot
    # Save path still may call exportBlob for trim; playback path must not.
    sync_idx = boot.index("function syncPlayback")
    save_idx = boot.index('btnSave.addEventListener')
    sync_block = boot[sync_idx:save_idx]
    assert "exportBlob" not in sync_block
    assert "audioBufferToWav" not in sync_block
    # afterStop wires original recording into playback
    assert "syncPlayback(blob" in boot


@pytest.mark.django_db
def test_recorder_upload_success_message_and_selected_redirect(sc_client, sc_user):
    org = Organization.get_default()
    resp = sc_client.post(
        reverse("speech_center:upload_media"),
        {
            "organization_id": str(org.id),
            "upload_source": "recorder",
            "file": SimpleUploadedFile(
                "recording-ux.webm",
                _tiny_webm(),
                content_type="audio/webm",
            ),
        },
    )
    assert resp.status_code == 302
    media = MediaAsset.objects.get(original_filename="recording-ux.webm")
    assert media.uploaded_by_id == sc_user.id
    loc = resp["Location"]
    assert loc.startswith(reverse("speech_center:create_transcript"))
    assert f"selected={media.id}" in loc

    # Follow redirect — success message + highlighted row
    page = sc_client.get(loc)
    assert page.status_code == 200
    body = page.content.decode()
    assert "Recording uploaded successfully" in body
    assert "recording-ux.webm" in body
    assert "Just uploaded" in body
    assert "sc-row-selected" in body
    assert str(media.id) in body


@pytest.mark.django_db
def test_file_upload_still_works_with_selected_context(sc_client, sc_user):
    """Normal Upload File path unchanged except selected= context on create page."""
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    org = Organization.get_default()
    resp = sc_client.post(
        reverse("speech_center:upload_media"),
        {
            "organization_id": str(org.id),
            "file": SimpleUploadedFile(
                "normal-file.wav",
                buf.getvalue(),
                content_type="audio/wav",
            ),
        },
    )
    assert resp.status_code == 302
    media = MediaAsset.objects.get(original_filename="normal-file.wav")
    assert f"selected={media.id}" in resp["Location"]
    page = sc_client.get(resp["Location"])
    body = page.content.decode()
    # File upload keeps generic success copy (not recorder-specific).
    assert "Uploaded normal-file.wav" in body
    assert "Recording uploaded successfully" not in body
    assert "normal-file.wav" in body
    assert "Just uploaded" in body


@pytest.mark.django_db
def test_upload_page_progress_labels_are_clear(sc_client):
    resp = sc_client.get(reverse("speech_center:upload_media"))
    body = resp.content.decode()
    assert "Preparing recording" in body
    assert "Uploading recording" in body
    assert "Upload complete" in body
    assert "Redirecting to transcript creation" in body
