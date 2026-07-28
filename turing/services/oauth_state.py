from __future__ import annotations

"""Signed OAuth state service with replay protection (Phase 4.3.7)."""

import logging
import secrets
from dataclasses import dataclass

from django.core import signing
from django.core.cache import cache

from turing.connectors.exceptions import ConnectorConfigurationError

logger = logging.getLogger(__name__)

_STATE_SALT = "turing.connector.oauth.state"
_STATE_MAX_AGE_SECONDS = 600
_USED_KEY_PREFIX = "turing:oauth:state:used:"


@dataclass(frozen=True)
class OAuthStateClaims:
    """Validated OAuth state payload (no secrets)."""

    installation_id: str
    organization_id: str
    connector_type: str
    nonce: str


class OAuthStateService:
    """
    Generate and validate signed OAuth ``state`` values.

    Binds organization + installation + connector type. Consumes a nonce so the
    same state cannot be replayed within the TTL window.
    """

    def __init__(self, *, max_age_seconds: int = _STATE_MAX_AGE_SECONDS) -> None:
        self.max_age_seconds = max_age_seconds

    def generate(
        self,
        *,
        installation_id: str,
        organization_id: int | str,
        connector_type: str,
    ) -> str:
        installation_id = str(installation_id or "").strip()
        organization_id = str(organization_id or "").strip()
        connector_type = str(connector_type or "").strip()
        if not installation_id or not organization_id or not connector_type:
            raise ConnectorConfigurationError(
                "OAuth state requires installation, organization, and connector."
            )
        nonce = secrets.token_urlsafe(16)
        payload = f"{installation_id}:{organization_id}:{connector_type}:{nonce}"
        return signing.TimestampSigner(salt=_STATE_SALT).sign(payload)

    def validate(
        self,
        state: str,
        *,
        expected_connector_type: str | None = None,
        consume: bool = True,
    ) -> OAuthStateClaims:
        """
        Validate signature, expiry, and optional connector binding.

        When ``consume=True`` (default), marks the nonce used to prevent replay.
        """
        if not (state or "").strip():
            raise ConnectorConfigurationError("OAuth state is required.")

        signer = signing.TimestampSigner(salt=_STATE_SALT)
        try:
            raw = signer.unsign(state.strip(), max_age=self.max_age_seconds)
        except signing.SignatureExpired as exc:
            raise ConnectorConfigurationError("OAuth state has expired.") from exc
        except signing.BadSignature as exc:
            raise ConnectorConfigurationError("OAuth state is invalid.") from exc

        parts = str(raw).split(":")
        if len(parts) != 4 or not all(parts):
            raise ConnectorConfigurationError("OAuth state is malformed.")

        installation_id, organization_id, connector_type, nonce = parts
        if expected_connector_type and connector_type != expected_connector_type:
            raise ConnectorConfigurationError(
                "OAuth state connector type does not match callback."
            )

        if consume:
            self._consume_nonce(nonce)

        return OAuthStateClaims(
            installation_id=installation_id,
            organization_id=organization_id,
            connector_type=connector_type,
            nonce=nonce,
        )

    def _consume_nonce(self, nonce: str) -> None:
        key = f"{_USED_KEY_PREFIX}{nonce}"
        # cache.add is atomic: False means the nonce was already consumed.
        if not cache.add(key, "1", timeout=self.max_age_seconds):
            logger.warning("OAuth state replay rejected")
            raise ConnectorConfigurationError(
                "OAuth state has already been used.",
                code="oauth_state_replay",
            )


# Backward-compatible module helpers (delegate to the service).
def build_oauth_state(
    *,
    installation_id: str,
    organization_id: int | str,
    connector_type: str = "",
) -> str:
    if not connector_type:
        raise ConnectorConfigurationError(
            "OAuth state requires connector_type (use OAuthStateService.generate)."
        )
    return OAuthStateService().generate(
        installation_id=installation_id,
        organization_id=organization_id,
        connector_type=connector_type,
    )


def parse_oauth_state(
    state: str,
    *,
    max_age: int = _STATE_MAX_AGE_SECONDS,
    expected_connector_type: str | None = None,
    consume: bool = True,
) -> tuple[str, str]:
    """
    Validate state and return ``(installation_id, organization_id)``.

    Prefer ``OAuthStateService.validate`` for full claims (includes connector).
    """
    claims = OAuthStateService(max_age_seconds=max_age).validate(
        state,
        expected_connector_type=expected_connector_type,
        consume=consume,
    )
    return claims.installation_id, claims.organization_id
