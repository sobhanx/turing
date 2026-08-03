"""Shared Django settings for the Turing demo project."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------

INSECURE_SECRET_KEY = "dev-insecure-turing-secret-key"


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [part.strip() for part in raw.split(",") if part.strip()]


def env_secret_key(*, allow_insecure_default: bool) -> str:
    key = (os.environ.get("DJANGO_SECRET_KEY") or "").strip()
    if key and key not in {INSECURE_SECRET_KEY, "change-me-in-production"}:
        return key
    if allow_insecure_default:
        return key or INSECURE_SECRET_KEY
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a strong secret in production "
        "(not empty, not the development placeholder)."
    )


def database_config() -> dict:
    """
    Resolve database settings.

    Priority:
    1. DATABASE_URL (postgres:// or postgresql:// or sqlite:///...)
    2. SQLite file at BASE_DIR/db.sqlite3 (local default)
    """
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }

    if database_url.startswith("sqlite:///"):
        path = database_url.removeprefix("sqlite:///")
        name = Path(path) if path != ":memory:" else path
        if path not in {":memory:"} and not Path(path).is_absolute():
            name = BASE_DIR / path
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": name}

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            f"Unsupported DATABASE_URL scheme '{parsed.scheme}'. "
            "Use postgres://, postgresql://, or sqlite:///."
        )

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": (parsed.path or "/").lstrip("/") or "turing",
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
    }


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "turing.apps.TuringConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "turing.ui.speech_center.i18n.SessionLanguageMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

import turing as _turing_pkg

_TURING_PKG_DIR = Path(_turing_pkg.__file__).resolve().parent

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Prefer packaged templates; keep optional host override dir if present.
        "DIRS": [
            d
            for d in (_TURING_PKG_DIR / "templates", BASE_DIR / "templates")
            if d.is_dir()
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.i18n",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", "English"),
    ("fa", "Persian"),
]
LOCALE_PATHS = [
    d
    for d in (_TURING_PKG_DIR / "locale", BASE_DIR / "locale")
    if d.is_dir()
]
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("TURING_MEDIA_ROOT") or (BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Auth redirects — Speech Center / Admin share Django session auth.
# LOGIN_URL must be admin login (there is no /accounts/login/ in this project).
LOGIN_URL = "admin:login"
LOGIN_REDIRECT_URL = "speech_center:dashboard"
LOGOUT_REDIRECT_URL = "admin:login"

# Safety net only. Transcript admin no longer inlines segments/words; primary
# protection against TooManyFieldsSent is the lightweight change form.
# Default Django value is 1000 — raise modestly for speaker + revision formsets.
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(
    os.environ.get("DATA_UPLOAD_MAX_NUMBER_FIELDS", "2000")
)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "turing.api.pagination.StandardPagination",
    "PAGE_SIZE": 25,
}

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 55 * 60
# Prefer fair scheduling so one long Speechmatics call does not starve newer jobs
# on the same worker (tasks remain isolated per job_id).
CELERY_WORKER_PREFETCH_MULTIPLIER = int(
    os.environ.get("CELERY_WORKER_PREFETCH_MULTIPLIER", "1")
)
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_DEFAULT_QUEUE = "turing.default"
CELERY_TASK_ROUTES = {
    "turing.tasks.transcription.*": {"queue": "turing.default"},
    "turing.tasks.webhooks.*": {"queue": "turing.default"},
    "turing.tasks.analysis.*": {"queue": "turing.default"},
    "turing.tasks.ingestion.*": {"queue": "turing.default"},
    "turing.tasks.events.*": {"queue": "turing.default"},
    "turing.tasks.connectors.*": {"queue": "turing.default"},
    "turing.tasks.export.*": {"queue": "turing.export"},
}

# Outbox / outbound webhook reliability (Phase 4.2.3)
# Set TURING_OUTBOX_DISPATCH_ENABLED=false to omit Beat entries (manual dispatch only).
TURING_OUTBOX_DISPATCH_ENABLED = os.environ.get(
    "TURING_OUTBOX_DISPATCH_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}
TURING_OUTBOX_DISPATCH_INTERVAL_SECONDS = float(
    os.environ.get("TURING_OUTBOX_DISPATCH_INTERVAL_SECONDS", "30")
)
TURING_OUTBOX_STUCK_TIMEOUT_SECONDS = float(
    os.environ.get("TURING_OUTBOX_STUCK_TIMEOUT_SECONDS", "300")
)
# Connector periodic sync (Phase 4.3.4) — independent of outbox Beat entries.
TURING_CONNECTOR_SYNC_ENABLED = os.environ.get(
    "TURING_CONNECTOR_SYNC_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}
TURING_CONNECTOR_SYNC_INTERVAL_SECONDS = float(
    os.environ.get("TURING_CONNECTOR_SYNC_INTERVAL_SECONDS", "3600")
)

# Zoom OAuth (Phase 4.3.6)
TURING_ZOOM_CLIENT_ID = os.environ.get("TURING_ZOOM_CLIENT_ID", "")
TURING_ZOOM_CLIENT_SECRET = os.environ.get("TURING_ZOOM_CLIENT_SECRET", "")
TURING_ZOOM_OAUTH_REDIRECT_URI = os.environ.get("TURING_ZOOM_OAUTH_REDIRECT_URI", "")
TURING_ZOOM_OAUTH_AUTHORIZE_URL = os.environ.get(
    "TURING_ZOOM_OAUTH_AUTHORIZE_URL", "https://zoom.us/oauth/authorize"
)
TURING_ZOOM_OAUTH_TOKEN_URL = os.environ.get(
    "TURING_ZOOM_OAUTH_TOKEN_URL", "https://zoom.us/oauth/token"
)
TURING_ZOOM_OAUTH_REVOKE_URL = os.environ.get(
    "TURING_ZOOM_OAUTH_REVOKE_URL", "https://zoom.us/oauth/revoke"
)
TURING_ZOOM_OAUTH_SCOPES = os.environ.get(
    "TURING_ZOOM_OAUTH_SCOPES",
    "recording:read user:read:user",
)

# Microsoft Teams OAuth (Phase 4.3.8)
TURING_TEAMS_CLIENT_ID = os.environ.get("TURING_TEAMS_CLIENT_ID", "")
TURING_TEAMS_CLIENT_SECRET = os.environ.get("TURING_TEAMS_CLIENT_SECRET", "")
TURING_TEAMS_OAUTH_REDIRECT_URI = os.environ.get("TURING_TEAMS_OAUTH_REDIRECT_URI", "")
TURING_TEAMS_OAUTH_AUTHORIZE_URL = os.environ.get(
    "TURING_TEAMS_OAUTH_AUTHORIZE_URL",
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
)
TURING_TEAMS_OAUTH_TOKEN_URL = os.environ.get(
    "TURING_TEAMS_OAUTH_TOKEN_URL",
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
)
TURING_TEAMS_OAUTH_REVOKE_URL = os.environ.get(
    "TURING_TEAMS_OAUTH_REVOKE_URL",
    "https://graph.microsoft.com/v1.0/me/revokeSignInSessions",
)
TURING_TEAMS_OAUTH_SCOPES = os.environ.get(
    "TURING_TEAMS_OAUTH_SCOPES",
    "openid offline_access User.Read OnlineMeetings.Read OnlineMeetingRecording.Read.All",
)

# Google Meet OAuth (Phase 4.3.9)
TURING_GOOGLE_MEET_CLIENT_ID = os.environ.get("TURING_GOOGLE_MEET_CLIENT_ID", "")
TURING_GOOGLE_MEET_CLIENT_SECRET = os.environ.get(
    "TURING_GOOGLE_MEET_CLIENT_SECRET", ""
)
TURING_GOOGLE_MEET_OAUTH_REDIRECT_URI = os.environ.get(
    "TURING_GOOGLE_MEET_OAUTH_REDIRECT_URI", ""
)
TURING_GOOGLE_MEET_OAUTH_AUTHORIZE_URL = os.environ.get(
    "TURING_GOOGLE_MEET_OAUTH_AUTHORIZE_URL",
    "https://accounts.google.com/o/oauth2/v2/auth",
)
TURING_GOOGLE_MEET_OAUTH_TOKEN_URL = os.environ.get(
    "TURING_GOOGLE_MEET_OAUTH_TOKEN_URL",
    "https://oauth2.googleapis.com/token",
)
TURING_GOOGLE_MEET_OAUTH_REVOKE_URL = os.environ.get(
    "TURING_GOOGLE_MEET_OAUTH_REVOKE_URL",
    "https://oauth2.googleapis.com/revoke",
)
TURING_GOOGLE_MEET_OAUTH_SCOPES = os.environ.get(
    "TURING_GOOGLE_MEET_OAUTH_SCOPES",
    "openid email profile https://www.googleapis.com/auth/drive.readonly",
)

# Salesforce OAuth (Phase 4.3.10)
TURING_SALESFORCE_CLIENT_ID = os.environ.get("TURING_SALESFORCE_CLIENT_ID", "")
TURING_SALESFORCE_CLIENT_SECRET = os.environ.get(
    "TURING_SALESFORCE_CLIENT_SECRET", ""
)
TURING_SALESFORCE_OAUTH_REDIRECT_URI = os.environ.get(
    "TURING_SALESFORCE_OAUTH_REDIRECT_URI", ""
)
TURING_SALESFORCE_OAUTH_AUTHORIZE_URL = os.environ.get(
    "TURING_SALESFORCE_OAUTH_AUTHORIZE_URL",
    "https://login.salesforce.com/services/oauth2/authorize",
)
TURING_SALESFORCE_OAUTH_TOKEN_URL = os.environ.get(
    "TURING_SALESFORCE_OAUTH_TOKEN_URL",
    "https://login.salesforce.com/services/oauth2/token",
)
TURING_SALESFORCE_OAUTH_REVOKE_URL = os.environ.get(
    "TURING_SALESFORCE_OAUTH_REVOKE_URL",
    "https://login.salesforce.com/services/oauth2/revoke",
)
TURING_SALESFORCE_OAUTH_SCOPES = os.environ.get(
    "TURING_SALESFORCE_OAUTH_SCOPES",
    "api refresh_token offline_access",
)

# Twilio telephony (Phase 4.4.4) — Account SID + Auth Token (never log)
TURING_TWILIO_ACCOUNT_SID = os.environ.get("TURING_TWILIO_ACCOUNT_SID", "")
TURING_TWILIO_AUTH_TOKEN = os.environ.get("TURING_TWILIO_AUTH_TOKEN", "")
TURING_TWILIO_API_BASE = os.environ.get(
    "TURING_TWILIO_API_BASE",
    "https://api.twilio.com",
)

from turing.celery_schedule import build_celery_beat_schedule  # noqa: E402

CELERY_BEAT_SCHEDULE = build_celery_beat_schedule()

# ---------------------------------------------------------------------------
# Turing package (env defaults; Admin overrides at runtime)
# ---------------------------------------------------------------------------

TURING_DEFAULT_PROVIDER = os.environ.get("TURING_DEFAULT_PROVIDER", "speechmatics")
TURING_SPEECHMATICS_API_KEY = os.environ.get("TURING_SPEECHMATICS_API_KEY", "")
TURING_SPEECHMATICS_BASE_URL = os.environ.get(
    "TURING_SPEECHMATICS_BASE_URL",
    "https://asr.api.speechmatics.com/v2",
)
TURING_SPEECHMATICS_CONNECT_TIMEOUT = float(
    os.environ.get("TURING_SPEECHMATICS_CONNECT_TIMEOUT", "10")
)
TURING_SPEECHMATICS_UPLOAD_TIMEOUT = float(
    os.environ.get("TURING_SPEECHMATICS_UPLOAD_TIMEOUT", "120")
)
TURING_SPEECHMATICS_READ_TIMEOUT = float(
    os.environ.get("TURING_SPEECHMATICS_READ_TIMEOUT", "60")
)
TURING_MAX_UPLOAD_BYTES = int(os.environ.get("TURING_MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))
TURING_DEFAULT_MAX_ATTEMPTS = int(os.environ.get("TURING_DEFAULT_MAX_ATTEMPTS", "3"))
TURING_POLL_INTERVAL_SECONDS = float(os.environ.get("TURING_POLL_INTERVAL_SECONDS", "3"))
TURING_POLL_TIMEOUT_SECONDS = int(os.environ.get("TURING_POLL_TIMEOUT_SECONDS", "1800"))
TURING_POLL_BACKOFF_BASE_SECONDS = float(
    os.environ.get("TURING_POLL_BACKOFF_BASE_SECONDS", str(TURING_POLL_INTERVAL_SECONDS))
)
TURING_POLL_BACKOFF_MAX_SECONDS = float(
    os.environ.get("TURING_POLL_BACKOFF_MAX_SECONDS", "60")
)
TURING_MAX_POLL_ATTEMPTS = int(os.environ.get("TURING_MAX_POLL_ATTEMPTS", "0"))
TURING_RETRY_BACKOFF_BASE_SECONDS = float(
    os.environ.get("TURING_RETRY_BACKOFF_BASE_SECONDS", "5")
)
TURING_RETRY_BACKOFF_MAX_SECONDS = float(
    os.environ.get("TURING_RETRY_BACKOFF_MAX_SECONDS", "300")
)
TURING_STORAGE_BACKEND = os.environ.get("TURING_STORAGE_BACKEND", "local")
TURING_ALLOWED_AUDIO_EXTENSIONS = os.environ.get(
    "TURING_ALLOWED_AUDIO_EXTENSIONS",
    "mp3,wav,m4a,webm,ogg",
)
TURING_ALLOWED_AUDIO_MIME_TYPES = os.environ.get("TURING_ALLOWED_AUDIO_MIME_TYPES", "")
TURING_WEBHOOK_MODE = os.environ.get("TURING_WEBHOOK_MODE", "augment")
TURING_WEBHOOK_BASE_URL = os.environ.get("TURING_WEBHOOK_BASE_URL", "")
TURING_SPEECHMATICS_WEBHOOK_SECRET = os.environ.get("TURING_SPEECHMATICS_WEBHOOK_SECRET", "")
TURING_AI_PROVIDER = os.environ.get("TURING_AI_PROVIDER", "fake")
TURING_OPENAI_API_KEY = os.environ.get("TURING_OPENAI_API_KEY", "")
TURING_OPENAI_MODEL = os.environ.get("TURING_OPENAI_MODEL", "gpt-4o-mini")
TURING_OPENAI_BASE_URL = os.environ.get(
    "TURING_OPENAI_BASE_URL", "https://api.openai.com/v1"
)
# RAG LLM (Phase 4.5.6 / 4.5.7) — null keeps the pipeline testable without vendors.
TURING_LLM_PROVIDER = os.environ.get("TURING_LLM_PROVIDER", "null")
TURING_LLM_MODEL = os.environ.get(
    "TURING_LLM_MODEL",
    os.environ.get("TURING_OPENAI_MODEL", "gpt-4o-mini"),
)
TURING_NORMALIZATION_ENABLED = os.environ.get("TURING_NORMALIZATION_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}
TURING_MAX_DURATION_MS = int(os.environ.get("TURING_MAX_DURATION_MS", "0"))
TURING_POLL_TIMEOUT_MULTIPLIER = float(os.environ.get("TURING_POLL_TIMEOUT_MULTIPLIER", "2.0"))
TURING_OUTBOUND_WEBHOOK_MAX_RETRIES = int(
    os.environ.get("TURING_OUTBOUND_WEBHOOK_MAX_RETRIES", "5")
)
TURING_OUTBOUND_WEBHOOK_BACKOFF_BASE_SECONDS = float(
    os.environ.get("TURING_OUTBOUND_WEBHOOK_BACKOFF_BASE_SECONDS", "2")
)
TURING_OUTBOUND_WEBHOOK_BACKOFF_MAX_SECONDS = float(
    os.environ.get("TURING_OUTBOUND_WEBHOOK_BACKOFF_MAX_SECONDS", "300")
)
TURING_OUTBOUND_WEBHOOK_TIMEOUT_SECONDS = float(
    os.environ.get("TURING_OUTBOUND_WEBHOOK_TIMEOUT_SECONDS", "10")
)

# Semantic search (Phase 4.5.4) — pgvector is the default production provider.
# Set TURING_SEARCH_PROVIDER=null to disable ranking (Embedding rows only).
TURING_SEARCH_PROVIDER = os.environ.get("TURING_SEARCH_PROVIDER", "pgvector")
TURING_SEARCH_EMBEDDING_DIMS = int(os.environ.get("TURING_SEARCH_EMBEDDING_DIMS", "256"))
# Optional Postgres SQL distance via CREATE EXTENSION vector (off by default;
# Python cosine over Embedding.vector works on SQLite and Postgres).
TURING_SEARCH_PGVECTOR_SQL = os.environ.get(
    "TURING_SEARCH_PGVECTOR_SQL", "false"
).lower() in {"1", "true", "yes"}

# Embedding providers (Phase 4.5.5) — local neural default; unknown → null.
TURING_EMBEDDING_PROVIDER = os.environ.get("TURING_EMBEDDING_PROVIDER", "local")
TURING_EMBEDDING_MODEL = os.environ.get(
    "TURING_EMBEDDING_MODEL", "turing-local-v1"
)

# Media storage (local by default; set TURING_STORAGE_BACKEND=s3 in production)
from config.settings.storage import apply_media_storage  # noqa: E402

apply_media_storage(globals())
