from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from turing.admin.authz import CapabilityGatedAdminMixin, admin_scope_queryset
from turing.models import ConnectorInstallation, ConnectorSyncJob
from turing.models.connector import redact_connector_config


@admin.register(ConnectorInstallation)
class ConnectorInstallationAdmin(CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "manage_config"
    turing_change_capability = "manage_config"
    turing_add_capability = "manage_config"
    turing_delete_capability = "manage_config"

    list_display = (
        "name",
        "connector_type",
        "organization",
        "status",
        "last_sync_display",
        "created_at",
    )
    list_filter = ("status", "connector_type", "organization", "created_at")
    search_fields = ("name", "connector_type", "organization__name", "organization__slug")
    readonly_fields = ("config_public", "created_at", "updated_at")
    autocomplete_fields = ("organization",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "organization",
                    "connector_type",
                    "name",
                    "status",
                )
            },
        ),
        (
            "Configuration",
            {
                "fields": ("config_public", "config"),
                "description": (
                    "Secrets in config are masked in the public view. "
                    "Prefer storing credentials via encrypted secrets later."
                ),
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Config (redacted)")
    def config_public(self, obj: ConnectorInstallation) -> str:
        if not obj or not obj.pk:
            return "(none)"
        return str(redact_connector_config(obj.config))

    @admin.display(description="Last sync")
    def last_sync_display(self, obj: ConnectorInstallation) -> str:
        job = obj.sync_jobs.order_by("-created_at").first()
        if job is None:
            return "—"
        when = job.finished_at or job.started_at or job.created_at
        return format_html("{} · {}", job.status, when)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("organization")
        return admin_scope_queryset(qs, request.user, field="organization_id")


@admin.register(ConnectorSyncJob)
class ConnectorSyncJobAdmin(CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "manage_config"
    turing_change_capability = "manage_config"
    turing_add_capability = "manage_config"
    turing_delete_capability = "manage_config"

    list_display = (
        "id",
        "installation",
        "connector_type",
        "organization",
        "status",
        "records_processed",
        "started_at",
        "finished_at",
        "created_at",
    )
    list_filter = ("status", "installation__connector_type", "created_at")
    search_fields = (
        "id",
        "installation__name",
        "installation__connector_type",
        "error",
    )
    readonly_fields = (
        "installation",
        "status",
        "started_at",
        "finished_at",
        "records_processed",
        "error",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Type", ordering="installation__connector_type")
    def connector_type(self, obj: ConnectorSyncJob) -> str:
        return obj.installation.connector_type if obj.installation_id else ""

    @admin.display(description="Organization", ordering="installation__organization")
    def organization(self, obj: ConnectorSyncJob):
        return obj.installation.organization if obj.installation_id else None

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            "installation",
            "installation__organization",
        )
        return admin_scope_queryset(
            qs,
            request.user,
            field="installation__organization_id",
        )
