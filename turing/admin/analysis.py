from __future__ import annotations

from django.contrib import admin

from turing.admin.authz import CapabilityGatedAdminMixin, admin_scope_queryset
from turing.models import TranscriptAnalysis


@admin.register(TranscriptAnalysis)
class TranscriptAnalysisAdmin(CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "view_transcript"
    turing_change_capability = "view_transcript"
    turing_add_capability = "view_transcript"
    turing_delete_capability = "view_transcript"

    list_display = (
        "id",
        "transcript",
        "analysis_type",
        "provider",
        "model_name",
        "organization",
        "created_at",
    )
    list_filter = ("analysis_type", "provider", "organization")
    search_fields = ("id", "transcript__id")
    readonly_fields = (
        "transcript",
        "organization",
        "analysis_type",
        "content",
        "provider",
        "model_name",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return admin_scope_queryset(qs, request, field="organization_id")
