from __future__ import annotations

from django.contrib import admin, messages

from turing.conf import clear_settings_cache, get_turing_settings
from turing.domain.exceptions import TuringError, ValidationError
from turing.models import MediaAsset
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "display_name",
        "use_case",
        "source_type",
        "audio_format",
        "display_duration",
        "display_size",
        "storage_backend",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("use_case", "source_type", "storage_backend", "audio_format", "created_at")
    search_fields = ("id", "original_filename", "external_url", "checksum", "tenant_key")
    readonly_fields = (
        "checksum",
        "byte_size",
        "object_key",
        "duration_ms",
        "sample_rate_hz",
        "channels",
        "audio_format",
        "audio_codec",
        "storage_backend",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("uploaded_by",)
    actions = ("create_transcription_jobs",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.file or obj.object_key:
            try:
                if not obj.original_filename and obj.file:
                    obj.original_filename = obj.file.name.rsplit("/", 1)[-1]
                    obj.save(update_fields=["original_filename", "updated_at"])
                MediaService().enrich_uploaded_asset(obj)
            except ValidationError as exc:
                self.message_user(request, str(exc), messages.ERROR)
                raise

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
