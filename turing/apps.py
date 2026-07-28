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

        from turing.ai.providers import fake as _ai_fake  # noqa: F401
        from turing.ai.providers import openai as _ai_openai  # noqa: F401
        from turing.ai.registry import register_builtin_llm_providers

        register_builtin_llm_providers()

        # Ensure default roles exist after migrate (idempotent signal hook).
        from turing.auth import signals  # noqa: F401

        # Outbound webhook extension point (no HTTP handlers registered yet).
        from turing.events.outbound import register_outbound_handlers

        register_outbound_handlers()

        from turing.connectors.builtins import register_builtin_connectors

        register_builtin_connectors()

        from turing.search import register_builtin_search_providers
        from turing.search.embeddings import register_builtin_embedding_providers
        from turing.search.handlers import register_search_handlers

        register_builtin_embedding_providers()
        register_builtin_search_providers()
        register_search_handlers()
