from __future__ import annotations

from django import forms
from django.contrib import admin

from turing.admin.authz import GlobalCapabilityAdminMixin
from turing.admin.persian import PersianAdminMixin
from turing.models import TranscriptExportSettings


class TranscriptExportSettingsForm(forms.ModelForm):
    """User-friendly labels for PDF/DOCX section visibility (no schema change)."""

    class Meta:
        model = TranscriptExportSettings
        fields = (
            "show_meeting_title",
            "show_persian_date",
            "show_gregorian_date",
            "show_duration",
            "show_speakers",
            "show_full_transcript",
            "show_timeline",
            "show_ai_summary",
            "show_key_topics",
            "show_action_items",
            "show_decisions",
            "show_keywords",
            "show_provider",
        )
        labels = {
            "show_meeting_title": "Meeting title",
            "show_persian_date": "Persian (Jalali) date",
            "show_gregorian_date": "Gregorian date",
            "show_duration": "Duration",
            "show_speakers": "Speakers",
            "show_full_transcript": "Full transcript",
            "show_timeline": "Timeline timestamps",
            "show_ai_summary": "Executive Summary",
            "show_key_topics": "Key Topics",
            "show_action_items": "Action Items",
            "show_decisions": "Decisions / Key Points",
            "show_keywords": "Keywords",
            "show_provider": "Speech provider (internal)",
        }
        help_texts = {
            "show_ai_summary": (
                "Include the AI executive summary section in PDF and DOCX exports."
            ),
            "show_key_topics": (
                "Include the AI key topics list in PDF and DOCX exports."
            ),
            "show_action_items": (
                "Include AI action items in PDF and DOCX exports."
            ),
            "show_decisions": (
                "Include decisions / key points (from summary main points) "
                "in PDF and DOCX exports."
            ),
            "show_keywords": (
                "Include keyword chips (derived from topics) in PDF and DOCX exports."
            ),
            "show_provider": (
                "Include the STT provider code in meeting information. "
                "Usually leave this off for end-user documents."
            ),
        }


@admin.register(TranscriptExportSettings)
class TranscriptExportSettingsAdmin(PersianAdminMixin, GlobalCapabilityAdminMixin, admin.ModelAdmin):
    """
    Transcript Export Settings — platform-wide section visibility for PDF/DOCX.

    Organization overrides are supported by the model but not exposed here yet.
    """

    turing_capability = "manage_config"
    form = TranscriptExportSettingsForm
    list_display = (
        "scope_display",
        "show_full_transcript",
        "show_ai_summary",
        "show_key_topics",
        "show_action_items",
        "show_decisions",
        "show_keywords",
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
                    "These toggles control both PDF and DOCX transcript exports."
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
            "AI analysis sections",
            {
                "fields": (
                    "show_ai_summary",
                    "show_key_topics",
                    "show_action_items",
                    "show_decisions",
                    "show_keywords",
                ),
                "description": (
                    "Optional AI sections. When disabled, the section is omitted from "
                    "both PDF and DOCX. When enabled, content is taken from existing "
                    "Transcript Analysis rows (never regenerated on export)."
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
