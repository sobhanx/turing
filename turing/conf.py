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
    allowed_audio_extensions: str
    allowed_audio_mime_types: str
    default_max_attempts: int
    poll_interval_seconds: float
    poll_timeout_seconds: int
    poll_backoff_base_seconds: float
    poll_backoff_max_seconds: float
    max_poll_attempts: int
    retry_backoff_base_seconds: float
    retry_backoff_max_seconds: float
    storage_backend: str
    signed_url_ttl_seconds: int
    speechmatics_api_key: str
    speechmatics_base_url: str
    speechmatics_connect_timeout: float
    speechmatics_upload_timeout: float
    speechmatics_read_timeout: float
    speechmatics_webhook_secret: str
    webhook_mode: str
    webhook_base_url: str
    webhook_callback_url: str
    ai_provider: str
    openai_api_key: str
    openai_model: str
    openai_base_url: str
    normalization_enabled: bool
    max_duration_ms: int
    poll_timeout_multiplier: float
    auto_enqueue: bool
    enable_diarization_default: bool
    default_language: str
    outbound_webhook_max_retries: int
    outbound_webhook_backoff_base_seconds: float
    outbound_webhook_backoff_max_seconds: float
    outbound_webhook_timeout_seconds: float
    outbox_dispatch_enabled: bool
    outbox_dispatch_interval_seconds: float
    outbox_stuck_timeout_seconds: float
    connector_sync_enabled: bool
    connector_sync_interval_seconds: float


