from __future__ import annotations

from django import forms
from django.contrib import admin

from turing.admin import fa as fa_labels
from turing.admin.authz import GlobalCapabilityAdminMixin
from turing.admin.persian import PersianAdminMixin
from turing.models import PlatformConfiguration, ProviderCredential, SpeechProviderConfig
from turing.security.secrets import mask_secret


@admin.register(PlatformConfiguration)
class PlatformConfigurationAdmin(PersianAdminMixin, GlobalCapabilityAdminMixin, admin.ModelAdmin):
    turing_capability = "manage_config"
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
            "پیش‌فرض‌های ارائه‌دهنده",
            {
                "fields": (
                    "default_provider_code",
                    "default_language",
                    "enable_diarization_default",
                )
            },
        ),
        (
            "پردازش",
            {
                "fields": (
                    "auto_enqueue",
                    "default_max_attempts",
                    "poll_interval_seconds",
                    "poll_timeout_seconds",
                    "poll_timeout_multiplier",
                    "normalization_enabled",
                    "max_duration_ms",
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
        (
            "وب‌هوک‌ها",
            {
                "fields": ("webhook_mode", "webhook_base_url"),
                "description": (
                    "حالت augment هنگام تنظیم رمز وب‌هوک Speechmatics و نشانی پایه، "
                    "callback ثبت می‌کند. نظرسنجی همچنان به‌عنوان پشتیبان فعال است."
                ),
            },
        ),
        ("یادداشت‌ها", {"fields": ("notes",)}),
    )

    def has_add_permission(self, request) -> bool:
        if not PlatformConfiguration.objects.exists():
            return super().has_add_permission(request)
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class SpeechProviderConfigForm(forms.ModelForm):
    """Never prefill or render the live API key; blank means keep existing."""

    api_key = forms.CharField(
        required=False,
        label=fa_labels.FIELD_LABELS["api_key"],
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text=fa_labels.FIELD_HELP["api_key"],
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
        labels = {
            key: fa_labels.FIELD_LABELS[key]
            for key in (
                "code",
                "name",
                "is_active",
                "priority",
                "base_url",
                "default_language",
                "operating_point",
                "enable_diarization",
                "extra_options",
            )
            if key in fa_labels.FIELD_LABELS
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["api_key"].initial = ""


@admin.register(SpeechProviderConfig)
class SpeechProviderConfigAdmin(PersianAdminMixin, GlobalCapabilityAdminMixin, admin.ModelAdmin):
    turing_capability = "manage_config"
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
            "اعتبارنامه‌ها",
            {
                "fields": ("api_key_display", "api_key", "base_url"),
                "description": (
                    "کلیدهای API در پایگاه‌داده رمزنگاری می‌شوند و هرگز کامل نمایش داده نمی‌شوند. "
                    "اولویت: رمز پایگاه‌داده ← متغیر محیطی TURING_SPEECHMATICS_API_KEY ← خطا."
                ),
            },
        ),
        (
            "پیش‌فرض‌ها",
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

    @admin.display(description=fa_labels.FIELD_LABELS["api_key"])
    def api_key_display(self, obj: SpeechProviderConfig) -> str:
        if not obj or not obj.pk:
            return "(not set)"
        return mask_secret(obj.api_key)

    def save_model(self, request, obj, form, change):
        new_key = (form.cleaned_data.get("api_key") or "").strip()
        if change and not new_key:
            previous = SpeechProviderConfig.objects.get(pk=obj.pk)
            obj.api_key = previous.api_key
        elif new_key:
            obj.api_key = new_key
        else:
            obj.api_key = ""
        super().save_model(request, obj, form, change)


class ProviderCredentialForm(forms.ModelForm):
    """Never prefill or render the live API key; blank means keep existing."""

    api_key = forms.CharField(
        required=False,
        label=fa_labels.FIELD_LABELS.get("api_key", "API key"),
        widget=forms.PasswordInput(
            render_value=False, attrs={"autocomplete": "new-password"}
        ),
        help_text=(
            "Leave blank to keep the current key. Stored encrypted; never shown "
            "in plaintext after save."
        ),
    )

    class Meta:
        model = ProviderCredential
        fields = (
            "provider",
            "name",
            "is_active",
            "priority",
            "api_key",
            "cooldown_until",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["api_key"].initial = ""


@admin.register(ProviderCredential)
class ProviderCredentialAdmin(PersianAdminMixin, GlobalCapabilityAdminMixin, admin.ModelAdmin):
    """
    Manage STT API-key pool rows.

    Secrets are masked; blank password field keeps the existing encrypted key.
    """

    turing_capability = "manage_config"
    form = ProviderCredentialForm
    list_display = (
        "name",
        "provider",
        "is_active",
        "priority",
        "api_key_display",
        "last_used_at",
        "cooldown_until",
        "failure_count",
        "last_error_code",
        "updated_at",
    )
    list_filter = ("is_active", "provider")
    search_fields = ("name", "provider__code", "provider__name", "last_error_code")
    ordering = ("priority", "last_used_at", "id")
    autocomplete_fields = ("provider",)
    readonly_fields = (
        "api_key_display",
        "last_used_at",
        "failure_count",
        "last_error_code",
        "last_error_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            None,
            {"fields": ("provider", "name", "is_active", "priority")},
        ),
        (
            "API key",
            {
                "fields": ("api_key_display", "api_key"),
                "description": (
                    "Keys are encrypted at rest and never shown in full. "
                    "Leave the password field blank to keep the current key."
                ),
            },
        ),
        (
            "Pool status",
            {
                "fields": (
                    "last_used_at",
                    "cooldown_until",
                    "failure_count",
                    "last_error_code",
                    "last_error_at",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description=fa_labels.FIELD_LABELS.get("api_key", "API key"))
    def api_key_display(self, obj: ProviderCredential) -> str:
        if not obj or not obj.pk:
            return "(not set)"
        return mask_secret(obj.api_key)

    def save_model(self, request, obj, form, change):
        new_key = (form.cleaned_data.get("api_key") or "").strip()
        if change and not new_key:
            previous = ProviderCredential.objects.get(pk=obj.pk)
            obj.api_key = previous.api_key
        elif new_key:
            obj.api_key = new_key
        else:
            obj.api_key = ""
        super().save_model(request, obj, form, change)
