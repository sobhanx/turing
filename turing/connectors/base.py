from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Sequence

from turing.connectors.definition import (
    ConnectorCategory,
    ConnectorDefinition,
    InstallationRequirements,
    split_scopes,
)
from turing.domain.enums import ConnectorAuthType


@dataclass(frozen=True)
class MediaPullItem:
    """
    Descriptor for media discovered by a connector.

    Host/provider adapters map these into ``MediaService`` create paths later.
    No transcript text or secrets belong here.
    """

    external_id: str
    source_url: str = ""
    filename: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorSyncResult:
    """Outcome of ``BaseConnector.sync()``."""

    records_processed: int = 0
    media_items: list[MediaPullItem] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class BaseConnector(ABC):
    """
    Vendor-agnostic connector contract.

    Implementations register with ``ConnectorRegistry`` under ``connector_type``.
    Core sync services depend only on this interface.

    Auth:
    - ``api_key`` — credentials live in installation ``config`` (existing)
    - ``oauth2`` — tokens live in org-scoped ``ConnectorCredential`` (encrypted)

    Marketplace metadata lives on class attributes and is exposed via
    ``definition()`` / ``ConnectorRegistry.get_definition()`` (never secrets).
    """

    connector_type: str = ""
    display_name: str = ""
    description: ClassVar[str] = ""
    provider: ClassVar[str] = ""
    category: ClassVar[str] = ConnectorCategory.OTHER
    documentation_url: ClassVar[str] = ""
    icon_url: ClassVar[str] = ""
    auth_type: str = ConnectorAuthType.API_KEY

    # Capability metadata (catalog / hosts) — no secrets.
    supports_oauth: bool = False
    supports_refresh: bool = False
    supports_revoke: bool = False
    supported_sync_types: ClassVar[Sequence[str]] = ("media",)
    required_scopes: ClassVar[Sequence[str]] = ()
    # Structured InstallationRequirements, legacy string checklist, or empty.
    installation_requirements: ClassVar[Any] = ()

    def __init__(self, installation) -> None:
        self.installation = installation
        self.config: dict[str, Any] = dict(installation.config or {})

    @property
    def name(self) -> str:
        """Human-readable connector name (defaults to display_name / type)."""
        return self.display_name or self.connector_type or self.installation.name

    @classmethod
    def definition(cls) -> ConnectorDefinition:
        """Build public marketplace metadata for this connector class."""
        auth_type = getattr(cls, "auth_type", ConnectorAuthType.API_KEY) or (
            ConnectorAuthType.API_KEY
        )
        supports_oauth = bool(cls.supports_oauth) or auth_type == ConnectorAuthType.OAUTH2
        supports_refresh = bool(cls.supports_refresh) or supports_oauth
        supports_revoke = bool(cls.supports_revoke) or supports_oauth
        scopes = split_scopes(getattr(cls, "required_scopes", None) or ())
        requirements = InstallationRequirements.from_legacy(
            getattr(cls, "installation_requirements", None)
        )
        if not requirements.oauth_scopes and scopes:
            requirements = InstallationRequirements(
                oauth_scopes=scopes,
                config_fields=requirements.config_fields,
                messages=requirements.messages,
            )
        if (
            not requirements.oauth_scopes
            and not requirements.config_fields
            and not requirements.messages
        ):
            if auth_type == ConnectorAuthType.OAUTH2:
                requirements = InstallationRequirements(
                    oauth_scopes=scopes,
                    messages=(
                        "oauth_client_credentials",
                        "oauth_redirect_uri",
                    ),
                )
            else:
                requirements = InstallationRequirements(
                    messages=("api_credentials",),
                )

        definition = ConnectorDefinition(
            connector_type=cls.connector_type or "",
            display_name=getattr(cls, "display_name", "") or cls.connector_type or "",
            description=str(getattr(cls, "description", "") or ""),
            provider=str(getattr(cls, "provider", "") or ""),
            category=str(getattr(cls, "category", "") or ConnectorCategory.OTHER),
            documentation_url=str(getattr(cls, "documentation_url", "") or ""),
            icon_url=str(getattr(cls, "icon_url", "") or ""),
            auth_type=auth_type,
            capabilities={
                "oauth": supports_oauth,
                "refresh": supports_refresh,
                "revoke": supports_revoke,
            },
            supported_sync_types=tuple(cls.supported_sync_types or ()),
            required_scopes=scopes or requirements.oauth_scopes,
            installation_requirements=requirements,
        )
        definition.validate()
        return definition

    @classmethod
    def capability_metadata(cls) -> dict[str, Any]:
        """Public capability summary for catalog APIs (never includes secrets)."""
        definition = cls.definition()
        catalog = definition.to_catalog_dict()
        return {
            "auth_type": catalog["auth_type"],
            "supports_oauth": catalog["capabilities"]["oauth"],
            "supports_refresh": catalog["capabilities"]["refresh"],
            "supports_revoke": catalog["capabilities"]["revoke"],
            "capabilities": catalog["capabilities"],
            "supported_sync_types": catalog["supported_sync_types"],
            "required_scopes": catalog["required_scopes"],
            "installation_requirements": catalog["installation_requirements"],
            "provider": catalog["provider"],
            "description": catalog["description"],
            "category": catalog["category"],
            "documentation_url": catalog["documentation_url"],
            "icon_url": catalog["icon_url"],
        }

    @abstractmethod
    def validate_config(self) -> None:
        """Raise ``ConnectorConfigurationError`` if installation config is invalid."""

    def validate_credentials(self) -> None:
        """
        Validate auth material for this installation.

        Default: ``api_key`` → ``validate_config()``; ``oauth2`` → require a
        decryptable access token on the linked credential. Must not log secrets.
        """
        from turing.connectors.exceptions import ConnectorConfigurationError

        if self.auth_type == ConnectorAuthType.API_KEY:
            self.validate_config()
            return

        token = self._decrypt_access_token()
        if not token:
            raise ConnectorConfigurationError(
                "OAuth access token is missing. Complete authorization first."
            )

    def refresh_credentials(self) -> None:
        """
        Refresh OAuth tokens (or no-op for api_key).

        Override on oauth2 connectors. Default raises for oauth2.
        """
        from turing.connectors.exceptions import ConnectorError

        if self.auth_type == ConnectorAuthType.API_KEY:
            return
        raise ConnectorError(
            f"Credential refresh is not implemented for '{self.connector_type}'.",
            code="connector_refresh_unsupported",
        )

    def revoke_credentials(self) -> None:
        """
        Provider-side revoke hook (optional).

        Token clearing is handled by ``ConnectorInstallationService.revoke``.
        Override to call remote revoke APIs. Must not log secrets.
        """
        return None

    def _credential(self):
        """Return the linked ``ConnectorCredential`` or None (no decrypt)."""
        from turing.models import ConnectorCredential

        pk = getattr(self.installation, "pk", None)
        if not pk:
            return None
        # Always query — avoid stale reverse OneToOne cache after token rotation.
        return ConnectorCredential.objects.filter(
            connector_installation_id=pk
        ).first()

    def _decrypt_access_token(self) -> str:
        """Decrypt access token for connector execution only. Never log."""
        from turing.services.credential_encryption import CredentialEncryptionService

        cred = self._credential()
        if cred is None or not cred.encrypted_access_token:
            return ""
        return CredentialEncryptionService().decrypt(cred.encrypted_access_token)

    def _decrypt_refresh_token(self) -> str:
        """Decrypt refresh token for connector execution only. Never log."""
        from turing.services.credential_encryption import CredentialEncryptionService

        cred = self._credential()
        if cred is None or not cred.encrypted_refresh_token:
            return ""
        return CredentialEncryptionService().decrypt(cred.encrypted_refresh_token)

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """
        Probe remote connectivity / credentials.

        Returns a small status dict (e.g. ``{"ok": True}``). Must not include secrets.
        """

    @abstractmethod
    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        """Discover remote media candidates without persisting Turing media yet."""

    @abstractmethod
    def sync(self) -> ConnectorSyncResult:
        """
        Run a full sync pass for this installation.

        Typically validates config, optionally health-checks, pulls media
        descriptors, and returns counts for the sync job record.
        """
