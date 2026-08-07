from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from turing.admin import fa as fa_labels
from turing.admin.authz import (
    AppendOnlyBrowseAdminMixin,
    CapabilityGatedAdminMixin,
    admin_assert_capability,
    admin_scope_queryset,
)
from turing.admin.persian import PersianAdminMixin, PersianInlineMixin
from turing.domain.enums import JobStatus
from turing.domain.exceptions import PermissionDeniedError
from turing.models import ProcessingAttempt, ProcessingJob, ProcessingLog
from turing.services.job_orchestrator import JobOrchestrator


class ProcessingAttemptInline(PersianInlineMixin, admin.TabularInline):
    model = ProcessingAttempt
    extra = 0
    verbose_name = fa_labels.MODEL_TITLES["ProcessingAttempt"][0]
    verbose_name_plural = fa_labels.MODEL_TITLES["ProcessingAttempt"][1]
    fields = (
        "attempt_number",
        "provider_code",
        "provider_credential_identity",
        "credential_id_display",
        "credential_name_display",
        "external_job_id",
        "status",
        "error_code",
        "error_message",
        "started_at",
        "finished_at",
    )
    readonly_fields = (
        "attempt_number",
        "provider_code",
        "provider_credential_identity",
        "credential_id_display",
        "credential_name_display",
        "external_job_id",
        "status",
        "error_code",
        "error_message",
        "started_at",
        "finished_at",
    )
    can_delete = False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("provider_credential", "provider_credential__provider")
        )

    @admin.display(description="Credential identity")
    def provider_credential_identity(self, obj: ProcessingAttempt) -> str:
        """Human-readable sticky credential (never plaintext secret)."""
        cred = getattr(obj, "provider_credential", None)
        if cred is None:
            return "— (legacy fallback)"
        provider = getattr(cred, "provider", None)
        provider_code = getattr(provider, "code", "") or "?"
        return f"{cred.name} · id={cred.pk} · {provider_code}"

    @admin.display(description="Credential id")
    def credential_id_display(self, obj: ProcessingAttempt) -> str:
        cred_id = getattr(obj, "provider_credential_id", None)
        return str(cred_id) if cred_id is not None else "—"

    @admin.display(description="Credential name")
    def credential_name_display(self, obj: ProcessingAttempt) -> str:
        cred = getattr(obj, "provider_credential", None)
        if cred is None:
            return "—"
        return cred.name


class ProcessingLogInline(PersianInlineMixin, admin.TabularInline):
    model = ProcessingLog
    extra = 0
    max_num = 25
    verbose_name = fa_labels.MODEL_TITLES["ProcessingLog"][0]
    verbose_name_plural = fa_labels.MODEL_TITLES["ProcessingLog"][1]
    readonly_fields = ("level", "message", "context", "created_at")
    can_delete = False
    ordering = ("-created_at",)
    show_change_link = True


