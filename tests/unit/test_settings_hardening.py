from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.settings.base import database_config, env_secret_key


ROOT = Path(__file__).resolve().parents[2]


def test_default_settings_module_is_local():
    assert "turing.apps.TuringConfig" in settings.INSTALLED_APPS
    assert settings.SESSION_COOKIE_SECURE is False
    assert settings.CSRF_COOKIE_SECURE is False


def test_env_secret_key_rejects_placeholders():
    os.environ.pop("DJANGO_SECRET_KEY", None)
    with pytest.raises(ImproperlyConfigured):
        env_secret_key(allow_insecure_default=False)

    os.environ["DJANGO_SECRET_KEY"] = "change-me-in-production"
    with pytest.raises(ImproperlyConfigured):
        env_secret_key(allow_insecure_default=False)

    os.environ["DJANGO_SECRET_KEY"] = "prod-ok-secret-value-1234567890"
    assert env_secret_key(allow_insecure_default=False) == "prod-ok-secret-value-1234567890"
    os.environ.pop("DJANGO_SECRET_KEY", None)


def test_database_config_sqlite_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db = database_config()
    assert db["ENGINE"].endswith("sqlite3")


def test_database_config_postgres_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@dbhost:5432/turing")
    db = database_config()
    assert db["ENGINE"].endswith("postgresql")
    assert db["NAME"] == "turing"
    assert db["USER"] == "u"
    assert db["HOST"] == "dbhost"
    assert db["PORT"] == "5432"


def test_production_settings_module_loads_in_subprocess():
    env = os.environ.copy()
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            "DJANGO_SECRET_KEY": "prod-test-secret-key-with-enough-entropy-0123456789abcdef",
            "DJANGO_DEBUG": "false",
            "DJANGO_ALLOWED_HOSTS": "turing.example.com",
            # Avoid requiring psycopg in unit tests; production still defaults to Postgres.
            "TURING_ALLOW_SQLITE_IN_PRODUCTION": "true",
            "DATABASE_URL": "sqlite:///:memory:",
            "PYTHONPATH": str(ROOT),
        }
    )
    script = (
        "import django; django.setup(); "
        "from django.conf import settings; "
        "assert settings.DEBUG is False; "
        "assert settings.SESSION_COOKIE_SECURE is True; "
        "assert settings.CSRF_COOKIE_SECURE is True; "
        "assert settings.SECURE_SSL_REDIRECT is True; "
        "assert settings.SECURE_HSTS_SECONDS >= 1; "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_production_settings_reject_sqlite_by_default_in_subprocess():
    env = os.environ.copy()
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            "DJANGO_SECRET_KEY": "prod-test-secret-key-with-enough-entropy-0123456789abcdef",
            "DJANGO_DEBUG": "false",
            "DJANGO_ALLOWED_HOSTS": "turing.example.com",
            "PYTHONPATH": str(ROOT),
        }
    )
    env.pop("DATABASE_URL", None)
    env.pop("TURING_ALLOW_SQLITE_IN_PRODUCTION", None)
    script = "import django; django.setup()"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "PostgreSQL" in (result.stderr + result.stdout)


def test_production_settings_reject_missing_secret_in_subprocess():
    env = os.environ.copy()
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            "DJANGO_DEBUG": "false",
            "DJANGO_ALLOWED_HOSTS": "turing.example.com",
            "TURING_ALLOW_SQLITE_IN_PRODUCTION": "true",
            "DATABASE_URL": "sqlite:///:memory:",
            "PYTHONPATH": str(ROOT),
        }
    )
    env.pop("DJANGO_SECRET_KEY", None)
    script = "import django; django.setup()"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in (result.stderr + result.stdout)
