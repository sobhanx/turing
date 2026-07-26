from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from turing.domain.enums import JobStatus
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
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status_badge",
        "capability",
        "provider_code",
        "media_link",
        "language_code",
        "attempt_count",
        "created_by",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "capability", "provider_code", "created_at")
    search_fields = ("id", "external_job_id", "idempotency_key", "error_code", "tenant_key")
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
                orch.enqueue(job)
                count += 1
            except Exception as exc:  # noqa: BLE001
                self.message_user(request, f"{job.id}: {exc}", messages.ERROR)
        self.message_user(request, f"Enqueued {count} job(s).", messages.SUCCESS)

    @admin.action(description="Retry failed jobs")
    def retry_jobs(self, request, queryset):
        orch = JobOrchestrator()
        count = 0
        for job in queryset:
            try:
                orch.retry(job)
                count += 1
            except Exception as exc:  # noqa: BLE001
                self.message_user(request, f"{job.id}: {exc}", messages.ERROR)
        self.message_user(request, f"Retried {count} job(s).", messages.SUCCESS)

    @admin.action(description="Cancel selected jobs")
    def cancel_jobs(self, request, queryset):
        orch = JobOrchestrator()
        count = 0
        for job in queryset:
            try:
                orch.cancel(job)
                count += 1
            except Exception as exc:  # noqa: BLE001
                self.message_user(request, f"{job.id}: {exc}", messages.ERROR)
        self.message_user(request, f"Cancelled {count} job(s).", messages.SUCCESS)


@admin.register(ProcessingLog)
class ProcessingLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "job", "level", "message_short")
    list_filter = ("level", "created_at")
    search_fields = ("message", "job__id")
    readonly_fields = ("job", "attempt", "level", "message", "context", "created_at", "updated_at")

    @admin.display(description="Message")
    def message_short(self, obj: ProcessingLog):
        return obj.message[:120]