def _env(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _load_from_django() -> TuringSettings:
    base_interval = float(_env("TURING_POLL_INTERVAL_SECONDS", 3.0))
    return TuringSettings(
        default_provider=_env("TURING_DEFAULT_PROVIDER", "speechmatics"),
        max_upload_bytes=int(_env("TURING_MAX_UPLOAD_BYTES", 500 * 1024 * 1024)),
        allowed_audio_extensions=_env(
            "TURING_ALLOWED_AUDIO_EXTENSIONS",
            "mp3,wav,m4a,webm,ogg",
        ),
        allowed_audio_mime_types=_env("TURING_ALLOWED_AUDIO_MIME_TYPES", ""),
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
        signed_url_ttl_seconds=int(_env("TURING_SIGNED_URL_TTL_SECONDS", 3600)),
        speechmatics_api_key=_env("TURING_SPEECHMATICS_API_KEY", ""),
        speechmatics_base_url=_env(
            "TURING_SPEECHMATICS_BASE_URL",
            "https://asr.api.speechmatics.com/v2",
        ),
        speechmatics_connect_timeout=float(
            _env("TURING_SPEECHMATICS_CONNECT_TIMEOUT", 10.0)
        ),
        speechmatics_upload_timeout=float(
            _env("TURING_SPEECHMATICS_UPLOAD_TIMEOUT", 120.0)
        ),
        speechmatics_read_timeout=float(
            _env("TURING_SPEECHMATICS_READ_TIMEOUT", 60.0)
        ),
        speechmatics_webhook_secret=_env("TURING_SPEECHMATICS_WEBHOOK_SECRET", ""),
        webhook_mode=_env("TURING_WEBHOOK_MODE", "augment"),
        webhook_base_url=_env("TURING_WEBHOOK_BASE_URL", ""),
        webhook_callback_url="",
        ai_provider=_env("TURING_AI_PROVIDER", "fake"),
        openai_api_key=_env("TURING_OPENAI_API_KEY", ""),
        openai_model=_env("TURING_OPENAI_MODEL", "gpt-4o-mini"),
        openai_base_url=_env(
            "TURING_OPENAI_BASE_URL", "https://api.openai.com/v1"
        ),
        normalization_enabled=_as_bool(_env("TURING_NORMALIZATION_ENABLED", True), True),
        max_duration_ms=int(_env("TURING_MAX_DURATION_MS", 0)),
        poll_timeout_multiplier=float(_env("TURING_POLL_TIMEOUT_MULTIPLIER", 2.0)),
        auto_enqueue=True,
        enable_diarization_default=True,
        default_language="",
        outbound_webhook_max_retries=int(
            _env("TURING_OUTBOUND_WEBHOOK_MAX_RETRIES", 5)
        ),
        outbound_webhook_backoff_base_seconds=float(
            _env("TURING_OUTBOUND_WEBHOOK_BACKOFF_BASE_SECONDS", 2.0)
        ),
        outbound_webhook_backoff_max_seconds=float(
            _env("TURING_OUTBOUND_WEBHOOK_BACKOFF_MAX_SECONDS", 300.0)
        ),
        outbound_webhook_timeout_seconds=float(
            _env("TURING_OUTBOUND_WEBHOOK_TIMEOUT_SECONDS", 10.0)
        ),
        outbox_dispatch_enabled=_as_bool(
            _env("TURING_OUTBOX_DISPATCH_ENABLED", True), True
        ),
        outbox_dispatch_interval_seconds=float(
            _env("TURING_OUTBOX_DISPATCH_INTERVAL_SECONDS", 30.0)
        ),
        outbox_stuck_timeout_seconds=float(
            _env("TURING_OUTBOX_STUCK_TIMEOUT_SECONDS", 300.0)
        ),
        connector_sync_enabled=_as_bool(
            _env("TURING_CONNECTOR_SYNC_ENABLED", True), True
        ),
        connector_sync_interval_seconds=float(
            _env("TURING_CONNECTOR_SYNC_INTERVAL_SECONDS", 3600.0)
        ),
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
    webhook_base = (getattr(platform, "webhook_base_url", None) or base.webhook_base_url or "").strip()
    webhook_secret = (base.speechmatics_webhook_secret or "").strip()
    from turing.providers.speechmatics.webhook import webhook_callback_url as build_callback_url

    callback_url = build_callback_url(webhook_base) if webhook_base else ""
    webhook_mode = getattr(platform, "webhook_mode", None) or base.webhook_mode or "augment"
    return TuringSettings(
        default_provider=default_provider,
        max_upload_bytes=platform.max_upload_bytes or base.max_upload_bytes,
        allowed_audio_extensions=(
            platform.allowed_audio_extensions or base.allowed_audio_extensions
        ),
        allowed_audio_mime_types=(
            platform.allowed_audio_mime_types or base.allowed_audio_mime_types
        ),
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
        signed_url_ttl_seconds=base.signed_url_ttl_seconds,
        speechmatics_api_key=api_key,
        speechmatics_base_url=base_url,
        speechmatics_connect_timeout=base.speechmatics_connect_timeout,
        speechmatics_upload_timeout=base.speechmatics_upload_timeout,
        speechmatics_read_timeout=base.speechmatics_read_timeout,
        speechmatics_webhook_secret=webhook_secret,
        webhook_mode=webhook_mode,
        webhook_base_url=webhook_base,
        webhook_callback_url=callback_url,
        ai_provider=base.ai_provider,
        openai_api_key=base.openai_api_key,
        openai_model=base.openai_model,
        openai_base_url=base.openai_base_url,
        normalization_enabled=getattr(platform, "normalization_enabled", base.normalization_enabled),
        max_duration_ms=getattr(platform, "max_duration_ms", base.max_duration_ms) or 0,
        poll_timeout_multiplier=float(
            getattr(platform, "poll_timeout_multiplier", None) or base.poll_timeout_multiplier
        ),
        auto_enqueue=platform.auto_enqueue,
        enable_diarization_default=platform.enable_diarization_default,
        default_language=platform.default_language or base.default_language,
        outbound_webhook_max_retries=base.outbound_webhook_max_retries,
        outbound_webhook_backoff_base_seconds=base.outbound_webhook_backoff_base_seconds,
        outbound_webhook_backoff_max_seconds=base.outbound_webhook_backoff_max_seconds,
        outbound_webhook_timeout_seconds=base.outbound_webhook_timeout_seconds,
        outbox_dispatch_enabled=base.outbox_dispatch_enabled,
        outbox_dispatch_interval_seconds=base.outbox_dispatch_interval_seconds,
        outbox_stuck_timeout_seconds=base.outbox_stuck_timeout_seconds,
        connector_sync_enabled=base.connector_sync_enabled,
        connector_sync_interval_seconds=base.connector_sync_interval_seconds,
    )


def clear_settings_cache() -> None:
    _cached_settings.cache_clear()
