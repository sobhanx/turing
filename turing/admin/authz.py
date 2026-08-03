"""Helpers for Django Admin Turing capability gates."""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser
from django.db.models import QuerySet
from django.http import HttpRequest

from turing.auth.roles import user_has_capability
from turing.auth.tenancy import scope_by_organization, user_is_global_bypass
from turing.domain.exceptions import PermissionDeniedError
from turing.models import Organization


def admin_assert_capability(
    user: AbstractBaseUser,
    *,
    organization: Organization | None,
    capability: str,
) -> None:
    """
    Gate Admin business actions.

    Superuser bypasses. Otherwise requires membership capability in ``organization``.
    """
    if user_is_global_bypass(user):
        return
    if organization is None:
        raise PermissionDeniedError(
            "This resource has no organization; only a superuser may act on it."
        )
    if not user_has_capability(user, capability, organization=organization):
        raise PermissionDeniedError(
            f"Missing capability '{capability}' for organization '{organization.slug}'."
        )


def admin_may_capability(
    user: AbstractBaseUser,
    capability: str,
    *,
    organization: Organization | None = None,
) -> bool:
    """True if ``user`` may use ``capability`` (optionally in ``organization``)."""
    if user_is_global_bypass(user):
        return True
    if organization is not None:
        return user_has_capability(user, capability, organization=organization)
    return user_has_capability(user, capability)


def admin_scope_queryset(
    queryset: QuerySet,
    user: AbstractBaseUser,
    *,
    field: str = "organization_id",
) -> QuerySet:
    """Superuser sees all; others only membership organizations."""
    return scope_by_organization(queryset, user, field=field)


def _admin_staff_gate(user: AbstractBaseUser) -> bool:
    return bool(
        getattr(user, "is_active", False) and getattr(user, "is_staff", False)
    )


class AppendOnlyBrowseAdminMixin:
    """
    Browse-only Admin for append-only / audit rows.

    Staff cannot add, change, or delete these objects in Admin.
    Superusers may delete so parent cascade deletes (e.g. MediaAsset) are not
    blocked by Django's related-object permission checks. Model-level CASCADE
    / PROTECT rules are unchanged.
    """

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        return user_is_global_bypass(request.user)


class CapabilityGatedAdminMixin:
    """
    Enforce Turing capabilities on Admin add/change/delete/view.

    Membership capabilities are the source of truth for staff users.
    Django model ACLs alone do not grant Turing access; superuser bypasses.
    """

    turing_view_capability: str = "view_transcript"
    turing_change_capability: str = "view_transcript"
    turing_add_capability: str | None = None
    turing_delete_capability: str | None = None

    def turing_organization(self, obj) -> Organization | None:
        return getattr(obj, "organization", None)

    def has_module_permission(self, request: HttpRequest) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        if user_is_global_bypass(request.user):
            return True
        return user_has_capability(request.user, self.turing_view_capability)

    def has_view_permission(self, request: HttpRequest, obj=None) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        if obj is None:
            return admin_may_capability(request.user, self.turing_view_capability)
        return admin_may_capability(
            request.user,
            self.turing_view_capability,
            organization=self.turing_organization(obj),
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        capability = self.turing_add_capability or self.turing_change_capability
        return admin_may_capability(request.user, capability)

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        if obj is None:
            return admin_may_capability(request.user, self.turing_change_capability)
        return admin_may_capability(
            request.user,
            self.turing_change_capability,
            organization=self.turing_organization(obj),
        )

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        capability = self.turing_delete_capability or self.turing_change_capability
        if obj is None:
            return admin_may_capability(request.user, capability)
        return admin_may_capability(
            request.user,
            capability,
            organization=self.turing_organization(obj),
        )

    def save_model(self, request, obj, form, change):
        capability = (
            self.turing_add_capability or self.turing_change_capability
            if not change
            else self.turing_change_capability
        )
        try:
            admin_assert_capability(
                request.user,
                organization=self.turing_organization(obj),
                capability=capability,
            )
        except PermissionDeniedError as exc:
            from django.contrib import messages

            self.message_user(request, str(exc), messages.ERROR)  # type: ignore[attr-defined]
            return
        super().save_model(request, obj, form, change)  # type: ignore[misc]

    def delete_model(self, request, obj):
        capability = self.turing_delete_capability or self.turing_change_capability
        admin_assert_capability(
            request.user,
            organization=self.turing_organization(obj),
            capability=capability,
        )
        super().delete_model(request, obj)  # type: ignore[misc]

    def delete_queryset(self, request, queryset):
        capability = self.turing_delete_capability or self.turing_change_capability
        for obj in queryset:
            admin_assert_capability(
                request.user,
                organization=self.turing_organization(obj),
                capability=capability,
            )
        super().delete_queryset(request, queryset)  # type: ignore[misc]


class GlobalCapabilityAdminMixin:
    """Gate Admin models that are not org-scoped (config, memberships)."""

    turing_capability: str = "manage_config"

    def has_module_permission(self, request: HttpRequest) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        return admin_may_capability(request.user, self.turing_capability)

    def has_view_permission(self, request: HttpRequest, obj=None) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        return admin_may_capability(request.user, self.turing_capability)

    def has_add_permission(self, request: HttpRequest) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        return admin_may_capability(request.user, self.turing_capability)

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        return admin_may_capability(request.user, self.turing_capability)

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        if not _admin_staff_gate(request.user):
            return False
        return admin_may_capability(request.user, self.turing_capability)
