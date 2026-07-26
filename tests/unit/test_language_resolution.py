from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model

from turing.conf import clear_settings_cache
from turing.domain.enums import UseCase
from turing.domain.exceptions import ConfigurationError, ValidationError
from turing.models import PlatformConfiguration, SpeechProviderConfig
from turing.providers.speechmatics.adapter import SpeechmaticsAdapter
from turing.providers.types import TranscriptionRequest
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService


User = get_user_model()


@pytest.fixture
def media(db):
    user = User.objects.create_user(username="lang_user", password="pass")
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="sample.wav",
        use_case=UseCase.MEETING,
        uploaded_by=user,
    )


@pytest.fixture(autouse=True)
def _clear_lang_defaults(db):
    clear_settings_cache()
    platform = PlatformConfiguration.get_solo()
    platform.default_language = ""
    platform.save()
    SpeechProviderConfig.objects.filter(code="speechmatics").update(default_language="")
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.django_db
def test_explicit_language_code_wins(media):
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="fa",
        auto_enqueue=False,
    )
    assert job.language_code == "fa"


@pytest.mark.django_db
def test_platform_default_language_used_when_job_omits_language(media):
    platform = PlatformConfiguration.get_solo()
    platform.default_language = "fa"
    platform.save()
    clear_settings_cache()

    job = JobOrchestrator().create_transcription_job(
        media=media,
        auto_enqueue=False,
    )
    assert job.language_code == "fa"


@pytest.mark.django_db
def test_provider_default_language_used_when_platform_empty(media):
    SpeechProviderConfig.objects.filter(code="speechmatics").update(default_language="fa")
    clear_settings_cache()

    job = JobOrchestrator().create_transcription_job(
        media=media,
        auto_enqueue=False,
    )
    assert job.language_code == "fa"


@pytest.mark.django_db
def test_missing_language_raises_validation_error(media):
    with pytest.raises(ValidationError, match="language_code is required"):
        JobOrchestrator().create_transcription_job(
            media=media,
            auto_enqueue=False,
        )


@pytest.mark.django_db
def test_admin_bulk_path_uses_platform_default(media):
    """Mirrors Admin bulk action: no language_code argument."""
    platform = PlatformConfiguration.get_solo()
    platform.default_language = "fa"
    platform.save()
    clear_settings_cache()

    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=media.uploaded_by,
        auto_enqueue=False,
    )
    assert job.language_code == "fa"


def test_speechmatics_adapter_sends_fa_not_en():
    adapter = SpeechmaticsAdapter(client=object())  # type: ignore[arg-type]
    adapter._operating_point_default = "enhanced"
    config = adapter._build_config(
        TranscriptionRequest(
            language_code="fa",
            diarization=True,
            operating_point="enhanced",
            media_bytes=b"x",
            filename="a.wav",
        )
    )
    assert config["transcription_config"]["language"] == "fa"


def test_speechmatics_adapter_rejects_missing_language():
    adapter = SpeechmaticsAdapter(client=object())  # type: ignore[arg-type]
    adapter._operating_point_default = "enhanced"
    with pytest.raises(ConfigurationError, match="language_code"):
        adapter._build_config(
            TranscriptionRequest(
                language_code="",
                diarization=True,
                media_bytes=b"x",
            )
        )
