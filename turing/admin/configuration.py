from __future__ import annotations

from django.contrib import admin

from turing.models import PlatformConfiguration, SpeechProviderConfig


@admin.register(PlatformConfiguration)
class PlatformConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "default_provider_code",
        "storage_backend",
        "auto_enqueue",
        "default_max_attempts",
        "enable_diarization_default",
        "updated_at",
    )
    fieldsets = (
        (
            "Provider defaults",
            {
                "fields": (
                    "default_provider_code",
                    "default_language",
                    "enable_diarization_default",
                )
            },
        ),
        (
            "Processing",
            {
                "fields": (
                    "auto_enqueue",
                    "default_max_attempts",
                    "poll_interval_seconds",
                    "poll_timeout_seconds",
                    "max_upload_bytes",
                    "storage_backend",
                )
            },
        ),
        (
            "API",
            {"fields": ("api_require_auth", "api_page_size")},
        ),
        ("Notes", {"fields": ("notes",)}),
    )

    def has_add_permission(self, request) -> bool:
        return not PlatformConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(SpeechProviderConfig)
class SpeechProviderConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "priority", "operating_point", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("priority", "code")
    fieldsets = (
        (None, {"fields": ("code", "name", "is_active", "priority")}),
        (
            "Credentials",
            {
                "fields": ("api_key", "base_url"),
                "description": "Leave API key blank to use TURING_SPEECHMATICS_API_KEY from the environment.",
            },
        ),
        (
            "Defaults",
            {
                "fields": (
                    "default_language",
                    "operating_point",
                    "enable_diarization",
                    "extra_options",
                )
            },
        ),
    )
