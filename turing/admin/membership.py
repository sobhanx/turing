from __future__ import annotations

from django.contrib import admin

from turing.models import Organization, TuringMembership


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "external_key", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "external_key", "notes")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(TuringMembership)
class TuringMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "is_active", "updated_at")
    list_filter = ("role", "is_active", "organization")
    search_fields = (
        "user__username",
        "user__email",
        "organization__name",
        "organization__slug",
        "notes",
    )
    raw_id_fields = ("user",)
    autocomplete_fields = ("organization",)
