from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser
from django.db.models import QuerySet

from turing.domain.exceptions import NotFoundError, PermissionDeniedError
from turing.models import Organization, TuringMembership


def user_is_global_bypass(user: AbstractBaseUser | None) -> bool:
    """
    True only for Django superusers.

    ``is_staff`` alone does **not** grant Turing capabilities or cross-org access.
    Staff may use Django Admin UI; membership (or superuser) is required for
    Turing business actions.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(user, "is_superuser", False))


def user_sees_all_organizations(user: AbstractBaseUser | None) -> bool:
    """Cross-org visibility / unscoped querysets — superuser only."""
    return user_is_global_bypass(user)


def active_memberships_for(user: AbstractBaseUser | None) -> QuerySet[TuringMembership]:
    if user is None or not getattr(user, "is_authenticated", False):
        return TuringMembership.objects.none()
    return TuringMembership.objects.filter(user=user, is_active=True).select_related(
        "organization"
    )


def organization_ids_for(user: AbstractBaseUser | None) -> list:
    return list(
        active_memberships_for(user).values_list("organization_id", flat=True)
    )


def get_membership_for_organization(
    user: AbstractBaseUser | None,
    organization: Organization | None,
) -> TuringMembership | None:
    if user is None or organization is None:
        return None
    return (
        active_memberships_for(user).filter(organization_id=organization.pk).first()
    )


def assert_organization_access(
    user: AbstractBaseUser | None,
    organization: Organization,
    *,
    capability: str | None = None,
) -> None:
    """
    Ensure ``user`` may use ``organization`` (and optionally a capability there).

    - Superuser: always allowed (optional capability still treated as granted).
    - Members: must have an active membership; capability checked in that org.
    - ``user is None``: allowed for CLI/system paths.
    - Staff without membership: denied.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return
    if user_is_global_bypass(user):
        return

    membership = get_membership_for_organization(user, organization)
    if membership is None:
        raise PermissionDeniedError(
            f"You are not a member of organization '{organization.slug}'."
        )
    if capability:
        from turing.auth.roles import user_has_capability

        if not user_has_capability(user, capability, organization=organization):
            raise PermissionDeniedError(
                f"Missing capability '{capability}' for organization "
                f"'{organization.slug}'."
            )


def resolve_organization(
    *,
    organization: Organization | None = None,
    organization_id=None,
    tenant_key: str = "",
    user: AbstractBaseUser | None = None,
    capability: str | None = None,
) -> Organization:
    """
    Resolve owning organization for create paths.

    Explicit targets are membership-validated (superuser bypasses).
    They never fall back to Default on denial.

    When no explicit target is given:
    - superuser → Default
    - authenticated member → first active membership org
    - authenticated non-member (including staff) → PermissionDeniedError
    - no user (CLI/system) → Default (local workflow)
    """
    org: Organization | None = None

    if organization is not None:
        org = organization
    elif organization_id not in (None, ""):
        org = Organization.objects.filter(pk=organization_id, is_active=True).first()
        if org is None:
            raise NotFoundError(f"Organization '{organization_id}' not found.")
    else:
        key = (tenant_key or "").strip()
        if key:
            org = Organization.objects.filter(slug=key, is_active=True).first()
            if org is None:
                org = Organization.objects.filter(
                    external_key=key, is_active=True
                ).first()
            if org is None:
                raise NotFoundError(f"Organization matching '{key}' not found.")

    if org is not None:
        assert_organization_access(user, org, capability=capability)
        return org

    # Implicit resolution (no explicit target)
    if user is not None and getattr(user, "is_authenticated", False):
        if user_is_global_bypass(user):
            return Organization.get_default()
        membership = active_memberships_for(user).order_by("id").first()
        if membership is None:
            raise PermissionDeniedError(
                "You must belong to an organization to create resources."
            )
        if capability:
            assert_organization_access(
                user, membership.organization, capability=capability
            )
        return membership.organization

    return Organization.get_default()


def scope_by_organization(
    queryset: QuerySet,
    user: AbstractBaseUser | None,
    *,
    field: str = "organization_id",
) -> QuerySet:
    """Filter queryset to organizations the user may access."""
    if user_sees_all_organizations(user):
        return queryset
    org_ids = organization_ids_for(user)
    if not org_ids:
        return queryset.none()
    return queryset.filter(**{f"{field}__in": org_ids})
