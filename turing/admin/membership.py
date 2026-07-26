from __future__ import annotations

from django.contrib import admin

from turing.models import TuringMembership


@admin.register(TuringMembership)
class TuringMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "is_active", "updated_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__username", "user__email", "notes")
    raw_id_fields = ("user",)
    autocomplete_fields = ()
