from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import responses

from turing.connectors import (
    ConnectorCategory,
    ConnectorConfigurationError,
    ConnectorRegistry,
    ConnectorSyncError,
)
from turing.connectors.builtins import register_builtin_connectors
from turing.connectors.exceptions import AuthenticationError
from turing.connectors.twilio.client import TwilioClient
from turing.connectors.twilio.connector import TwilioConnector
from turing.connectors.twilio.serializers import (
    normalize_twilio_recording,
    pick_primary_recording,
    recording_media_url,
)
from turing.domain.enums import ConnectorInstallationStatus, UseCase
from turing.domain.events import DomainEvent, EventName
from turing.events.bus import EventBus
from turing.models import (
    ConnectorInstallation,
    ExternalReference,
    MediaAsset,
    Organization,
)
from turing.services.connector_sync import ConnectorSyncService

ACCOUNT_SID = "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AUTH_TOKEN = "twilio-auth-secret-token"
API = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/"


@pytest.fixture(autouse=True)
def _twilio_registry(settings):
    settings.TURING_TWILIO_ACCOUNT_SID = ACCOUNT_SID
    settings.TURING_TWILIO_AUTH_TOKEN = AUTH_TOKEN
    settings.TURING_TWILIO_API_BASE = "https://api.twilio.com"
    ConnectorRegistry.clear()
    register_builtin_connectors()
    EventBus.clear()
    yield
    ConnectorRegistry.clear()
    register_builtin_connectors()
    EventBus.clear()


@pytest.fixture
def org(db):
    return Organization.get_default()


