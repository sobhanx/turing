from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from turing.domain.enums import ConnectorAuthType, ConnectorInstallationStatus
from turing.domain.exceptions import ValidationError
from turing.models import ConnectorCredential, ConnectorInstallation
from turing.services.credential_encryption import CredentialEncryptionService

logger = logging.getLogger(__name__)

_NON_SYNCABLE = frozenset(
    {
        ConnectorInstallationStatus.PENDING,
        ConnectorInstallationStatus.EXPIRED,
        ConnectorInstallationStatus.REVOKED,
    }
)


class ConnectorInstallationService:
    """Installation lifecycle + encrypted credential storage (Phase 4.3.5)."""

    def __init__(self, encryption: CredentialEncryptionService | None = None) -> None:
        self._encryption = encryption or CredentialEncryptionService()

    def activate(self, installation: ConnectorInstallation) -> ConnectorInstallation:
        """Mark installation ACTIVE (authorized / ready to sync)."""
        if installation.status == ConnectorInstallationStatus.REVOKED:
            raise ValidationError("Cannot activate a revoked connector installation.")
        installation.status = ConnectorInstallationStatus.ACTIVE
        installation.save(update_fields=["status", "updated_at"])
        logger.info(
            "Connector installation activated installation_id=%s connector_type=%s",
            installation.id,
            installation.connector_type,
        )
        return installation

    def expire(self, installation: ConnectorInstallation) -> ConnectorInstallation:
        """Mark installation EXPIRED (tokens no longer valid)."""
        if installation.status == ConnectorInstallationStatus.REVOKED:
            raise ValidationError("Cannot expire a revoked connector installation.")
        installation.status = ConnectorInstallationStatus.EXPIRED
        installation.save(update_fields=["status", "updated_at"])
        logger.info(
            "Connector installation expired installation_id=%s connector_type=%s",
            installation.id,
            installation.connector_type,
        )
        return installation

    def revoke(self, installation: ConnectorInstallation) -> ConnectorInstallation:
        """
        Revoke installation: clear stored tokens and set status REVOKED.

        Invokes connector ``revoke_credentials()`` when possible. Never logs secrets.
        """
        with transaction.atomic():
            installation = (
                ConnectorInstallation.objects.select_for_update()
                .select_related("organization")
                .get(pk=installation.pk)
            )
            try:
                from turing.connectors.registry import ConnectorRegistry

                connector = ConnectorRegistry.create(installation)
                connector.revoke_credentials()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Connector revoke hook failed installation_id=%s connector_type=%s",
                    installation.id,
                    installation.connector_type,
                )

            cred = ConnectorCredential.objects.filter(
                connector_installation_id=installation.pk
            ).first()
            if cred is not None:
                cred.encrypted_access_token = ""
                cred.encrypted_refresh_token = ""
                cred.expires_at = None
                meta = dict(cred.metadata or {})
                meta["revoked_at"] = timezone.now().isoformat()
                cred.metadata = meta
                cred.save(
                    update_fields=[
                        "encrypted_access_token",
                        "encrypted_refresh_token",
                        "expires_at",
                        "metadata",
                        "updated_at",
                    ]
                )

            installation.status = ConnectorInstallationStatus.REVOKED
            installation.save(update_fields=["status", "updated_at"])

        logger.info(
            "Connector installation revoked installation_id=%s connector_type=%s",
            installation.id,
            installation.connector_type,
        )
        return installation

    def store_credentials(
        self,
        installation: ConnectorInstallation,
        *,
        access_token: str,
        refresh_token: str = "",
        expires_at: datetime | None = None,
        auth_type: str = ConnectorAuthType.OAUTH2,
        metadata: dict[str, Any] | None = None,
    ) -> ConnectorCredential:
        """
        Encrypt and upsert the single credential row for an installation.

        Callers must not log ``access_token`` / ``refresh_token``.
        """
        if not (access_token or "").strip():
            raise ValidationError("access_token is required.")

        enc_access = self._encryption.encrypt(access_token.strip())
        enc_refresh = self._encryption.encrypt((refresh_token or "").strip())
        meta = dict(metadata or {})

        with transaction.atomic():
            cred, _created = ConnectorCredential.objects.update_or_create(
                connector_installation=installation,
                defaults={
                    "organization": installation.organization,
                    "auth_type": auth_type,
                    "encrypted_access_token": enc_access,
                    "encrypted_refresh_token": enc_refresh,
                    "expires_at": expires_at,
                    "metadata": meta,
                },
            )
        logger.info(
            "Connector credentials stored installation_id=%s connector_type=%s "
            "auth_type=%s has_refresh=%s",
            installation.id,
            installation.connector_type,
            auth_type,
            bool(enc_refresh),
        )
        return cred

    def auth_status(self, installation: ConnectorInstallation) -> dict[str, Any]:
        """Public auth summary — never includes tokens or ciphertext."""
        auth_type = self._resolve_auth_type(installation)
        cred = ConnectorCredential.objects.filter(
            connector_installation_id=installation.pk
        ).first()
        has_credentials = False
        expires_at = None
        if auth_type == ConnectorAuthType.API_KEY:
            # Config-based secrets: report presence without exposing values.
            cfg = installation.config or {}
            has_credentials = any(
                bool(cfg.get(k))
                for k in ("api_token", "api_key", "token", "password")
            )
        elif cred is not None:
            has_credentials = cred.has_access_token()
            expires_at = cred.expires_at

        is_expired = installation.status == ConnectorInstallationStatus.EXPIRED
        if expires_at is not None and expires_at <= timezone.now():
            is_expired = True

        return {
            "auth_type": auth_type,
            "has_credentials": has_credentials,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "is_expired": is_expired,
            "status": installation.status,
        }

    def _resolve_auth_type(self, installation: ConnectorInstallation) -> str:
        try:
            from turing.connectors.registry import ConnectorRegistry

            cls = ConnectorRegistry.get(installation.connector_type)
            return getattr(cls, "auth_type", ConnectorAuthType.API_KEY) or (
                ConnectorAuthType.API_KEY
            )
        except Exception:  # noqa: BLE001
            cred = ConnectorCredential.objects.filter(
                connector_installation_id=installation.pk
            ).first()
            if cred is not None:
                return cred.auth_type
            return ConnectorAuthType.API_KEY

    @staticmethod
    def is_syncable(installation: ConnectorInstallation) -> bool:
        return installation.status not in _NON_SYNCABLE
