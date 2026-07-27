from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from turing.admin.authz import (
    CapabilityGatedAdminMixin,
    admin_assert_capability,
    admin_scope_queryset,
)
from turing.domain.enums import JobStatus
from turing.domain.exceptions import PermissionDeniedError
from turing.models import ProcessingAttempt, ProcessingJob, ProcessingLog
from turing.services.job_orchestrator import JobOrchestrator


class ProcessingAttemptInline(admin.TabularInline):
    model = ProcessingAttempt
    extra = 0
    readonly_fields = (
        "attempt_number",
        "provider_code",
        "external_job_id",
        "status",
        "error_code",
        "error_message",
        "started_at",
        "finished_at",
    )
    can_delete = False


class ProcessingLogInline(admin.TabularInline):
    model = ProcessingLog
    extra = 0
    readonly_fields = ("level", "message", "context", "created_at")
    can_delete = False
    ordering = ("-created_at",)


@admin.register(ProcessingJob)
class ProcessingJobAdmin(CapabilityGatedAdminMixin, admin.ModelAdmin):
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

    @admin.display(description="Status")
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
            obj.get_status_display(),
        )

    @admin.display(description="Media")
    def media_link(self, obj: ProcessingJob):
        return obj.media.display_name

    @admin.action(description="Enqueue selected jobs")
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
        self.message_user(request, f"Enqueued {count} job(s).", messages.SUCCESS)

    @admin.action(description="Retry failed jobs")
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
        self.message_user(request, f"Retried {count} job(s).", messages.SUCCESS)

    @admin.action(description="Cancel selected jobs")
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
        self.message_user(request, f"Cancelled {count} job(s).", messages.SUCCESS)


@admin.register(ProcessingLog)
class ProcessingLogAdmin(CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "view_transcript"
    turing_change_capability = "manage_jobs"
    turing_delete_capability = "manage_jobs"

    list_display = ("created_at", "job", "level", "message_short")
    list_filter = ("level", "created_at")
    search_fields = ("message", "job__id")
    readonly_fields = ("job", "attempt", "level", "message", "context", "created_at", "updated_at")

    def get_queryset(self, request):
        return admin_scope_queryset(
            super().get_queryset(request),
            request.user,
            field="job__organization_id",
        )

    def turing_organization(self, obj):
        return obj.job.organization if obj and obj.job_id else None

    def has_add_permission(self, request) -> bool:
        return False

    @admin.display(description="Message")
    def message_short(self, obj: ProcessingLog):
        return obj.message[:120]
