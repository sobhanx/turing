from __future__ import annotations

import io

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from turing.admin.configuration import SpeechProviderConfigAdmin, SpeechProviderConfigForm
from turing.conf import clear_settings_cache, get_turing_settings
from turing.domain.exceptions import ConfigurationError
from turing.models import SpeechProviderConfig
from turing.providers.speechmatics.adapter import SpeechmaticsAdapter
from turing.providers.speechmatics.client import SpeechmaticsClient, SpeechmaticsTimeouts
from turing.security.secrets import ENCRYPTED_PREFIX, is_encrypted, mask_secret


User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.django_db
def test_api_key_stored_encrypted_not_plaintext():
    row = SpeechProviderConfig.objects.create(
        code="speechmatics-secure",
        name="Speechmatics Secure",
        api_key="super-secret-key-xyz",
        is_active=True,
    )
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT api_key FROM turing_speechproviderconfig WHERE id = %s",
            [row.pk],
        )
        raw = cursor.fetchone()[0]
    assert is_encrypted(raw)
    assert ENCRYPTED_PREFIX in raw
    assert "super-secret-key-xyz" not in raw

    row.refresh_from_db()
    assert row.api_key == "super-secret-key-xyz"
    assert row.api_key_masked == "********-xyz"


@pytest.mark.django_db
def test_legacy_plaintext_reads_and_reencrypts_on_save():
    row = SpeechProviderConfig.objects.create(
        code="legacy",
        name="Legacy",
        is_active=True,
    )
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE turing_speechproviderconfig SET api_key = %s WHERE id = %s",
            ["plain-legacy-key", row.pk],
        )
    row.refresh_from_db()
    assert row.api_key == "plain-legacy-key"

    row.name = "Legacy Updated"
    row.save()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT api_key FROM turing_speechproviderconfig WHERE id = %s",
            [row.pk],
        )
        raw = cursor.fetchone()[0]
    assert is_encrypted(raw)
    row.refresh_from_db()
    assert row.api_key == "plain-legacy-key"


@pytest.mark.django_db
def test_admin_form_does_not_expose_plaintext():
    row, _ = SpeechProviderConfig.objects.update_or_create(
        code="speechmatics",
        defaults={
            "name": "Speechmatics",
            "api_key": "admin-secret-key-9999",
            "is_active": True,
            "priority": 10,
            "base_url": "https://asr.api.speechmatics.com/v2",
        },
    )
    form = SpeechProviderConfigForm(instance=row)
    assert form.fields["api_key"].initial == ""
    form = SpeechProviderConfigForm(
        data={
            "code": row.code,
            "name": row.name,
            "is_active": True,
            "priority": 10,
            "api_key": "",
            "base_url": "https://asr.api.speechmatics.com/v2",
            "default_language": "fa",
            "operating_point": "enhanced",
            "enable_diarization": True,
            "extra_options": "{}",
        },
        instance=row,
    )
    assert form.is_valid(), form.errors
    admin = SpeechProviderConfigAdmin(SpeechProviderConfig, AdminSite())
    request = RequestFactory().post("/admin/")
    request.user = User.objects.create_superuser("admin", "a@b.com", "pass")
    obj = form.save(commit=False)
    admin.save_model(request, obj, form, change=True)
    row.refresh_from_db()
    assert row.api_key == "admin-secret-key-9999"
    assert mask_secret(row.api_key) == "********9999"


@pytest.mark.django_db
def test_db_secret_preferred_over_env(settings):
    settings.TURING_SPEECHMATICS_API_KEY = "env-key-should-lose"
    SpeechProviderConfig.objects.filter(code="speechmatics").delete()
    SpeechProviderConfig.objects.create(
        code="speechmatics",
        name="Speechmatics",
        api_key="db-key-should-win",
        is_active=True,
        priority=1,
    )
    clear_settings_cache()
    resolved = get_turing_settings(refresh=True)
    assert resolved.speechmatics_api_key == "db-key-should-win"


@pytest.mark.django_db
def test_env_fallback_when_db_key_empty(settings):
    settings.TURING_SPEECHMATICS_API_KEY = "env-only-key"
    SpeechProviderConfig.objects.filter(code="speechmatics").update(api_key="")
    clear_settings_cache()
    resolved = get_turing_settings(refresh=True)
    assert resolved.speechmatics_api_key == "env-only-key"


@pytest.mark.django_db
def test_adapter_uses_decrypted_db_key(monkeypatch):
    SpeechProviderConfig.objects.filter(code="speechmatics").delete()
    SpeechProviderConfig.objects.create(
        code="speechmatics",
        name="Speechmatics",
        api_key="adapter-db-secret",
        is_active=True,
        base_url="https://asr.api.speechmatics.com/v2",
    )
    clear_settings_cache()
    captured: dict[str, str] = {}

    class CapturingClient(SpeechmaticsClient):
        def __init__(
            self,
            *,
            api_key: str,
            base_url: str = "",
            connect_timeout: float = 10.0,
            upload_timeout: float = 120.0,
            read_timeout: float = 60.0,
            timeout: int | float | None = None,
            **kwargs,
        ):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            captured["connect_timeout"] = connect_timeout
            captured["upload_timeout"] = upload_timeout
            if not api_key:
                raise ConfigurationError("missing")
            self.api_key = api_key
            self.base_url = base_url
            self.timeouts = SpeechmaticsTimeouts(
                connect=connect_timeout,
                upload=upload_timeout,
                read=read_timeout if timeout is None else float(timeout),
            )
            self.timeout = self.timeouts.read
            self.session = None  # type: ignore[assignment]

    monkeypatch.setattr(
        "turing.providers.speechmatics.adapter.SpeechmaticsClient",
        CapturingClient,
    )
    adapter = SpeechmaticsAdapter()
    client = adapter._get_client()
    assert client.api_key == "adapter-db-secret"
    assert captured["api_key"] == "adapter-db-secret"


def test_missing_credentials_produce_clear_error():
    with pytest.raises(ConfigurationError, match="API key"):
        SpeechmaticsClient(api_key="")


def test_mask_secret_format():
    assert mask_secret("abcdefghij") == "********ghij"
    assert mask_secret("") == "(not set)"
    assert mask_secret("ab") == "********"
