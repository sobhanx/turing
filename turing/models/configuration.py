from __future__ import annotations

from django.core.cache import cache
from django.db import models, transaction

from turing.domain.enums import StorageBackend
from turing.models.media import TimeStampedModel


class PlatformConfiguration(TimeStampedModel):
    """
    Singleton platform settings managed from Django Admin.

    Host projects configure Turing without editing package source.
    Environment variables remain the fallback / bootstrap source.
    """

    singleton_enforcer = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)

    default_provider_code = models.CharField(
        max_length=64,
        default="speechmatics",
        help_text="Provider code used when a job does not specify one.",
    )
    storage_backend = models.CharField(
        max_length=16,
        choices=StorageBackend.choices,
        default=StorageBackend.LOCAL,
    )
    max_upload_bytes = models.BigIntegerField(
        default=500 * 1024 * 1024,
        help_text="Maximum upload size in bytes.",
    )
    default_max_attempts = models.PositiveSmallIntegerField(default=3)
    poll_interval_seconds = models.FloatField(default=3.0)
    poll_timeout_seconds = models.PositiveIntegerField(default=1800)
    auto_enqueue = models.BooleanField(
        default=True,
        help_text=(
            "Automatically schedule the async Celery pipeline "
            "(submit → poll → fetch/persist) when a job is created."
        ),
    )
    enable_diarization_default = models.BooleanField(default=True)
    default_language = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text=(
            "Default STT language for new jobs (e.g. fa, en). "
            "Required for Admin bulk job creation when language is not set explicitly."
        ),
    )
    api_require_auth = models.BooleanField(
        default=True,
        help_text="Require authentication for Turing REST API.",
    )
    api_page_size = models.PositiveSmallIntegerField(default=25)
    notes = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Platform configuration"
        verbose_name_plural = "Platform configuration"

    def __str__(self) -> str:
        return "Turing platform configuration"

    def save(self, *args, **kwargs):
        self.singleton_enforcer = 1
        super().save(*args, **kwargs)
        from turing.conf import clear_settings_cache

        clear_settings_cache()
        cache.delete("turing:platform_configuration")

    @classmethod
    def get_solo(cls) -> PlatformConfiguration:
        with transaction.atomic():
            obj, _ = cls.objects.get_or_create(singleton_enforcer=1)
            return obj


class SpeechProviderConfig(TimeStampedModel):
    """
    Provider credentials and defaults managed in Admin.

    Phase 1 ships Speechmatics only; additional providers reuse this model.
    """

    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True, db_index=True)
    priority = models.PositiveSmallIntegerField(
        default=100,
        help_text="Lower values are preferred when selecting a default.",
    )
    api_key = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Leave blank to fall back to environment / Django settings.",
    )
    base_url = models.URLField(max_length=512, blank=True, default="")
    default_language = models.CharField(max_length=16, blank=True, default="")
    operating_point = models.CharField(
        max_length=32,
        blank=True,
        default="enhanced",
        help_text="Speechmatics operating point (standard|enhanced).",
    )
    enable_diarization = models.BooleanField(default=True)
    extra_options = models.JSONField(
        default=dict,
        blank=True,
        help_text="Provider-specific options merged into job requests.",
    )

    class Meta:
        ordering = ["priority", "code"]
        verbose_name = "Speech provider config"
        verbose_name_plural = "Speech provider configs"

    def __str__(self) -> str:
        state = "active" if self.is_active else "inactive"
        return f"{self.name} ({self.code}, {state})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from turing.conf import clear_settings_cache

        clear_settings_cache()
