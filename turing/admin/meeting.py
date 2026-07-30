from __future__ import annotations

from django.contrib import admin

from turing.models import Meeting, Recording


class RecordingInline(admin.TabularInline):
    model = Recording
    extra = 0
    fields = (
        "provider",
        "external_id",
        "status",
        "duration_ms",
        "media",
        "created_at",
    )
    readonly_fields = ("created_at",)
    show_change_link = True
    raw_id_fields = ("media",)


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "provider",
        "external_id",
        "status",
        "organization",
        "started_at",
        "created_at",
    )
    list_filter = ("provider", "status", "organization")
    search_fields = ("title", "external_id", "host_external_id")
    list_select_related = ("organization", "connector_installation")
    list_per_page = 50
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("organization", "connector_installation")
    inlines = (RecordingInline,)


@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "provider",
        "status",
        "meeting",
        "organization",
        "media",
        "duration_ms",
        "created_at",
    )
    list_filter = ("provider", "status", "organization")
    search_fields = ("external_id", "meeting__title", "meeting__external_id")
    list_select_related = ("organization", "meeting", "media")
    list_per_page = 50
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("organization", "meeting", "media")
