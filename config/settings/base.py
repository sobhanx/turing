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
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
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

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("TURING_MEDIA_ROOT") or (BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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

# Media storage (local by default; set TURING_STORAGE_BACKEND=s3 in production)
from config.settings.storage import apply_media_storage  # noqa: E402

apply_media_storage(globals())
