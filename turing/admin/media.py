from __future__ import annotations

from django.contrib import admin, messages

from turing.conf import clear_settings_cache, get_turing_settings
from turing.domain.exceptions import TuringError, ValidationError
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
        """
        Create jobs using Platform / provider default language.

        Does not silently create jobs without language_code — configure
        Platform configuration → Default language (e.g. fa) first.
        """
        clear_settings_cache()
        settings = get_turing_settings()
        if not (settings.default_language or "").strip():
            # Also allow provider-level default; orchestrator resolves fully.
            from turing.models.configuration import SpeechProviderConfig

            provider_has_lang = SpeechProviderConfig.objects.filter(
                code=settings.default_provider,
                is_active=True,
            ).exclude(default_language="").exists()
            if not provider_has_lang:
                self.message_user(
                    request,
                    "Cannot create transcription jobs: no default language configured. "
                    "Set Platform configuration → Default language (e.g. fa for Persian), "
                    "then try again.",
                    messages.ERROR,
                )
                return

        orchestrator = JobOrchestrator()
        created = 0
        languages: set[str] = set()
        for media in queryset:
            try:
                job = orchestrator.create_transcription_job(
                    media=media,
                    created_by=request.user,
                )
            except (ValidationError, TuringError) as exc:
                self.message_user(
                    request,
                    f"Failed for media {media.id}: {exc}",
                    messages.ERROR,
                )
                continue
            created += 1
            languages.add(job.language_code)

        if created:
            lang_note = ", ".join(sorted(languages)) or "(none)"
            self.message_user(
                request,
                f"Created and enqueued {created} transcription job(s) "
                f"with language_code={lang_note}.",
                messages.SUCCESS,
            )
