from __future__ import annotations

"""Signed OAuth state for connector installation authorization."""

from django.core import signing

from turing.connectors.exceptions import ConnectorConfigurationError

_STATE_SALT = "turing.connector.oauth.state"
_STATE_MAX_AGE_SECONDS = 600


def build_oauth_state(*, installation_id: str, organization_id: int | str) -> str:
    """Create a signed state binding installation + organization."""
    signer = signing.TimestampSigner(salt=_STATE_SALT)
    return signer.sign(f"{installation_id}:{organization_id}")


def parse_oauth_state(state: str, *, max_age: int = _STATE_MAX_AGE_SECONDS) -> tuple[str, str]:
    """
    Validate and unpack OAuth state.

    Returns ``(installation_id, organization_id)``.
    """
    if not (state or "").strip():
        raise ConnectorConfigurationError("OAuth state is required.")
    signer = signing.TimestampSigner(salt=_STATE_SALT)
    try:
        raw = signer.unsign(state.strip(), max_age=max_age)
    except signing.SignatureExpired as exc:
        raise ConnectorConfigurationError("OAuth state has expired.") from exc
    except signing.BadSignature as exc:
        raise ConnectorConfigurationError("OAuth state is invalid.") from exc
    parts = str(raw).split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ConnectorConfigurationError("OAuth state is malformed.")
    return parts[0], parts[1]
