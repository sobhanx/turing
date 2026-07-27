from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser

from turing.auth.tenancy import (
    active_memberships_for,
    get_membership_for_organization,
    user_is_global_bypass,
)
from turing.domain.enums import TuringRole
from turing.domain.policies import role_has_capability
from turing.models import Organization, TuringMembership


def get_user_role(
    user: AbstractBaseUser | None,
    *,
    organization: Organization | None = None,
) -> str | None:
    """
    Resolve Turing role from membership (or superuser → ADMIN).

    ``is_staff`` never invents a role. Without membership the role is ``None``.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if user_is_global_bypass(user):
        return TuringRole.ADMIN

    if organization is not None:
        membership = get_membership_for_organization(user, organization)
        return membership.role if membership else None

    memberships = list(
        TuringMembership.objects.filter(user=user, is_active=True).only("role")
    )
    if not memberships:
        return None
    # Display/debug only — prefer ``user_has_capability(..., organization=)``
    return max(memberships, key=lambda m: _role_rank(m.role)).role


def user_has_capability(
    user: AbstractBaseUser | None,
    capability: str,
    *,
    organization: Organization | None = None,
) -> bool:
    """
    Capability check — membership is the single source of truth.

    With ``organization``: evaluate that org's membership role only.
    Without ``organization``: true if *any* active membership grants the
    capability. Superuser is the only global bypass.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False

    if user_is_global_bypass(user):
        return role_has_capability(TuringRole.ADMIN, capability)

    if organization is not None:
        membership = get_membership_for_organization(user, organization)
        if membership is None:
            return False
        return role_has_capability(membership.role, capability)

    for membership in active_memberships_for(user):
        if role_has_capability(membership.role, capability):
            return True
    return False


def _role_rank(role: str) -> int:
    order = {
        TuringRole.VIEWER: 1,
        TuringRole.USER: 2,
        TuringRole.EDITOR: 3,
        TuringRole.REVIEWER: 4,
        TuringRole.ADMIN: 5,
    }
    return order.get(role, 0)
