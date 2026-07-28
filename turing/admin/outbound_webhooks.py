from __future__ import annotations

from django import forms
from django.contrib import admin

from turing.admin import fa as fa_labels
from turing.admin.authz import CapabilityGatedAdminMixin, admin_scope_queryset
from turing.admin.persian import PersianAdminMixin
from turing.models import WebhookDelivery, WebhookSubscription
from turing.security.secrets import mask_secret


class WebhookSubscriptionForm(forms.ModelForm):
    """Never prefill or render the live signing secret; blank means keep existing."""

    secret = forms.CharField(
        required=False,
        label=fa_labels.FIELD_LABELS["secret"],
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password"},
        ),
        help_text=fa_labels.FIELD_HELP["secret"],
    )

    class Meta:
        model = WebhookSubscription
        fields = (
            "organization",
            "name",
            "url",
            "secret",
            "subscribed_events",
            "is_active",
        )
        labels = {
            key: fa_labels.FIELD_LABELS[key]
            for key in ("organization", "name", "url", "subscribed_events", "is_active")
            if key in fa_labels.FIELD_LABELS
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["secret"].initial = ""


class DeliveryEventFilter(admin.SimpleListFilter):
    title = fa_labels.FILTER_EVENT
    parameter_name = "event"

    def lookups(self, request, model_admin):
        names = (
            WebhookDelivery.objects.order_by("outbox_event__event_name")
            .values_list("outbox_event__event_name", flat=True)
            .distinct()
        )
        return [(name, name) for name in names if name]

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            return queryset.filter(outbox_event__event_name=value)
        return queryset


class DeliveryAttemptsFilter(admin.SimpleListFilter):
    title = fa_labels.FILTER_ATTEMPTS
    parameter_name = "attempts_bucket"

    def lookups(self, request, model_admin):
        return [
            ("0", "0"),
            ("1", "1"),
            ("2_3", "2–3"),
            ("4_plus", "4+"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "0":
            return queryset.filter(attempts=0)
        if value == "1":
            return queryset.filter(attempts=1)
        if value == "2_3":
            return queryset.filter(attempts__gte=2, attempts__lte=3)
        if value == "4_plus":
            return queryset.filter(attempts__gte=4)
        return queryset


@admin.register(WebhookSubscription)
class WebhookSubscriptionAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "manage_config"
    turing_change_capability = "manage_config"
    turing_add_capability = "manage_config"
    turing_delete_capability = "manage_config"

    form = WebhookSubscriptionForm
    list_display = (
        "name",
        "organization",
        "url",
        "is_active",
        "subscribed_events_display",
        "secret_display",
        "created_at",
    )
    list_filter = ("is_active", "organization", "created_at")
    search_fields = ("name", "url", "subscribed_events", "organization__name", "organization__slug")
    readonly_fields = ("secret_display", "created_at", "updated_at")
    autocomplete_fields = ("organization",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "organization",
                    "name",
                    "url",
                    "is_active",
                    "subscribed_events",
                )
            },
        ),
        (
            "اعتبارنامه‌ها",
            {
                "fields": ("secret_display", "secret"),
                "description": (
                    "رمزهای امضا در پایگاه‌داده رمزنگاری می‌شوند و هرگز کامل نمایش داده نمی‌شوند."
                ),
            },
        ),
        ("زمان‌ها", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="رویدادها")
    def subscribed_events_display(self, obj: WebhookSubscription) -> str:
        events = obj.subscribed_events or []
        return ", ".join(events) if events else "(none)"

    @admin.display(description="رمز")
    def secret_display(self, obj: WebhookSubscription) -> str:
        if not obj or not obj.pk:
            return "(not set)"
        return mask_secret(obj.secret)

    def save_model(self, request, obj, form, change):
        new_secret = (form.cleaned_data.get("secret") or "").strip()
        if change and not new_secret:
            previous = WebhookSubscription.objects.get(pk=obj.pk)
            obj.secret = previous.secret
        elif new_secret:
            obj.secret = new_secret
        else:
            obj.secret = ""
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("organization")
        return admin_scope_queryset(qs, request.user, field="organization_id")


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "manage_config"
    turing_change_capability = "manage_config"
    turing_add_capability = "manage_config"
    turing_delete_capability = "manage_config"

    list_display = (
        "id",
        "subscription",
        "event_name",
        "status",
        "attempts",
        "recovery_count",
        "response_status_code",
        "processing_started_at",
        "delivered_at",
        "created_at",
    )
    list_filter = (
        "status",
        DeliveryEventFilter,
        DeliveryAttemptsFilter,
        "created_at",
        "subscription__organization",
    )
    search_fields = (
        "id",
        "subscription__name",
        "outbox_event__event_name",
        "last_error",
        "response_body_preview",
    )
    readonly_fields = (
        "subscription",
        "outbox_event",
        "status",
        "attempts",
        "recovery_count",
        "response_status_code",
        "response_body_preview",
        "last_error",
        "processing_started_at",
        "delivered_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="رویداد", ordering="outbox_event__event_name")
    def event_name(self, obj: WebhookDelivery) -> str:
        return obj.outbox_event.event_name if obj.outbox_event_id else ""

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            "subscription",
            "subscription__organization",
            "outbox_event",
        )
        return admin_scope_queryset(
            qs,
            request.user,
            field="subscription__organization_id",
        )
