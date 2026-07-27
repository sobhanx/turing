"""Phase 3.1 — Provider webhook support tests."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from turing.domain.enums import JobStatus, UseCase
from turing.models import ProviderWebhookDelivery, WebhookDeliveryOutcome
from turing.providers.speechmatics.adapter import SpeechmaticsAdapter
from turing.providers.speechmatics.webhook import (
    SPEECHMATICS_PROVIDER_CODE,
    WebhookParseError,
    compute_dedupe_key,
    map_status_param,
    parse_speechmatics_notification,
)
from turing.providers.types import TranscriptionRequest
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcription import TranscriptionService
from turing.webhooks.auth import verify_speechmatics_webhook_bearer
from turing.webhooks.types import ProviderNotification


WEBHOOK_URL = "/api/turing/v1/webhooks/speechmatics/"
WEBHOOK_SECRET = "test-webhook-secret-32chars-min"


def _notification(
    *,
    external_job_id: str = "ext-sm-1",
    status_param: str = "success",
    provider_state: str = "succeeded",
    dedupe_key: str = "dedupe-1",
) -> ProviderNotification:
    return ProviderNotification(
        provider_code=SPEECHMATICS_PROVIDER_CODE,
        external_job_id=external_job_id,
        status_param=status_param,
        provider_state=provider_state,
        provider_message=status_param,
        dedupe_key=dedupe_key,
        payload_hash="abc123",
        raw_metadata={"query": {"id": external_job_id, "status": status_param}},
    )


@pytest.fixture
def media(db):
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio-bytes"),
        filename="clip.wav",
        use_case=UseCase.VOICE_FILE,
    )


@pytest.fixture
def submitted_job(media):
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    job.external_job_id = "ext-sm-1"
    job.status = JobStatus.RUNNING
    job.save(update_fields=["external_job_id", "status", "updated_at"])
    JobOrchestrator().begin_attempt(job)
    return job


@override_settings(TURING_SPEECHMATICS_WEBHOOK_SECRET=WEBHOOK_SECRET)
def test_verify_bearer_valid_and_invalid():
    from turing.conf import clear_settings_cache

    clear_settings_cache()
    assert verify_speechmatics_webhook_bearer(
        {"Authorization": f"Bearer {WEBHOOK_SECRET}"}
    )
    assert not verify_speechmatics_webhook_bearer(
        {"Authorization": "Bearer wrong-secret"}
    )
    assert not verify_speechmatics_webhook_bearer({})


def test_map_status_param_success_and_failed():
    assert map_status_param("success").state == "succeeded"
    assert map_status_param("rejected").state == "failed"
    assert map_status_param("running").state == "running"
    assert map_status_param("weird").state == "running"


@pytest.mark.django_db
@override_settings(TURING_SPEECHMATICS_WEBHOOK_SECRET=WEBHOOK_SECRET)
def test_webhook_http_valid_queues_task(submitted_job):
    from turing.conf import clear_settings_cache

    clear_settings_cache()
    client = Client()
    with patch("turing.api.webhooks.process_provider_webhook_event") as mock_task:
        response = client.post(
            f"{WEBHOOK_URL}?id=ext-sm-1&status=success",
            data=b"",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_SECRET}",
        )
    assert response.status_code == 200
    mock_task.delay.assert_called_once()
    payload = mock_task.delay.call_args[0][0]
    assert payload["external_job_id"] == "ext-sm-1"
    assert payload["status_param"] == "success"


@pytest.mark.django_db
@override_settings(TURING_SPEECHMATICS_WEBHOOK_SECRET=WEBHOOK_SECRET)
def test_webhook_http_invalid_secret_returns_403():
    from turing.conf import clear_settings_cache

    clear_settings_cache()
    client = Client()
    with patch("turing.api.webhooks.process_provider_webhook_event") as mock_task:
        response = client.post(
            f"{WEBHOOK_URL}?id=ext-sm-1&status=success",
            HTTP_AUTHORIZATION="Bearer wrong",
        )
    assert response.status_code == 403
    mock_task.delay.assert_not_called()


@pytest.mark.django_db
def test_ingest_success_schedules_fetch(submitted_job):
    notification = _notification()
    service = TranscriptionService()
    with patch(
        "turing.tasks.transcription.fetch_and_persist_transcription.delay"
    ) as mock_fetch:
        outcome = service.ingest_provider_notification(notification)
    assert outcome == WebhookDeliveryOutcome.PROCESSED
    mock_fetch.assert_called_once_with(str(submitted_job.id))
    delivery = ProviderWebhookDelivery.objects.get(dedupe_key=notification.dedupe_key)
    assert delivery.outcome == WebhookDeliveryOutcome.PROCESSED
    assert delivery.processing_job_id == submitted_job.id


@pytest.mark.django_db
def test_ingest_failed_marks_job_failed(submitted_job):
    notification = _notification(
        status_param="rejected",
        provider_state="failed",
        dedupe_key="dedupe-fail",
    )
    service = TranscriptionService()
    with patch(
        "turing.tasks.transcription.fetch_and_persist_transcription.delay"
    ) as mock_fetch:
        outcome = service.ingest_provider_notification(notification)
    assert outcome == WebhookDeliveryOutcome.PROCESSED
    mock_fetch.assert_not_called()
    submitted_job.refresh_from_db()
    assert submitted_job.status == JobStatus.FAILED
    assert submitted_job.error_code == "PROVIDER_JOB_FAILED"


@pytest.mark.django_db
def test_ingest_duplicate_delivery(submitted_job):
    notification = _notification(dedupe_key="dedupe-dup")
    service = TranscriptionService()
    assert service.ingest_provider_notification(notification) == WebhookDeliveryOutcome.PROCESSED
    with patch(
        "turing.tasks.transcription.fetch_and_persist_transcription.delay"
    ) as mock_fetch:
        outcome = service.ingest_provider_notification(notification)
    assert outcome == WebhookDeliveryOutcome.DUPLICATE
    mock_fetch.assert_not_called()
    assert (
        ProviderWebhookDelivery.objects.filter(dedupe_key="dedupe-dup").count() == 1
    )


@pytest.mark.django_db
def test_ingest_unknown_job_returns_unknown_job():
    notification = _notification(
        external_job_id="no-such-ext",
        dedupe_key="dedupe-unknown",
    )
    service = TranscriptionService()
    with patch(
        "turing.tasks.transcription.fetch_and_persist_transcription.delay"
    ) as mock_fetch:
        outcome = service.ingest_provider_notification(notification)
    assert outcome == WebhookDeliveryOutcome.UNKNOWN_JOB
    mock_fetch.assert_not_called()
    delivery = ProviderWebhookDelivery.objects.get(dedupe_key="dedupe-unknown")
    assert delivery.outcome == WebhookDeliveryOutcome.UNKNOWN_JOB
    assert delivery.processing_job_id is None


@pytest.mark.django_db
@override_settings(TURING_SPEECHMATICS_WEBHOOK_SECRET=WEBHOOK_SECRET)
def test_webhook_http_unknown_job_returns_200():
    from turing.conf import clear_settings_cache

    clear_settings_cache()
    client = Client()
    with patch(
        "turing.api.webhooks.process_provider_webhook_event.delay",
        side_effect=lambda data: TranscriptionService().ingest_provider_notification(
            ProviderNotification.from_dict(data)
        ),
    ):
        response = client.post(
            f"{WEBHOOK_URL}?id=missing-ext-id&status=success",
            HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_SECRET}",
        )
    assert response.status_code == 200
    assert ProviderWebhookDelivery.objects.filter(
        external_job_id="missing-ext-id",
        outcome=WebhookDeliveryOutcome.UNKNOWN_JOB,
    ).exists()


@pytest.mark.django_db
def test_ingest_terminal_job_ignored(submitted_job):
    submitted_job.status = JobStatus.SUCCEEDED
    submitted_job.save(update_fields=["status", "updated_at"])
    notification = _notification(dedupe_key="dedupe-terminal")
    service = TranscriptionService()
    with patch(
        "turing.tasks.transcription.fetch_and_persist_transcription.delay"
    ) as mock_fetch:
        outcome = service.ingest_provider_notification(notification)
    assert outcome == WebhookDeliveryOutcome.IGNORED
    mock_fetch.assert_not_called()
    delivery = ProviderWebhookDelivery.objects.get(dedupe_key="dedupe-terminal")
    assert delivery.outcome == WebhookDeliveryOutcome.IGNORED


@pytest.mark.django_db
@override_settings(
    TURING_WEBHOOK_MODE="augment",
    TURING_WEBHOOK_BASE_URL="https://turing.example.com",
    TURING_SPEECHMATICS_WEBHOOK_SECRET=WEBHOOK_SECRET,
)
def test_augment_mode_adds_notification_config():
    from turing.conf import clear_settings_cache

    clear_settings_cache()
    adapter = SpeechmaticsAdapter(client=MagicMock())
    config = adapter._build_config(
        TranscriptionRequest(language_code="en", media_bytes=b"x", filename="a.wav")
    )
    assert "notification_config" in config
    note = config["notification_config"][0]
    assert note["url"].startswith("https://turing.example.com/api/turing/v1/webhooks/speechmatics")
    assert any("Bearer" in h for h in note["auth_headers"])
    assert note["contents"] == ["jobinfo"]


@pytest.mark.django_db
def test_off_mode_skips_notification_config():
    from turing.conf import clear_settings_cache
    from turing.models import PlatformConfiguration

    platform = PlatformConfiguration.get_solo()
    platform.webhook_mode = "off"
    platform.webhook_base_url = "https://turing.example.com"
    platform.save()
    clear_settings_cache()

    adapter = SpeechmaticsAdapter(client=MagicMock())
    config = adapter._build_config(
        TranscriptionRequest(language_code="en", media_bytes=b"x", filename="a.wav")
    )
    assert "notification_config" not in config


def test_parse_speechmatics_notification_requires_id(rf):
    request = rf.generic("POST", "/webhooks/", "")
    with pytest.raises(WebhookParseError, match="Missing required query parameter"):
        parse_speechmatics_notification(request)


def test_compute_dedupe_key_stable():
    a = compute_dedupe_key(
        provider_code="speechmatics",
        external_job_id="j1",
        status_param="success",
        payload_hash="hash1",
    )
    b = compute_dedupe_key(
        provider_code="speechmatics",
        external_job_id="j1",
        status_param="success",
        payload_hash="hash1",
    )
    assert a == b
    assert a != compute_dedupe_key(
        provider_code="speechmatics",
        external_job_id="j1",
        status_param="failed",
        payload_hash="hash1",
    )
