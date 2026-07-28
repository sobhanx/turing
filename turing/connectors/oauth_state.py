from __future__ import annotations

"""Deprecated shim — use ``turing.services.oauth_state.OAuthStateService``."""

from turing.services.oauth_state import (  # noqa: F401
    OAuthStateClaims,
    OAuthStateService,
    build_oauth_state,
    parse_oauth_state,
)

__all__ = [
    "OAuthStateClaims",
    "OAuthStateService",
    "build_oauth_state",
    "parse_oauth_state",
]