def _installation(org, **kwargs) -> ConnectorInstallation:
    defaults = {
        "organization": org,
        "connector_type": "twilio",
        "name": "Company Twilio",
        "status": ConnectorInstallationStatus.ACTIVE,
        "config": {},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


def _recording(
    *,
    sid: str = "REaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    call_sid: str = "CAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    duration: str = "42",
) -> dict[str, Any]:
    return {
        "sid": sid,
        "call_sid": call_sid,
        "duration": duration,
        "status": "completed",
        "media_url": (
            f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/"
            f"Recordings/{sid}"
        ),
        "date_created": "Thu, 01 Jan 2026 10:00:00 +0000",
        "channels": 1,
        "source": "StartCallRecordingAPI",
    }


def _call(
    *,
    sid: str = "CAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
) -> dict[str, Any]:
    return {
        "sid": sid,
        "from": "+15551110000",
        "to": "+15552220000",
        "status": "completed",
        "direction": "outbound-api",
        "start_time": "Thu, 01 Jan 2026 09:59:00 +0000",
        "duration": "42",
    }


def test_normalize_twilio_recording():
    call = normalize_twilio_recording(
        _recording(),
        account_sid=ACCOUNT_SID,
        call=_call(),
    )
    assert call is not None
    assert call.external_system == "twilio"
    assert call.external_type == "call"
    assert call.external_id.startswith("CA")
    assert call.recording_url.endswith(".mp3")
    assert call.caller == "+15551110000"
    assert call.callee == "+15552220000"
    assert call.duration == 42
    assert call.metadata["recording_sid"].startswith("RE")
    assert "auth" not in str(call.to_public_dict()).lower() or "auth_token" not in str(
        call.to_public_dict()
    ).lower()

    assert normalize_twilio_recording({"sid": "RE1"}, account_sid=ACCOUNT_SID) is None
    primary = pick_primary_recording(
        [
            _recording(sid="REshort", duration="5"),
            _recording(sid="RElong", duration="99"),
        ]
    )
    assert primary is not None
    assert primary["sid"] == "RElong"
    assert recording_media_url(
        account_sid=ACCOUNT_SID, recording_sid="REx"
    ).endswith("/Recordings/REx.mp3")


@pytest.mark.django_db
def test_credentials_validation(org, settings):
    installation = _installation(org)
    connector = TwilioConnector(installation)
    connector.validate_config()

    settings.TURING_TWILIO_ACCOUNT_SID = ""
    settings.TURING_TWILIO_AUTH_TOKEN = ""
    bare = _installation(org, name="No Creds", config={})
    with pytest.raises(ConnectorConfigurationError, match="Account SID"):
        TwilioConnector(bare).validate_config()

    with_config = _installation(
        org,
        name="Config Creds",
        config={"account_sid": ACCOUNT_SID, "auth_token": AUTH_TOKEN},
    )
    TwilioConnector(with_config).validate_config()


@responses.activate
def test_client_list_and_auth():
    responses.add(
        responses.GET,
        f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}.json",
        json={
            "sid": ACCOUNT_SID,
            "friendly_name": "Demo",
            "status": "active",
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{API}Recordings.json",
        json={"recordings": [_recording()]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{API}Calls/CAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json",
        json=_call(),
        status=200,
    )
    client = TwilioClient(account_sid=ACCOUNT_SID, auth_token=AUTH_TOKEN)
    health = client.health_check()
    assert health["ok"] is True
    assert health["friendly_name"] == "Demo"
    assert AUTH_TOKEN not in str(health)

    calls = client.list_calls_with_recordings()
    assert len(calls) == 1
    assert calls[0].external_id.startswith("CA")
    assert AUTH_TOKEN not in str(calls[0].to_public_dict())

    responses.add(
        responses.GET,
        f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}.json",
        json={"code": 20003, "message": "Authenticate"},
        status=401,
    )
    with pytest.raises(AuthenticationError, match="401"):
        client.authenticate()


@pytest.mark.django_db(transaction=True)
def test_sync_creates_media_and_external_refs(org, monkeypatch):
    installation = _installation(org)
    client = MagicMock()
    call = normalize_twilio_recording(
        _recording(), account_sid=ACCOUNT_SID, call=_call()
    )
    client.list_calls_with_recordings.return_value = [call]
    client.authenticate.return_value = {"sid": ACCOUNT_SID}

    use_cases: list[str] = []
    real_create = __import__(
        "turing.services.media", fromlist=["MediaService"]
    ).MediaService.create_from_url

    def _create_from_url(self, **kwargs):
        use_cases.append(kwargs["use_case"])
        assert AUTH_TOKEN not in str(kwargs)
        return real_create(self, **kwargs)

    monkeypatch.setattr(
        "turing.services.media.MediaService.create_from_url",
        _create_from_url,
    )

    result = TwilioConnector(installation, client=client).sync()
    assert result.records_processed == 1
    assert use_cases == [UseCase.CRM_CALL]

    ref = ExternalReference.objects.get(
        organization=org,
        external_system="twilio",
        external_type="call",
        external_id="CAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert MediaAsset.objects.filter(id=ref.media_id).exists()
    assert ref.metadata["caller"] == "+15551110000"

    result2 = TwilioConnector(installation, client=client).sync()
    assert result2.records_processed == 0
    assert result2.details["skipped"] == 1


@pytest.mark.django_db(transaction=True)
def test_sync_via_service_emits_events(org, settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    seen: list[DomainEvent] = []
    EventBus.subscribe("*", seen.append)
    installation = _installation(org, name="Twilio Events")
    client = MagicMock()
    client.authenticate.return_value = {"sid": ACCOUNT_SID}
    client.list_calls_with_recordings.return_value = [
        normalize_twilio_recording(_recording(), account_sid=ACCOUNT_SID, call=_call())
    ]
    original = ConnectorRegistry.create

    def _create(inst):
        if inst.connector_type == "twilio":
            return TwilioConnector(inst, client=client)
        return original(inst)

    monkeypatch.setattr(ConnectorRegistry, "create", staticmethod(_create))
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=True)
    job.refresh_from_db()
    assert job.status == "completed"
    names = [e.name for e in seen]
    assert EventName.CONNECTOR_SYNC_STARTED in names
    assert EventName.CONNECTOR_SYNC_COMPLETED in names
    assert EventName.MEDIA_CREATED in names
    assert AUTH_TOKEN not in str([e.payload for e in seen])


@pytest.mark.django_db
def test_sync_failure(org, monkeypatch):
    installation = _installation(org, name="Twilio Fail")
    client = MagicMock()
    client.authenticate.return_value = {"sid": ACCOUNT_SID}
    client.list_calls_with_recordings.return_value = [
        normalize_twilio_recording(_recording(), account_sid=ACCOUNT_SID, call=_call())
    ]

    def _boom(*args, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr("turing.services.media.MediaService.create_from_url", _boom)
    with pytest.raises(ConnectorSyncError, match="storage down"):
        TwilioConnector(installation, client=client).sync()


@pytest.mark.django_db
def test_get_recording(org):
    installation = _installation(org, name="Twilio Get")
    client = MagicMock()
    expected = normalize_twilio_recording(
        _recording(), account_sid=ACCOUNT_SID, call=_call()
    )
    client.get_recording_for_call.return_value = expected
    connector = TwilioConnector(installation, client=client)
    got = connector.get_recording("CAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert got is not None
    assert got.external_system == "twilio"
    client.get_recording_for_call.assert_called_once()


@pytest.mark.django_db
def test_registry_and_definition():
    assert "twilio" in ConnectorRegistry.types()
    definition = ConnectorRegistry.get_definition("twilio")
    assert definition.provider == "Twilio"
    assert definition.category == ConnectorCategory.TELEPHONY
    assert list(definition.supported_sync_types) == ["calls"]
    catalog = definition.to_catalog_dict()
    assert catalog["capabilities"]["oauth"] is False
    assert AUTH_TOKEN not in str(catalog)
    assert ConnectorRegistry.get("twilio") is TwilioConnector
