from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser

from turing.auth.tenancy import (
    active_memberships_for,
    get_membership_for_organization,
    user_sees_all_organizations,
)
from turing.domain.enums import TuringRole
from turing.domain.policies import ROLE_CAPABILITIES, role_has_capability
from turing.models import Organization, TuringMembership


def get_user_role(
    user: AbstractBaseUser | None,
    *,
    organization: Organization | None = None,
) -> str | None:
    """
    Resolve Turing role for capability checks.

    When ``organization`` is provided, use that membership's role only
    (staff/superuser may receive an ops role without membership).
    Without organization, prefer calling ``user_has_capability`` which checks
    memberships individually — this helper returns the highest role for
    display/debug only.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_superuser", False):
        return TuringRole.ADMIN

    if organization is not None:
        membership = get_membership_for_organization(user, organization)
        if membership:
            return membership.role
        if user_sees_all_organizations(user):
            return TuringRole.REVIEWER
        return None

    memberships = list(
        TuringMembership.objects.filter(user=user, is_active=True).only("role")
    )
    if memberships:
        return max(
            memberships,
            key=lambda m: _role_rank(m.role),
        ).role

    # Staff without membership: Admin/ops convenience only.
    if getattr(user, "is_staff", False):
        return TuringRole.REVIEWER
    # No membership → no implicit USER role (prevents unscoped uploads).
    return None


def user_has_capability(
    user: AbstractBaseUser | None,
    capability: str,
    *,
    organization: Organization | None = None,
) -> bool:
    """
    Capability check.

    With ``organization``: evaluate that org's membership role only
    (never the global max role).

    Without ``organization``: true if *any* active membership grants the
    capability (or staff/superuser ops role). Does not invent a USER role
    for users with no memberships.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False

    if organization is not None:
        role = get_user_role(user, organization=organization)
        if not role:
            return False
        return role_has_capability(role, capability)

    if getattr(user, "is_superuser", False):
        return role_has_capability(TuringRole.ADMIN, capability)

    for membership in active_memberships_for(user):
        if role_has_capability(membership.role, capability):
            return True

    if getattr(user, "is_staff", False):
        return role_has_capability(TuringRole.REVIEWER, capability)

    return False


def ensure_default_roles_documented() -> dict[str, frozenset[str]]:
    """Expose role capability map for Admin help text / docs."""
    return ROLE_CAPABILITIES


def _role_rank(role: str) -> int:
    order = {
        TuringRole.VIEWER: 1,
        TuringRole.USER: 2,
        TuringRole.EDITOR: 3,
        TuringRole.REVIEWER: 4,
        TuringRole.ADMIN: 5,
    }
    return order.get(role, 0)
