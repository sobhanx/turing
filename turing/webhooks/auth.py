"""Webhook authentication helpers."""

from __future__ import annotations

import hmac
from typing import Mapping

from turing.conf import get_turing_settings


def extract_bearer_token(authorization_header: str) -> str:
    """Parse ``Authorization: Bearer <token>``."""
    if not authorization_header:
        return ""
    parts = authorization_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def verify_speechmatics_webhook_bearer(headers: Mapping[str, str]) -> bool:
    """
    Constant-time Bearer comparison against configured webhook secret.

    Returns False when secret is unset (endpoint disabled).
    """
    settings = get_turing_settings()
    expected = (settings.speechmatics_webhook_secret or "").strip()
    if not expected:
        return False
    auth = ""
    for key, value in headers.items():
        if key.lower() == "authorization":
            auth = value or ""
            break
    token = extract_bearer_token(auth)
    if not token:
        return False
    return hmac.compare_digest(token, expected)
