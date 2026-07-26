"""Typed access to Turing settings with Admin DB overrides."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from django.conf import settings


@dataclass(frozen=True)
class TuringSettings:
    default_provider: str
    max_upload_bytes: int
    default_max_attempts: int
    poll_interval_seconds: float
    poll_timeout_seconds: int
    poll_backoff_base_seconds: float
    poll_backoff_max_seconds: float
    max_poll_attempts: int
    retry_backoff_base_seconds: float
    retry_backoff_max_seconds: float
    storage_backend: str
    speechmatics_api_key: str
    speechmatics_base_url: str
    auto_enqueue: bool
    enable_diarization_default: bool
    default_language: str


def _env(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _load_from_django() -> TuringSettings:
    base_interval = float(_env("TURING_POLL_INTERVAL_SECONDS", 3.0))
    return TuringSettings(
        default_provider=_env("TURING_DEFAULT_PROVIDER", "speechmatics"),
        max_upload_bytes=int(_env("TURING_MAX_UPLOAD_BYTES", 500 * 1024 * 1024)),
        default_max_attempts=int(_env("TURING_DEFAULT_MAX_ATTEMPTS", 3)),
        poll_interval_seconds=base_interval,
        poll_timeout_seconds=int(_env("TURING_POLL_TIMEOUT_SECONDS", 1800)),
        poll_backoff_base_seconds=float(
            _env("TURING_POLL_BACKOFF_BASE_SECONDS", base_interval)
        ),
        poll_backoff_max_seconds=float(_env("TURING_POLL_BACKOFF_MAX_SECONDS", 60.0)),
        max_poll_attempts=int(_env("TURING_MAX_POLL_ATTEMPTS", 0)),
        retry_backoff_base_seconds=float(_env("TURING_RETRY_BACKOFF_BASE_SECONDS", 5.0)),
        retry_backoff_max_seconds=float(_env("TURING_RETRY_BACKOFF_MAX_SECONDS", 300.0)),
        storage_backend=_env("TURING_STORAGE_BACKEND", "local"),
        speechmatics_api_key=_env("TURING_SPEECHMATICS_API_KEY", ""),
        speechmatics_base_url=_env(
            "TURING_SPEECHMATICS_BASE_URL",
            "https://asr.api.speechmatics.com/v2",
        ),
        auto_enqueue=True,
        enable_diarization_default=True,
        default_language="",
    )


def get_turing_settings(*, refresh: bool = False) -> TuringSettings:
    """
    Resolve runtime settings.

    Provider API key priority:
    1. SpeechProviderConfig.api_key (DB, decrypted)
    2. TURING_SPEECHMATICS_API_KEY / Django settings (environment)
    3. Empty — callers (Speechmatics client) raise a clear configuration error
    """
    if refresh:
        _cached_settings.cache_clear()
    return _cached_settings()


@lru_cache(maxsize=1)
def _cached_settings() -> TuringSettings:
    base = _load_from_django()
    try:
        from turing.models.configuration import PlatformConfiguration, SpeechProviderConfig
    except Exception:
        return base

    try:
        platform = PlatformConfiguration.get_solo()
    except Exception:
        return base

    api_key = base.speechmatics_api_key
    base_url = base.speechmatics_base_url
    default_provider = platform.default_provider_code or base.default_provider

    try:
        provider = SpeechProviderConfig.objects.filter(
            code=default_provider,
            is_active=True,
        ).first()
        if provider:
            db_key = (provider.api_key or "").strip()
            if db_key:
                api_key = db_key
            if provider.base_url:
                base_url = provider.base_url
    except Exception:
        pass

    poll_interval = float(platform.poll_interval_seconds or base.poll_interval_seconds)
    return TuringSettings(
        default_provider=default_provider,
        max_upload_bytes=platform.max_upload_bytes or base.max_upload_bytes,
        default_max_attempts=platform.default_max_attempts or base.default_max_attempts,
        poll_interval_seconds=poll_interval,
        poll_timeout_seconds=platform.poll_timeout_seconds or base.poll_timeout_seconds,
        poll_backoff_base_seconds=base.poll_backoff_base_seconds
        if base.poll_backoff_base_seconds
        else poll_interval,
        poll_backoff_max_seconds=base.poll_backoff_max_seconds,
        max_poll_attempts=base.max_poll_attempts,
        retry_backoff_base_seconds=base.retry_backoff_base_seconds,
        retry_backoff_max_seconds=base.retry_backoff_max_seconds,
        storage_backend=platform.storage_backend or base.storage_backend,
        speechmatics_api_key=api_key,
        speechmatics_base_url=base_url,
        auto_enqueue=platform.auto_enqueue,
        enable_diarization_default=platform.enable_diarization_default,
        default_language=platform.default_language or base.default_language,
    )


def clear_settings_cache() -> None:
    _cached_settings.cache_clear()
