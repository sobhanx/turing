"""Production settings — require env secrets and enable hardened security."""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env_bool, env_list, env_secret_key, database_config

SECRET_KEY = env_secret_key(allow_insecure_default=False)
DEBUG = env_bool("DJANGO_DEBUG", default=False)

if DEBUG:
    raise ImproperlyConfigured(
        "DJANGO_DEBUG must be false when using config.settings.production."
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must be set for production "
        "(comma-separated hostnames)."
    )

DATABASES = {"default": database_config()}
engine = DATABASES["default"].get("ENGINE", "")
if engine.endswith("sqlite3") and not env_bool("TURING_ALLOW_SQLITE_IN_PRODUCTION", False):
    raise ImproperlyConfigured(
        "Production settings require PostgreSQL via DATABASE_URL "
        "(postgres://...). Set TURING_ALLOW_SQLITE_IN_PRODUCTION=true only for "
        "controlled exceptions."
    )

# HTTPS / cookie hardening
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", default=True)
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", default=True)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = os.environ.get(
    "SECURE_REFERRER_POLICY",
    "same-origin",
)
X_FRAME_OPTIONS = "DENY"

# Trust TLS terminated at a reverse proxy / load balancer
if env_bool("TURING_BEHIND_PROXY", default=True):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Prefer failing closed if Speechmatics key missing in process env
# (Admin DB override may still supply it at runtime).
if not (os.environ.get("TURING_SPEECHMATICS_API_KEY") or "").strip():
    # Soft warning path: do not crash boot — Admin may hold the key.
    # Operators should still set the env var in production.
    pass

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "turing": {
            "handlers": ["console"],
            "level": os.environ.get("TURING_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
