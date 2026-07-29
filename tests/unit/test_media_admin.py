from __future__ import annotations

"""Admin form behavior for MediaAsset uploads."""

import io
import wave

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from turing.admin.media import MediaAssetAdmin, MediaAssetForm
from turing.domain.enums import SourceType, UseCase
from turing.models import MediaAsset, Organization
from turing.services.media import MediaService

User = get_user_model()


def _wav_bytes(duration_sec: float = 0.1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * duration_sec))
    return buf.getvalue()


@pytest.fixture
def org(db):
    return Organization.get_default()


@pytest.mark.django_db
def test_form_auto_populates_original_filename_from_upload(org):
    uploaded = SimpleUploadedFile(
        "interview_july_28.wav",
        _wav_bytes(),
        content_type="audio/wav",
    )
    form = MediaAssetForm(
        data={
            "source_type": SourceType.UPLOAD,
            "use_case": UseCase.GENERIC,
            "organization": org.pk,
            "metadata": "{}",
        },
        files={"file": uploaded},
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["original_filename"] == "interview_july_28.wav"


@pytest.mark.django_db
def test_form_preserves_manual_original_filename(org):
    uploaded = SimpleUploadedFile(
        "interview_july_28.wav",
        _wav_bytes(),
        content_type="audio/wav",
    )
    form = MediaAssetForm(
        data={
            "source_type": SourceType.UPLOAD,
            "use_case": UseCase.GENERIC,
            "organization": org.pk,
            "original_filename": "Custom display name.wav",
            "metadata": "{}",
        },
        files={"file": uploaded},
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["original_filename"] == "Custom display name.wav"


@pytest.mark.django_db
def test_form_rejects_create_without_file(org):
    form = MediaAssetForm(
        data={
            "source_type": SourceType.UPLOAD,
            "use_case": UseCase.GENERIC,
            "organization": org.pk,
            "original_filename": "should-not-save.wav",
            "metadata": "{}",
        }
    )
    assert not form.is_valid()
    assert "file" in form.errors
    assert form.errors["file"] == ["لطفاً قبل از ذخیره، فایل رسانه را بارگذاری کنید."]


@pytest.mark.django_db
def test_form_allows_existing_media_without_reupload(org):
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav_bytes()),
        filename="existing.wav",
        content_type="audio/wav",
        organization=org,
    )
    form = MediaAssetForm(
        data={
            "source_type": SourceType.UPLOAD,
            "use_case": media.use_case,
            "organization": org.pk,
            "original_filename": media.original_filename,
            "metadata": "{}",
        },
        instance=media,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_admin_add_rejects_save_without_file(org):
    admin_user = User.objects.create_superuser(
        username="media-admin",
        email="media@example.com",
        password="pass",
    )
    client = Client()
    client.force_login(admin_user)
    url = reverse("admin:turing_mediaasset_add")
    response = client.post(
        url,
        {
            "source_type": SourceType.UPLOAD,
            "use_case": UseCase.GENERIC,
            "organization": org.pk,
            "original_filename": "orphan.wav",
        },
    )
    assert response.status_code == 200
    assert MediaAsset.objects.count() == 0
    content = response.content.decode()
    assert "لطفاً قبل از ذخیره، فایل رسانه را بارگذاری کنید." in content


@pytest.mark.django_db
def test_admin_add_creates_media_with_auto_filename(org):
    admin_user = User.objects.create_superuser(
        username="media-uploader",
        email="upload@example.com",
        password="pass",
    )
    client = Client()
    client.force_login(admin_user)
    url = reverse("admin:turing_mediaasset_add")
    response = client.post(
        url,
        {
            "source_type": SourceType.UPLOAD,
            "use_case": UseCase.GENERIC,
            "organization": org.pk,
            "file": SimpleUploadedFile(
                "interview_july_28.wav",
                _wav_bytes(),
                content_type="audio/wav",
            ),
        },
        follow=True,
    )
    assert response.status_code == 200
    media = MediaAsset.objects.get()
    assert media.original_filename == "interview_july_28.wav"
    assert media.file or media.object_key


@pytest.mark.django_db
def test_create_transcription_go_redirects_to_speech_center(org, settings):
    """
    upload → media changelist (queue/review) → select + Go action
    → Speech Center home (named route).
    """
    from unittest.mock import patch

    from turing.conf import clear_settings_cache
    from turing.models import PlatformConfiguration, ProcessingJob

    settings.CELERY_TASK_ALWAYS_EAGER = True
    platform = PlatformConfiguration.get_solo()
    platform.default_language = "en"
    platform.save(update_fields=["default_language", "updated_at"])
    clear_settings_cache()

    admin_user = User.objects.create_superuser(
        username="media-go-flow",
        email="go@example.com",
        password="pass",
    )
    client = Client()
    client.force_login(admin_user)

    add_url = reverse("admin:turing_mediaasset_add")
    upload_resp = client.post(
        add_url,
        {
            "source_type": SourceType.UPLOAD,
            "use_case": UseCase.GENERIC,
            "organization": org.pk,
            "file": SimpleUploadedFile(
                "go_flow.wav",
                _wav_bytes(),
                content_type="audio/wav",
            ),
        },
    )
    # Post-upload still lands on the admin media queue/review path.
    assert upload_resp.status_code == 302
    assert reverse("admin:turing_mediaasset_changelist") in upload_resp["Location"]

    media = MediaAsset.objects.get()
    changelist = reverse("admin:turing_mediaasset_changelist")
    with patch(
        "turing.tasks.transcription.process_transcription_job.delay",
        return_value=None,
    ):
        go_resp = client.post(
            changelist,
            {
                "action": "create_transcription_jobs",
                "index": 0,
                "_selected_action": [str(media.pk)],
            },
        )

    assert go_resp.status_code == 302
    assert go_resp["Location"] == reverse("speech_center:dashboard")
    assert ProcessingJob.objects.filter(media=media).exists()


@pytest.mark.django_db
def test_create_transcription_go_keeps_page_on_validation_error(org):
    """Failed Go action must not redirect to Speech Center."""
    from turing.conf import clear_settings_cache
    from turing.models import PlatformConfiguration, ProcessingJob, SpeechProviderConfig

    platform = PlatformConfiguration.get_solo()
    platform.default_language = ""
    platform.save(update_fields=["default_language", "updated_at"])
    SpeechProviderConfig.objects.filter(code="speechmatics").update(default_language="")
    clear_settings_cache()

    admin_user = User.objects.create_superuser(
        username="media-go-fail",
        email="gofail@example.com",
        password="pass",
    )
    client = Client()
    client.force_login(admin_user)
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav_bytes()),
        filename="no_lang.wav",
        content_type="audio/wav",
        organization=org,
        uploaded_by=admin_user,
    )

    changelist = reverse("admin:turing_mediaasset_changelist")
    go_resp = client.post(
        changelist,
        {
            "action": "create_transcription_jobs",
            "index": 0,
            "_selected_action": [str(media.pk)],
        },
    )
    assert go_resp.status_code == 302
    assert reverse("admin:turing_mediaasset_changelist") in go_resp["Location"]
    assert reverse("speech_center:dashboard") not in go_resp["Location"]
    assert ProcessingJob.objects.filter(media=media).count() == 0

    followed = client.get(go_resp["Location"])
    assert "امکان ایجاد پردازش رونویسی نیست" in followed.content.decode()


@pytest.mark.django_db
def test_media_asset_admin_uses_custom_form():
    admin = MediaAssetAdmin(MediaAsset, AdminSite())
    assert admin.form is MediaAssetForm
