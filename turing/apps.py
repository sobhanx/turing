from __future__ import annotations

from django.apps import AppConfig


class TuringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "turing"
    verbose_name = "Turing Speech Intelligence"

    def ready(self) -> None:
        from turing.providers.registry import ProviderRegistry
        from turing.providers.speechmatics.adapter import SpeechmaticsAdapter

        ProviderRegistry.register(SpeechmaticsAdapter)

        # Ensure default roles exist after migrate (idempotent signal hook).
        from turing.auth import signals  # noqa: F401
