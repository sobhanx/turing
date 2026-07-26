"""Local / development settings (default for manage.py and pytest)."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import env_bool, env_list, env_secret_key, database_config

SECRET_KEY = env_secret_key(allow_insecure_default=True)
DEBUG = env_bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

DATABASES = {"default": database_config()}

# Relaxed security for local HTTP development
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
