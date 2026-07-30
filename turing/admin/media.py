from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpResponseRedirect
from django.urls import reverse

from turing.admin import fa as fa_labels
from turing.admin.authz import (
    CapabilityGatedAdminMixin,
    admin_assert_capability,
    admin_scope_queryset,
)
from turing.admin.persian import PersianAdminMixin
from turing.conf import clear_settings_cache, get_turing_settings
from turing.domain.enums import SourceType
from turing.domain.exceptions import PermissionDeniedError, TuringError, ValidationError
from turing.models import MediaAsset, Organization
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService


def _upload_basename(file) -> str:
    name = getattr(file, "name", "") or ""
    return name.rsplit("/", 1)[-1].strip()


class MediaAssetForm(forms.ModelForm):
    """Admin upload form: require a file on create and auto-fill display filename."""

    class Meta:
        model = MediaAsset
        fields = (
            "source_type",
            "use_case",
            "file",
            "original_filename",
            "external_url",
            "uploaded_by",
            "organization",
            "tenant_key",
            "metadata",
        )
        labels = {
            key: fa_labels.FIELD_LABELS[key]
            for key in (
                "source_type",
                "use_case",
                "file",
                "original_filename",
                "external_url",
                "uploaded_by",
                "organization",
                "tenant_key",
                "metadata",
            )
            if key in fa_labels.FIELD_LABELS
        }
        widgets = {
            "original_filename": forms.TextInput(
                attrs={"placeholder": fa_labels.MSG_FILENAME_PLACEHOLDER},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["original_filename"].help_text = fa_labels.FIELD_HELP["original_filename"]
        if "file" in self.fields:
            self.fields["file"].help_text = fa_labels.FIELD_HELP.get("file", "")

    def clean(self):
        cleaned_data = super().clean()
        source_type = cleaned_data.get("source_type") or SourceType.UPLOAD
        uploaded_file = cleaned_data.get("file")
        external_url = (cleaned_data.get("external_url") or "").strip()

        if uploaded_file and self._is_new_upload(uploaded_file):
            basename = _upload_basename(uploaded_file)
            if basename and not (cleaned_data.get("original_filename") or "").strip():
                cleaned_data["original_filename"] = basename

        if not self.instance.pk:
            if source_type == SourceType.URL:
                if not external_url:
                    raise forms.ValidationError(
                        {"external_url": fa_labels.MSG_URL_REQUIRED}
                    )
            elif not uploaded_file:
                raise forms.ValidationError(
                    {"file": fa_labels.MSG_UPLOAD_REQUIRED}
                )
        elif source_type == SourceType.URL:
            if not external_url and not (self.instance.external_url or "").strip():
                raise forms.ValidationError(
                    {"external_url": fa_labels.MSG_URL_REQUIRED}
                )
        elif not self._has_stored_file(uploaded_file):
            raise forms.ValidationError(
                {"file": fa_labels.MSG_UPLOAD_REQUIRED}
            )

        return cleaned_data

    @staticmethod
    def _is_new_upload(file) -> bool:
        return isinstance(file, UploadedFile)

    def _has_stored_file(self, uploaded_file) -> bool:
        if uploaded_file:
            return True
        if not self.instance.pk:
            return False
        return bool(self.instance.file or self.instance.object_key)


@admin.register(MediaAsset)
class MediaAssetAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    form = MediaAssetForm
    turing_view_capability = "view_transcript"
    turing_change_capability = "upload_media"
    turing_add_capability = "upload_media"
    turing_delete_capability = "upload_media"

    list_display = (
        "id",
        "display_name",
        "organization",
        "use_case",
        "source_type",
        "audio_format",
        "display_duration",
        "display_size",
        "storage_backend",
        "uploaded_by",
        "created_at",
    )
    list_select_related = ("organization", "uploaded_by")
    list_per_page = 50
    list_filter = (
        "organization",
        "use_case",
        "source_type",
        "storage_backend",
        "audio_format",
        "created_at",
    )
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
    autocomplete_fields = ("organization",)
    actions = ("create_transcription_jobs",)

    def get_queryset(self, request):
        return admin_scope_queryset(super().get_queryset(request), request.user)

    def save_model(self, request, obj, form, change):
        if not obj.organization_id:
            obj.organization = Organization.get_default()
            if not obj.tenant_key:
                obj.tenant_key = obj.organization.slug
        try:
            admin_assert_capability(
                request.user,
                organization=obj.organization,
                capability="upload_media",
            )
        except PermissionDeniedError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return
        admin.ModelAdmin.save_model(self, request, obj, form, change)
        if obj.file or obj.object_key:
            try:
                if not obj.original_filename and obj.file:
                    obj.original_filename = obj.file.name.rsplit("/", 1)[-1]
                    obj.save(update_fields=["original_filename", "updated_at"])
                MediaService().enrich_uploaded_asset(obj)
            except ValidationError as exc:
                self.message_user(request, str(exc), messages.ERROR)
                raise

    @admin.action(description="ایجاد پردازش رونویسی (Speechmatics)")
    def create_transcription_jobs(self, request, queryset):
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
                    "امکان ایجاد پردازش رونویسی نیست: زبان پیش‌فرض تنظیم نشده. "
                    "در پیکربندی سامانه، زبان پیش‌فرض را تنظیم کنید (مثلاً fa) و دوباره تلاش کنید.",
                    messages.ERROR,
                )
                return

        orchestrator = JobOrchestrator()
        created = 0
        languages: set[str] = set()
        for media in queryset:
            try:
                admin_assert_capability(
                    request.user,
                    organization=media.organization,
                    capability="manage_jobs",
                )
                job = orchestrator.create_transcription_job(
                    media=media,
                    created_by=request.user,
                )
            except (PermissionDeniedError, ValidationError, TuringError) as exc:
                self.message_user(
                    request,
                    f"خطا برای رسانه {media.id}: {exc}",
                    messages.ERROR,
                )
                continue
            created += 1
            languages.add(job.language_code)

        if created:
            lang_note = ", ".join(sorted(languages)) or "(none)"
            self.message_user(
                request,
                f"{created} پردازش رونویسی ایجاد و در صف قرار گرفت "
                f"(language_code={lang_note}).",
                messages.SUCCESS,
            )
            # Navigation only: after a successful Go action, open Speech Center home.
            return HttpResponseRedirect(reverse("speech_center:dashboard"))
