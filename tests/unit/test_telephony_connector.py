from __future__ import annotations

from typing import Any

import pytest

from turing.connectors import (
    ConnectorCategory,
    ConnectorConfigurationError,
    ConnectorRegistry,
    ConnectorSyncError,
    TelephonyCall,
    TelephonyConnector,
    normalize_call,
)
from turing.connectors.builtins import register_builtin_connectors
from turing.connectors.telephony.serializers import normalize_calls
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


class FakeTelephonyConnector(TelephonyConnector):
    """Concrete CTI-style adapter for unit tests (not a shipped provider)."""

    connector_type = "telephony-fake"
    display_name = "Fake Telephony"
    provider = "Turing Test"
    description = "Test telephony adapter for call recording ingest."

    def __init__(self, installation, *, calls: list[TelephonyCall] | None = None):
        super().__init__(installation)
        self._calls = list(calls or [])

    def validate_config(self) -> None:
        if not self.config.get("api_token"):
            raise ConnectorConfigurationError("api_token is required.")

    def list_calls(self, **kwargs: Any) -> list[TelephonyCall]:
        self.validate_config()
        return list(self._calls)

    def get_recording(self, call_id: str) -> TelephonyCall | None:
        self.validate_config()
        for call in self._calls:
            if call.external_id == call_id:
                return call
        return None


@pytest.fixture(autouse=True)
def _registry():
    ConnectorRegistry.clear()
    ConnectorRegistry.register(FakeTelephonyConnector)
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
        "connector_type": "telephony-fake",
        "name": "Contact Center",
        "status": ConnectorInstallationStatus.ACTIVE,
        "config": {"api_token": "tel-secret-token"},
    }
    defaults.update(kwargs)
    return ConnectorInstallation.objects.create(**defaults)


def _sample_calls() -> list[TelephonyCall]:
    return [
        TelephonyCall(
            external_system="telephony",
            external_type="call",
            external_id="call-100",
            recording_url="https://cdn.example/calls/100.mp3",
            caller="+15551110000",
            callee="+15552220000",
            started_at="2026-01-01T10:00:00Z",
            duration=120,
            metadata={"queue": "support"},
        ),
        TelephonyCall(
            external_system="telephony",
            external_type="call",
            external_id="call-200",
            recording_url="https://cdn.example/calls/200.mp3",
            caller="+15553330000",
            callee="+15554440000",
            started_at="2026-01-02T11:00:00Z",
            duration=45,
            metadata={},
        ),
    ]


def test_normalize_call():
    call = normalize_call(
        {
            "id": "c1",
            "recording_url": "https://cdn.example/c1.wav",
            "caller": "A",
            "callee": "B",
            "started_at": "2026-01-01T00:00:00Z",
            "duration": "90",
            "direction": "inbound",
            "api_token": "should-not-appear",
            "metadata": {"agent_id": "ag-1", "access_token": "nope"},
        }
    )
    assert call is not None
    assert call.external_system == "telephony"
    assert call.external_type == "call"
    assert call.external_id == "c1"
    assert call.recording_url.endswith(".wav")
    assert call.caller == "A"
    assert call.callee == "B"
    assert call.duration == 90
    assert call.metadata["direction"] == "inbound"
    assert call.metadata["agent_id"] == "ag-1"
    assert "api_token" not in call.metadata
    assert "access_token" not in call.metadata
    assert normalize_call({"id": "x"}) is None
    assert len(normalize_calls([{"call_id": "z", "media_url": "https://x/z.mp3"}])) == 1


def test_registry_discovery():
    assert "telephony-fake" in ConnectorRegistry.types()
    definition = ConnectorRegistry.get_definition("telephony-fake")
    assert definition.category == ConnectorCategory.TELEPHONY
    assert definition.supported_sync_types == ("calls",)
    assert definition.capabilities["oauth"] is False
    catalog = definition.to_catalog_dict()
    assert catalog["category"] == "telephony"
    assert catalog["supported_sync_types"] == ["calls"]
    assert "api_token" in [
        f["key"] for f in catalog["installation_requirements"]["config_fields"]
    ]
    assert catalog["installation_requirements"]["config_fields"][0]["secret"] is True
    assert "tel-secret-token" not in str(catalog)
    assert ConnectorRegistry.get("telephony-fake") is FakeTelephonyConnector


@pytest.mark.django_db(transaction=True)
def test_sync_creates_media_and_external_refs(org, monkeypatch):
    installation = _installation(org)
    connector = FakeTelephonyConnector(installation, calls=_sample_calls())

    use_cases: list[str] = []
    real_create = __import__(
        "turing.services.media", fromlist=["MediaService"]
    ).MediaService.create_from_url

    def _create_from_url(self, **kwargs):
        use_cases.append(kwargs["use_case"])
        assert "tel-secret-token" not in str(kwargs)
        return real_create(self, **kwargs)

    monkeypatch.setattr(
        "turing.services.media.MediaService.create_from_url",
        _create_from_url,
    )

    result = connector.sync()
    assert result.records_processed == 2
    assert use_cases == [UseCase.CRM_CALL, UseCase.CRM_CALL]

    ref = ExternalReference.objects.get(
        organization=org,
        external_system="telephony",
        external_type="call",
        external_id="call-100",
    )
    assert MediaAsset.objects.filter(id=ref.media_id).exists()
    assert ref.metadata["caller"] == "+15551110000"

    result2 = connector.sync()
    assert result2.records_processed == 0
    assert result2.details["skipped"] == 2


@pytest.mark.django_db(transaction=True)
def test_sync_via_service_emits_events(org, settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    seen: list[DomainEvent] = []
    EventBus.subscribe("*", seen.append)
    installation = _installation(org, name="Tel Events")
    calls = _sample_calls()[:1]
    original = ConnectorRegistry.create

    def _create(inst):
        if inst.connector_type == "telephony-fake":
            return FakeTelephonyConnector(inst, calls=calls)
        return original(inst)

    monkeypatch.setattr(ConnectorRegistry, "create", staticmethod(_create))
    job = ConnectorSyncService().start_sync(installation, auto_enqueue=True)
    job.refresh_from_db()
    assert job.status == "completed"
    names = [e.name for e in seen]
    assert EventName.CONNECTOR_SYNC_STARTED in names
    assert EventName.CONNECTOR_SYNC_COMPLETED in names
    assert EventName.MEDIA_CREATED in names
    assert "tel-secret-token" not in str([e.payload for e in seen])


@pytest.mark.django_db
def test_sync_failure(org, monkeypatch):
    installation = _installation(org, name="Tel Fail")
    connector = FakeTelephonyConnector(installation, calls=_sample_calls()[:1])

    def _boom(*args, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr("turing.services.media.MediaService.create_from_url", _boom)
    with pytest.raises(ConnectorSyncError, match="storage down"):
        connector.sync()


@pytest.mark.django_db
def test_get_recording_and_list(org):
    installation = _installation(org, name="Tel Get")
    connector = FakeTelephonyConnector(installation, calls=_sample_calls())
    assert connector.get_recording("call-100") is not None
    assert connector.get_recording("missing") is None
    assert len(connector.list_calls()) == 2
    normalized = connector.normalize_call(
        {"call_id": "n1", "recording_url": "https://cdn.example/n1.mp3"}
    )
    assert normalized is not None
    assert normalized.external_system == "telephony"


@pytest.mark.django_db
def test_base_definition_metadata():
    definition = TelephonyConnector.definition()
    assert definition.category == ConnectorCategory.TELEPHONY
    assert list(definition.supported_sync_types) == ["calls"]
    assert definition.connector_type == "telephony"
    assert definition.display_name == "Telephony"