@admin.register(ProcessingJob)
class ProcessingJobAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "view_transcript"
    turing_change_capability = "manage_jobs"
    turing_add_capability = "manage_jobs"
    turing_delete_capability = "manage_jobs"

    list_display = (
        "id",
        "status_badge",
        "capability",
        "provider_code",
        "media_link",
        "organization",
        "language_code",
        "attempt_count",
        "created_by",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "capability", "provider_code", "organization", "created_at")
    search_fields = ("id", "external_job_id", "idempotency_key", "error_code", "tenant_key")
    autocomplete_fields = ("organization",)
    list_select_related = ("media", "organization", "created_by")
    list_per_page = 50
    readonly_fields = (
        "external_job_id",
        "attempt_count",
        "error_code",
        "error_message",
        "queued_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("media", "created_by")
    inlines = [ProcessingAttemptInline, ProcessingLogInline]
    actions = ("retry_jobs", "cancel_jobs", "enqueue_jobs")

    def get_queryset(self, request):
        return admin_scope_queryset(super().get_queryset(request), request.user)

    def save_model(self, request, obj, form, change):
        if not obj.organization_id and obj.media_id:
            obj.organization = obj.media.organization
        try:
            admin_assert_capability(
                request.user,
                organization=obj.organization,
                capability="manage_jobs",
            )
        except PermissionDeniedError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return
        admin.ModelAdmin.save_model(self, request, obj, form, change)

    @admin.display(description=fa_labels.FIELD_LABELS["status"])
    def status_badge(self, obj: ProcessingJob):
        colors = {
            JobStatus.PENDING: "#6c757d",
            JobStatus.QUEUED: "#0d6efd",
            JobStatus.RUNNING: "#fd7e14",
            JobStatus.SUCCEEDED: "#198754",
            JobStatus.FAILED: "#dc3545",
            JobStatus.CANCELLED: "#6c757d",
            JobStatus.PARTIAL: "#ffc107",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="padding:2px 8px;border-radius:4px;background:{};color:#fff;">{}</span>',
            color,
            fa_labels.status_label(obj.status, obj.get_status_display()),
        )

    @admin.display(description=fa_labels.FIELD_LABELS["media"])
    def media_link(self, obj: ProcessingJob):
        return obj.media.display_name

    @admin.action(description="افزودن به صف")
    def enqueue_jobs(self, request, queryset):
        orch = JobOrchestrator()
        count = 0
        for job in queryset:
            try:
                admin_assert_capability(
                    request.user,
                    organization=job.organization,
                    capability="manage_jobs",
                )
                orch.enqueue(job)
                count += 1
            except (PermissionDeniedError, Exception) as exc:  # noqa: BLE001
                self.message_user(request, f"{job.id}: {exc}", messages.ERROR)
        self.message_user(request, f"{count} پردازش به صف اضافه شد.", messages.SUCCESS)

    @admin.action(description="تلاش مجدد پردازش‌های ناموفق")
    def retry_jobs(self, request, queryset):
        orch = JobOrchestrator()
        count = 0
        for job in queryset:
            try:
                admin_assert_capability(
                    request.user,
                    organization=job.organization,
                    capability="manage_jobs",
                )
                orch.retry(job)
                count += 1
            except (PermissionDeniedError, Exception) as exc:  # noqa: BLE001
                self.message_user(request, f"{job.id}: {exc}", messages.ERROR)
        self.message_user(request, f"{count} پردازش مجدداً تلاش شد.", messages.SUCCESS)

    @admin.action(description="لغو پردازش‌های انتخاب‌شده")
    def cancel_jobs(self, request, queryset):
        orch = JobOrchestrator()
        count = 0
        for job in queryset:
            try:
                admin_assert_capability(
                    request.user,
                    organization=job.organization,
                    capability="manage_jobs",
                )
                orch.cancel(job)
                count += 1
            except (PermissionDeniedError, Exception) as exc:  # noqa: BLE001
                self.message_user(request, f"{job.id}: {exc}", messages.ERROR)
        self.message_user(request, f"{count} پردازش لغو شد.", messages.SUCCESS)


@admin.register(ProcessingLog)
class ProcessingLogAdmin(
    PersianAdminMixin,
    AppendOnlyBrowseAdminMixin,
    CapabilityGatedAdminMixin,
    admin.ModelAdmin,
):
    """Processing reports — read-only browse of job log lines."""

    turing_view_capability = "view_transcript"
    turing_change_capability = "view_transcript"
    turing_delete_capability = "view_transcript"

    list_display = ("created_at", "job", "level", "message_short")
    list_filter = ("level", "created_at", ("job__organization", admin.RelatedOnlyFieldListFilter))
    search_fields = ("message", "job__id", "job__external_job_id")
    list_select_related = ("job", "job__organization", "job__media")
    list_per_page = 100
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = (
        "job",
        "attempt",
        "level",
        "message",
        "context",
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):
        return admin_scope_queryset(
            super().get_queryset(request).select_related(
                "job",
                "job__organization",
                "job__media",
            ),
            request.user,
            field="job__organization_id",
        )

    def turing_organization(self, obj):
        return obj.job.organization if obj and obj.job_id else None

    @admin.display(description=fa_labels.FIELD_LABELS["message"])
    def message_short(self, obj: ProcessingLog):
        return obj.message[:120]
