from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from turing.domain.enums import (
    ConnectorAuthType,
    ConnectorInstallationStatus,
    ConnectorSyncJobStatus,
)
from turing.models.media import UUIDModel

# Config keys (case-insensitive substring) redacted in Admin / public views.
_SENSITIVE_CONFIG_FRAGMENTS = (
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "private",
    "credential",
)


def redact_connector_config(config: dict | None) -> dict:
    """Return a copy of config with sensitive values masked."""
    raw = dict(config or {})
    redacted: dict = {}
    for key, value in raw.items():
        key_l = str(key).lower()
        if any(fragment in key_l for fragment in _SENSITIVE_CONFIG_FRAGMENTS):
            redacted[key] = "********" if value not in (None, "") else ""
        elif isinstance(value, dict):
            redacted[key] = redact_connector_config(value)
        else:
            redacted[key] = value
    return redacted


class ConnectorInstallation(UUIDModel):
    """
    Org-scoped installation of a registered connector type.

    ``config`` may hold non-secret settings and api_key material; OAuth tokens
    belong on ``ConnectorCredential``. Never expose unfiltered secrets in API.
    """

    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="connector_installations",
        db_index=True,
    )
    connector_type = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Registry key (e.g. zoom, crm). Must match a registered connector.",
    )
    name = models.CharField(max_length=128)
    status = models.CharField(
        max_length=16,
        choices=ConnectorInstallationStatus.choices,
        default=ConnectorInstallationStatus.PENDING,
        db_index=True,
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Connector-specific settings (secrets filtered in Admin/API).",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Connector installation"
        verbose_name_plural = "Connector installations"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="turing_connector_org_name_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "connector_type"],
                name="turing_conn_org_type",
            ),
            models.Index(
                fields=["organization", "status"],
                name="turing_conn_org_status",
            ),
        ]

    def __str__(self) -> str:
        return f"ConnectorInstallation({self.connector_type}:{self.name} [{self.status}])"

    def public_config(self) -> dict:
        return redact_connector_config(self.config)

    def clean(self) -> None:
        super().clean()
        if not (self.connector_type or "").strip():
            raise ValidationError({"connector_type": "Connector type is required."})
        self.connector_type = self.connector_type.strip()
        if not (self.name or "").strip():
            raise ValidationError({"name": "Name is required."})
        self.name = self.name.strip()
        if self.config is None:
            self.config = {}
        if not isinstance(self.config, dict):
            raise ValidationError({"config": "Config must be a JSON object."})


class ConnectorCredential(UUIDModel):
    """
    Org-scoped encrypted credentials for a connector installation.

    One credential row per installation. Token fields store ciphertext only;
    decrypt via ``CredentialEncryptionService`` during connector execution.
    """

    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.PROTECT,
        related_name="connector_credentials",
        db_index=True,
    )
    connector_installation = models.OneToOneField(
        ConnectorInstallation,
        on_delete=models.CASCADE,
        related_name="credential",
    )
    auth_type = models.CharField(
        max_length=16,
        choices=ConnectorAuthType.choices,
        default=ConnectorAuthType.OAUTH2,
        db_index=True,
    )
    # Ciphertext (Fernet). Never serialize or log plaintext.
    encrypted_access_token = models.TextField(blank=True, default="")
    encrypted_refresh_token = models.TextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Connector credential"
        verbose_name_plural = "Connector credentials"
        indexes = [
            models.Index(
                fields=["organization", "auth_type"],
                name="turing_conncred_org_auth",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"ConnectorCredential({self.auth_type} "
            f"installation={self.connector_installation_id})"
        )

    def has_access_token(self) -> bool:
        return bool(self.encrypted_access_token)

    def has_refresh_token(self) -> bool:
        return bool(self.encrypted_refresh_token)

    def clean(self) -> None:
        super().clean()
        if self.metadata is None:
            self.metadata = {}
        if not isinstance(self.metadata, dict):
            raise ValidationError({"metadata": "Metadata must be a JSON object."})
        if (
            self.connector_installation_id
            and self.organization_id
            and self.connector_installation.organization_id != self.organization_id
        ):
            raise ValidationError(
                {"organization": "Must match the installation organization."}
            )


class ConnectorSyncJob(UUIDModel):
    """One sync execution for a connector installation."""

    installation = models.ForeignKey(
        ConnectorInstallation,
        on_delete=models.CASCADE,
        related_name="sync_jobs",
    )
    status = models.CharField(
        max_length=16,
        choices=ConnectorSyncJobStatus.choices,
        default=ConnectorSyncJobStatus.PENDING,
        db_index=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    records_processed = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Connector sync job"
        verbose_name_plural = "Connector sync jobs"
        indexes = [
            models.Index(
                fields=["installation", "-created_at"],
                name="turing_connsync_inst",
            ),
            models.Index(fields=["status", "-created_at"], name="turing_connsync_status"),
        ]

    def __str__(self) -> str:
        return f"ConnectorSyncJob({self.status} installation={self.installation_id})"
