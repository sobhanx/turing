"""Speech Center UI authorization helpers (align with API capabilities)."""

from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from turing.auth.roles import user_has_capability
from turing.auth.tenancy import user_is_global_bypass


def require_turing_capability(capability: str):
    """
    Require a Turing capability in addition to staff login.

    Superusers bypass. Staff without membership/capability get 403 —
    matching API ``HasTuringCapability`` semantics.
    """

    def decorator(view):
        @wraps(view)
        def _wrapped(request: HttpRequest, *args, **kwargs):
            user = request.user
            if not user_is_global_bypass(user) and not user_has_capability(
                user, capability
            ):
                raise PermissionDenied(
                    f"Missing Turing capability '{capability}'."
                )
            return view(request, *args, **kwargs)

        return _wrapped

    return decorator
