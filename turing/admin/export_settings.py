from __future__ import annotations

from django.contrib import admin

from turing.admin.authz import GlobalCapabilityAdminMixin
from turing.admin.persian import PersianAdminMixin
from turing.models import TranscriptExportSettings


@admin.register(TranscriptExportSettings)
class TranscriptExportSettingsAdmin(PersianAdminMixin, GlobalCapabilityAdminMixin, admin.ModelAdmin):
    """
    Transcript Export Settings — platform-wide section visibility for PDF/DOCX.

    Organization overrides are supported by the model but not exposed here yet.
    """

    turing_capability = "manage_config"
    list_display = (
        "scope_display",
        "show_meeting_title",
        "show_full_transcript",
        "show_ai_summary",
        "show_provider",
        "updated_at",
    )
    list_filter = ("is_global",)
    readonly_fields = ("is_global", "organization", "created_at", "updated_at")
    fieldsets = (
        (
            "Scope",
            {
                "fields": ("organization", "is_global"),
                "description": (
                    "Platform-wide settings apply to all organizations. "
                    "Organization-level overrides can be added later without "
                    "changing exporters."
                ),
            },
        ),
        (
            "Document metadata",
            {
                "fields": (
                    "show_meeting_title",
                    "show_persian_date",
                    "show_gregorian_date",
                    "show_duration",
                    "show_speakers",
                ),
            },
        ),
        (
            "Transcript",
            {
                "fields": (
                    "show_full_transcript",
                    "show_timeline",
                ),
            },
        ),
        (
            "AI sections",
            {
                "fields": (
                    "show_ai_summary",
                    "show_key_topics",
                    "show_action_items",
                    "show_decisions",
                    "show_keywords",
                ),
            },
        ),
        (
            "Technical",
            {"fields": ("show_provider",)},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at")},
        ),
    )

    @admin.display(description="Scope")
    def scope_display(self, obj: TranscriptExportSettings) -> str:
        if obj.organization_id:
            return f"Organization: {obj.organization}"
        return "Platform (global)"

    def get_queryset(self, request):
        # Admin UI focuses on global settings for now.
        return super().get_queryset(request).filter(organization__isnull=True)

    def has_add_permission(self, request) -> bool:
        if TranscriptExportSettings.objects.filter(organization__isnull=True).exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def save_model(self, request, obj, form, change):
        obj.organization = None
        obj.is_global = True
        super().save_model(request, obj, form, change)
