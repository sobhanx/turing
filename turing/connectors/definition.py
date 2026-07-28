from __future__ import annotations

"""Marketplace-ready connector metadata (Phase 4.4.2). Never includes secrets."""

from dataclasses import dataclass, field
from typing import Any, Sequence

from turing.domain.enums import ConnectorAuthType


class ConnectorCategory:
    """Common marketplace category labels (free-form strings also allowed)."""

    MEETINGS = "meetings"
    CRM = "crm"
    TELEPHONY = "telephony"
    OTHER = "other"


def split_scopes(scopes: str | Sequence[str] | None) -> tuple[str, ...]:
    """Normalize a space/comma-separated scope string or sequence."""
    if scopes is None:
        return ()
    if isinstance(scopes, str):
        return tuple(part for part in scopes.replace(",", " ").split() if part)
    return tuple(str(s).strip() for s in scopes if str(s).strip())


@dataclass(frozen=True)
class InstallationRequirementField:
    """
    One installation config field expected by the host UI.

    ``secret`` is a boolean flag only — never populate with real credential values.
    """

    key: str
    label: str = ""
    required: bool = True
    secret: bool = False
    description: str = ""
    validation_message: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label or self.key,
            "required": bool(self.required),
            "secret": bool(self.secret),
            "description": self.description or "",
            "validation_message": self.validation_message
            or (f"{self.label or self.key} is required." if self.required else ""),
        }


@dataclass(frozen=True)
class InstallationRequirements:
    """
    Generic install checklist for marketplace / product UI.

    Supports OAuth scopes, config field schemas, and human validation messages.
    """

    oauth_scopes: tuple[str, ...] = ()
    config_fields: tuple[InstallationRequirementField, ...] = ()
    messages: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "oauth_scopes": list(self.oauth_scopes),
            "config_fields": [f.to_public_dict() for f in self.config_fields],
            "messages": list(self.messages),
        }

    @classmethod
    def from_legacy(cls, value: Any) -> InstallationRequirements:
        """Accept structured requirements, legacy string checklists, or empty."""
        if value is None:
            return cls()
        if isinstance(value, InstallationRequirements):
            return value
        if isinstance(value, dict):
            fields = tuple(
                InstallationRequirementField(
                    key=str(item.get("key") or ""),
                    label=str(item.get("label") or ""),
                    required=bool(item.get("required", True)),
                    secret=bool(item.get("secret", False)),
                    description=str(item.get("description") or ""),
                    validation_message=str(item.get("validation_message") or ""),
                )
                for item in (value.get("config_fields") or [])
                if item and item.get("key")
            )
            return cls(
                oauth_scopes=split_scopes(value.get("oauth_scopes")),
                config_fields=fields,
                messages=tuple(str(m) for m in (value.get("messages") or []) if m),
            )
        if isinstance(value, (list, tuple)):
            if not value:
                return cls()
            if all(isinstance(item, InstallationRequirementField) for item in value):
                return cls(config_fields=tuple(value))
            # Legacy checklist strings → messages (no secrets).
            return cls(messages=tuple(str(item) for item in value if item))
        return cls()


@dataclass(frozen=True)
class ConnectorDefinition:
    """
    Public connector metadata for discovery / marketplace UIs.

    Never carries credentials, tokens, or secret values.
    """

    connector_type: str
    display_name: str
    description: str = ""
    provider: str = ""
    category: str = ConnectorCategory.OTHER
    documentation_url: str = ""
    icon_url: str = ""
    auth_type: str = ConnectorAuthType.API_KEY
    capabilities: dict[str, bool] = field(default_factory=dict)
    supported_sync_types: tuple[str, ...] = ("media",)
    required_scopes: tuple[str, ...] = ()
    installation_requirements: InstallationRequirements = field(
        default_factory=InstallationRequirements
    )

    def validate(self) -> None:
        """Raise ``ConnectorConfigurationError`` if metadata is incomplete."""
        from turing.connectors.exceptions import ConnectorConfigurationError

        if not (self.connector_type or "").strip():
            raise ConnectorConfigurationError(
                "Connector definition requires connector_type."
            )
        if not (self.display_name or "").strip():
            raise ConnectorConfigurationError(
                "Connector definition requires display_name."
            )
        if self.auth_type not in {
            ConnectorAuthType.API_KEY,
            ConnectorAuthType.OAUTH2,
        }:
            raise ConnectorConfigurationError(
                f"Unsupported auth_type '{self.auth_type}'."
            )
        for field_def in self.installation_requirements.config_fields:
            if not (field_def.key or "").strip():
                raise ConnectorConfigurationError(
                    "installation_requirements.config_fields entries need a key."
                )

    def to_catalog_dict(self) -> dict[str, Any]:
        """Marketplace catalog row (no secrets)."""
        caps = dict(self.capabilities or {})
        return {
            "connector_type": self.connector_type,
            "display_name": self.display_name,
            "provider": self.provider or "",
            "description": self.description or "",
            "category": self.category or ConnectorCategory.OTHER,
            "documentation_url": self.documentation_url or "",
            "icon_url": self.icon_url or "",
            "auth_type": self.auth_type,
            "capabilities": {
                "oauth": bool(caps.get("oauth", False)),
                "refresh": bool(caps.get("refresh", False)),
                "revoke": bool(caps.get("revoke", False)),
            },
            "supported_sync_types": list(self.supported_sync_types or ()),
            "required_scopes": list(self.required_scopes or ()),
            "installation_requirements": (
                self.installation_requirements.to_public_dict()
            ),
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Alias for full public definition payload."""
        return self.to_catalog_dict()
