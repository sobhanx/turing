from __future__ import annotations

from django.contrib import admin

from turing.admin.authz import CapabilityGatedAdminMixin, admin_scope_queryset
from turing.admin.persian import PersianAdminMixin
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
    """Kept for tests / programmatic AdminSite use; hidden from default Admin UI."""

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
class RecordingAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "view_transcript"
    turing_change_capability = "manage_jobs"
    turing_add_capability = "manage_jobs"
    turing_delete_capability = "manage_jobs"

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
    list_filter = ("provider", "status", ("organization", admin.RelatedOnlyFieldListFilter))
    search_fields = ("external_id", "meeting__title", "meeting__external_id", "media__original_filename")
    list_select_related = ("organization", "meeting", "media")
    list_per_page = 50
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("organization", "meeting", "media")

    def get_queryset(self, request):
        return admin_scope_queryset(
            super().get_queryset(request).select_related("organization", "meeting", "media"),
            request.user,
            field="organization_id",
        )
