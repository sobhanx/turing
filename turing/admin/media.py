from __future__ import annotations

from django.contrib import admin, messages

from turing.models import MediaAsset
from turing.services.job_orchestrator import JobOrchestrator


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "display_name",
        "use_case",
        "source_type",
        "content_type",
        "byte_size",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("use_case", "source_type", "storage_backend", "created_at")
    search_fields = ("id", "original_filename", "external_url", "checksum", "tenant_key")
    readonly_fields = ("checksum", "byte_size", "object_key", "created_at", "updated_at")
    raw_id_fields = ("uploaded_by",)
    actions = ("create_transcription_jobs",)

    @admin.action(description="Create transcription jobs (Speechmatics)")
    def create_transcription_jobs(self, request, queryset):
        orchestrator = JobOrchestrator()
        created = 0
        for media in queryset:
            orchestrator.create_transcription_job(
                media=media,
                created_by=request.user,
            )
            created += 1
        self.message_user(
            request,
            f"Created and enqueued {created} transcription job(s).",
            messages.SUCCESS,
        )
