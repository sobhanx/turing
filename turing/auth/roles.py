from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser

from turing.domain.enums import TuringRole
from turing.domain.policies import ROLE_CAPABILITIES, role_has_capability
from turing.models import TuringMembership


def get_user_role(user: AbstractBaseUser | None) -> str | None:
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_superuser", False):
        return TuringRole.ADMIN
    membership = getattr(user, "turing_membership", None)
    if membership is None:
        try:
            membership = TuringMembership.objects.filter(user=user, is_active=True).first()
        except Exception:
            membership = None
    if membership and membership.is_active:
        return membership.role
    # Staff without membership defaults to reviewer for ops convenience
    if getattr(user, "is_staff", False):
        return TuringRole.REVIEWER
    return TuringRole.USER


def user_has_capability(user: AbstractBaseUser | None, capability: str) -> bool:
    role = get_user_role(user)
    if not role:
        return False
    return role_has_capability(role, capability)


def ensure_default_roles_documented() -> dict[str, frozenset[str]]:
    """Expose role capability map for Admin help text / docs."""
    return ROLE_CAPABILITIES
