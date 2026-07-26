from __future__ import annotations

from django import forms
from django.contrib import admin

from turing.models import PlatformConfiguration, SpeechProviderConfig
from turing.security.secrets import mask_secret


@admin.register(PlatformConfiguration)
class PlatformConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "default_provider_code",
        "storage_backend",
        "auto_enqueue",
        "default_max_attempts",
        "enable_diarization_default",
        "updated_at",
    )
    fieldsets = (
        (
            "Provider defaults",
            {
                "fields": (
                    "default_provider_code",
                    "default_language",
                    "enable_diarization_default",
                )
            },
        ),
        (
            "Processing",
            {
                "fields": (
                    "auto_enqueue",
                    "default_max_attempts",
                    "poll_interval_seconds",
                    "poll_timeout_seconds",
                    "max_upload_bytes",
                    "allowed_audio_extensions",
                    "allowed_audio_mime_types",
                    "storage_backend",
                )
            },
        ),
        (
            "API",
            {"fields": ("api_require_auth", "api_page_size")},
        ),
        ("Notes", {"fields": ("notes",)}),
    )

    def has_add_permission(self, request) -> bool:
        return not PlatformConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class SpeechProviderConfigForm(forms.ModelForm):
    """Never prefill or render the live API key; blank means keep existing."""

    api_key = forms.CharField(
        required=False,
        label="API key",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text=(
            "Enter a new key to replace the stored secret. "
            "Leave blank to keep the current key. "
            "Stored values are encrypted at rest."
        ),
    )

    class Meta:
        model = SpeechProviderConfig
        fields = (
            "code",
            "name",
            "is_active",
            "priority",
            "api_key",
            "base_url",
            "default_language",
            "operating_point",
            "enable_diarization",
            "extra_options",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["api_key"].initial = ""


@admin.register(SpeechProviderConfig)
class SpeechProviderConfigAdmin(admin.ModelAdmin):
    form = SpeechProviderConfigForm
    list_display = (
        "name",
        "code",
        "is_active",
        "priority",
        "api_key_display",
        "operating_point",
        "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("priority", "code")
    readonly_fields = ("api_key_display",)
    fieldsets = (
        (None, {"fields": ("code", "name", "is_active", "priority")}),
        (
            "Credentials",
            {
                "fields": ("api_key_display", "api_key", "base_url"),
                "description": (
                    "API keys are encrypted in the database and never shown in full. "
                    "Priority: database secret → TURING_SPEECHMATICS_API_KEY env → error."
                ),
            },
        ),
        (
            "Defaults",
            {
                "fields": (
                    "default_language",
                    "operating_point",
                    "enable_diarization",
                    "extra_options",
                )
            },
        ),
    )

    @admin.display(description="API key")
    def api_key_display(self, obj: SpeechProviderConfig) -> str:
        if not obj or not obj.pk:
            return "(not set)"
        return mask_secret(obj.api_key)

    def save_model(self, request, obj, form, change):
        new_key = (form.cleaned_data.get("api_key") or "").strip()
        if change and not new_key:
            # Preserve existing encrypted secret when the password field is left blank.
            previous = SpeechProviderConfig.objects.get(pk=obj.pk)
            obj.api_key = previous.api_key
        elif new_key:
            obj.api_key = new_key
        else:
            obj.api_key = ""
        super().save_model(request, obj, form, change)
