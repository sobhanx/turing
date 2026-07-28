from __future__ import annotations

from django.contrib import admin

from turing.admin.authz import CapabilityGatedAdminMixin, admin_scope_queryset
from turing.admin.persian import PersianAdminMixin
from turing.models import ExternalReference


@admin.register(ExternalReference)
class ExternalReferenceAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    """Read-oriented Admin for host object links (Phase 4.1.5)."""

    turing_view_capability = "view_transcript"
    turing_change_capability = "view_transcript"
    turing_add_capability = "view_transcript"
    turing_delete_capability = "view_transcript"

    list_display = (
        "id",
        "organization",
        "external_system",
        "external_type",
        "external_id",
        "target_kind",
        "media",
        "transcript",
        "created_at",
    )
    list_filter = ("external_system", "external_type", "organization", "created_at")
    search_fields = (
        "id",
        "external_system",
        "external_type",
        "external_id",
        "media__id",
        "transcript__id",
    )
    readonly_fields = (
        "organization",
        "external_system",
        "external_type",
        "external_id",
        "media",
        "transcript",
        "metadata",
        "created_at",
        "updated_at",
    )
    autocomplete_fields = ("organization",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            "organization",
            "media",
            "transcript",
        )
        return admin_scope_queryset(qs, request.user, field="organization_id")
