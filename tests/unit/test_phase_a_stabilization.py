from __future__ import annotations

"""Phase A stabilization coverage: SSRF, idempotency, SC authz, media ingest."""

import io
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from turing.domain.enums import TuringRole, UseCase
from turing.domain.exceptions import ValidationError
from turing.models import Organization, ProcessingJob, TuringMembership
from turing.security.urls import assert_safe_public_http_url
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService

User = get_user_model()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://localhost/hook",
        "http://10.0.0.5/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/hook",
    ],
)
def test_assert_safe_public_http_url_blocks_private(url):
    with pytest.raises(ValidationError):
        assert_safe_public_http_url(url, purpose="Webhook URL")


def test_assert_safe_public_http_url_allows_public_https():
    assert assert_safe_public_http_url(
        "https://1.1.1.1/webhook", purpose="Webhook URL"
    )


@pytest.mark.django_db
def test_job_idempotency_is_org_scoped():
    org_a = Organization.get_default()
    org_b = Organization.objects.create(name="Org B", slug="org-b-idem")
    media_a = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"a"),
        filename="a.wav",
        use_case=UseCase.GENERIC,
        organization=org_a,
    )
    media_b = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"b"),
        filename="b.wav",
        use_case=UseCase.GENERIC,
        organization=org_b,
    )
    orch = JobOrchestrator()
    job_a = orch.create_transcription_job(
        media=media_a,
        language_code="en",
        idempotency_key="same-key",
        auto_enqueue=False,
    )
    job_b = orch.create_transcription_job(
        media=media_b,
        language_code="en",
        idempotency_key="same-key",
        auto_enqueue=False,
    )
    assert job_a.id != job_b.id
    assert ProcessingJob.objects.filter(idempotency_key="same-key").count() == 2


@pytest.mark.django_db
def test_speech_center_denies_staff_without_capability(client):
    staff = User.objects.create_user(
        "sc-staff-only", password="pass", is_staff=True
    )
    client.force_login(staff)
    resp = client.get(reverse("speech_center:dashboard"))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_speech_center_allows_viewer_membership(client):
    org = Organization.get_default()
    user = User.objects.create_user(
        "sc-viewer-ok", password="pass", is_staff=True
    )
    TuringMembership.objects.create(
        user=user, organization=org, role=TuringRole.VIEWER, is_active=True
    )
    client.force_login(user)
    resp = client.get(reverse("speech_center:dashboard"))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_connector_media_download_fallback_to_url():
    from turing.connectors.media_ingest import create_media_from_connector_url

    org = Organization.get_default()
    with patch("turing.connectors.media_ingest.requests.get") as get:
        get.side_effect = RuntimeError("network down")
        asset, mode = create_media_from_connector_url(
            url="https://1.1.1.1/recording.mp4",
            organization=org,
            use_case=UseCase.MEETING,
            original_filename="rec.mp4",
            fallback_to_url=True,
        )
    assert mode == "url_fallback"
    assert asset.external_url.startswith("https://")
    assert asset.metadata.get("ingest") == "url_fallback"


@pytest.mark.django_db
def test_connector_media_download_success_uses_upload():
    from turing.connectors.media_ingest import create_media_from_connector_url

    org = Organization.get_default()
    fake_asset = MagicMock()
    fake_asset.external_url = ""
    with patch("turing.connectors.media_ingest.requests.get") as get:
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        resp.raise_for_status = MagicMock()
        resp.headers = {"Content-Type": "audio/wav"}
        resp.iter_content = MagicMock(return_value=[b"\x00" * 64])
        get.return_value = resp
        with patch.object(
            MediaService, "create_from_upload", return_value=fake_asset
        ) as upload:
            asset, mode = create_media_from_connector_url(
                url="https://1.1.1.1/recording.wav",
                organization=org,
                headers={"Authorization": "Bearer x"},
                fallback_to_url=True,
            )
    assert mode == "downloaded"
    assert asset is fake_asset
    upload.assert_called_once()


@pytest.mark.django_db
def test_webhook_subscription_rejects_private_url():
    client = APIClient()
    user = User.objects.create_superuser("wh-admin", "w@example.com", "pass")
    client.force_authenticate(user)
    org = Organization.get_default()
    resp = client.post(
        "/api/turing/v1/webhooks/",
        {
            "name": "bad",
            "url": "http://127.0.0.1/hook",
            "subscribed_events": ["*"],
            "organization_id": org.id,
        },
        format="json",
    )
    assert resp.status_code == 400
