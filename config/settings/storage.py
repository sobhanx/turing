"""Media storage configuration helpers (local filesystem + S3-compatible)."""

from __future__ import annotations

import os
from typing import Any


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def apply_media_storage(settings: dict[str, Any]) -> None:
    """
    Configure Django ``STORAGES['default']`` for Turing media.

    - ``TURING_STORAGE_BACKEND=local`` (default): FileSystemStorage under MEDIA_ROOT
    - ``TURING_STORAGE_BACKEND=s3``: private S3-compatible bucket via django-storages
      (AWS S3, MinIO, etc.) with querystring (signed) URLs
    """
    backend = (os.environ.get("TURING_STORAGE_BACKEND") or "local").strip().lower()
    settings["TURING_STORAGE_BACKEND"] = backend
    settings["TURING_SIGNED_URL_TTL_SECONDS"] = int(
        os.environ.get("TURING_SIGNED_URL_TTL_SECONDS", "3600")
    )

    static_backend = {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    }

    if backend == "s3":
        bucket = (
            os.environ.get("TURING_S3_BUCKET")
            or os.environ.get("AWS_STORAGE_BUCKET_NAME")
            or ""
        ).strip()
        if not bucket:
            from django.core.exceptions import ImproperlyConfigured

            raise ImproperlyConfigured(
                "TURING_STORAGE_BACKEND=s3 requires TURING_S3_BUCKET "
                "(or AWS_STORAGE_BUCKET_NAME)."
            )

        options: dict[str, Any] = {
            "bucket_name": bucket,
            "default_acl": None,  # private objects
            "querystring_auth": True,
            "querystring_expire": settings["TURING_SIGNED_URL_TTL_SECONDS"],
            "file_overwrite": False,
            "object_parameters": {"CacheControl": "max-age=86400"},
        }

        access_key = (
            os.environ.get("TURING_S3_ACCESS_KEY")
            or os.environ.get("AWS_ACCESS_KEY_ID")
            or ""
        ).strip()
        secret_key = (
            os.environ.get("TURING_S3_SECRET_KEY")
            or os.environ.get("AWS_SECRET_ACCESS_KEY")
            or ""
        ).strip()
        region = (
            os.environ.get("TURING_S3_REGION")
            or os.environ.get("AWS_S3_REGION_NAME")
            or ""
        ).strip()
        endpoint = (
            os.environ.get("TURING_S3_ENDPOINT_URL")
            or os.environ.get("AWS_S3_ENDPOINT_URL")
            or ""
        ).strip()
        custom_domain = (os.environ.get("TURING_S3_CUSTOM_DOMAIN") or "").strip()

        if access_key:
            options["access_key"] = access_key
        if secret_key:
            options["secret_key"] = secret_key
        if region:
            options["region_name"] = region
        if endpoint:
            options["endpoint_url"] = endpoint
        if custom_domain:
            options["custom_domain"] = custom_domain
        # Addressing style for MinIO / path-style endpoints
        if env_bool("TURING_S3_ADDRESSING_STYLE_PATH", default=bool(endpoint)):
            options["addressing_style"] = "path"
            options["signature_version"] = "s3v4"

        settings["STORAGES"] = {
            "default": {
                "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
                "OPTIONS": options,
            },
            "staticfiles": static_backend,
        }
        # Avoid accidental public MEDIA_URL serving assumptions in production
        settings.setdefault("AWS_QUERYSTRING_AUTH", True)
        return

    # Local filesystem (development default)
    media_root = settings.get("MEDIA_ROOT")
    media_url = settings.get("MEDIA_URL", "/media/")
    options = {}
    if media_root is not None:
        options["location"] = str(media_root)
    if media_url:
        options["base_url"] = media_url
    settings["STORAGES"] = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": options,
        },
        "staticfiles": static_backend,
    }
