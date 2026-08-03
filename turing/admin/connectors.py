from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from turing.admin.authz import CapabilityGatedAdminMixin, admin_scope_queryset
from turing.admin.persian import PersianAdminMixin
from turing.models import ConnectorCredential, ConnectorInstallation, ConnectorSyncJob
from turing.models.connector import redact_connector_config


@admin.register(ConnectorInstallation)
class ConnectorInstallationAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
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
    readonly_fields = ("config_public", "credential_summary", "created_at", "updated_at")
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
            "پیکربندی",
            {
                "fields": ("config_public", "config", "credential_summary"),
                "description": (
                    "اسرار موجود در پیکربندی در نمای عمومی ماسک می‌شوند. "
                    "توکن‌های OAuth به‌صورت رمزنگاری‌شده در اعتبارنامه اتصال ذخیره می‌شوند "
                    "و اینجا نمایش داده نمی‌شوند."
                ),
            },
        ),
        ("زمان‌ها", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="پیکربندی (ماسک‌شده)")
    def config_public(self, obj: ConnectorInstallation) -> str:
        if not obj or not obj.pk:
            return "(none)"
        return str(redact_connector_config(obj.config))

    @admin.display(description="اعتبارنامه")
    def credential_summary(self, obj: ConnectorInstallation) -> str:
        if not obj or not obj.pk:
            return "(none)"
        try:
            cred = obj.credential
        except ConnectorCredential.DoesNotExist:
            return "(none)"
        parts = [
            cred.auth_type,
            "access=yes" if cred.has_access_token() else "access=no",
            "refresh=yes" if cred.has_refresh_token() else "refresh=no",
        ]
        if cred.expires_at:
            parts.append(f"expires={cred.expires_at.isoformat()}")
        return ", ".join(parts)

    @admin.display(description="آخرین همگام‌سازی")
    def last_sync_display(self, obj: ConnectorInstallation) -> str:
        job = obj.sync_jobs.order_by("-created_at").first()
        if job is None:
            return "—"
        when = job.finished_at or job.started_at or job.created_at
        return format_html(
            '<span class="ltr" style="direction:ltr;unicode-bidi:isolate;">{} · {}</span>',
            job.status,
            when,
        )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("organization")
        return admin_scope_queryset(qs, request.user, field="organization_id")


@admin.register(ConnectorCredential)
class ConnectorCredentialAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    """Read-only credential metadata — never display decrypted tokens."""

    turing_view_capability = "manage_config"
    turing_change_capability = "manage_config"
    turing_add_capability = "manage_config"
    turing_delete_capability = "manage_config"

    list_display = (
        "id",
        "organization",
        "connector_installation",
        "auth_type",
        "has_access",
        "has_refresh",
        "expires_at",
        "last_refreshed_at",
        "revoked_at",
        "updated_at",
    )
    list_filter = ("auth_type", ("organization", admin.RelatedOnlyFieldListFilter), "expires_at")
    search_fields = (
        "id",
        "connector_installation__name",
        "connector_installation__connector_type",
        "organization__name",
    )
    list_select_related = ("organization", "connector_installation")
    list_per_page = 50
    date_hierarchy = "updated_at"
    ordering = ("-updated_at",)
    readonly_fields = (
        "organization",
        "connector_installation",
        "auth_type",
        "has_access",
        "has_refresh",
        "expires_at",
        "last_refreshed_at",
        "revoked_at",
        "metadata",
        "created_at",
        "updated_at",
    )
    exclude = ("encrypted_access_token", "encrypted_refresh_token")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(boolean=True, description="توکن دسترسی")
    def has_access(self, obj: ConnectorCredential) -> bool:
        return obj.has_access_token()

    @admin.display(boolean=True, description="توکن تازه‌سازی")
    def has_refresh(self, obj: ConnectorCredential) -> bool:
        return obj.has_refresh_token()

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            "organization",
            "connector_installation",
        )
        return admin_scope_queryset(qs, request.user, field="organization_id")


@admin.register(ConnectorSyncJob)
class ConnectorSyncJobAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
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

    @admin.display(description="نوع", ordering="installation__connector_type")
    def connector_type(self, obj: ConnectorSyncJob) -> str:
        return obj.installation.connector_type if obj.installation_id else ""

    @admin.display(description="سازمان", ordering="installation__organization")
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
