from __future__ import annotations

from typing import Any, Sequence, Type

from turing.connectors.base import BaseConnector
from turing.connectors.definition import ConnectorDefinition, split_scopes
from turing.connectors.exceptions import ConnectorConfigurationError, ConnectorNotFoundError


class ConnectorRegistry:
    """Strategy registry for connector implementations (no vendor hardcoding)."""

    _connectors: dict[str, Type[BaseConnector]] = {}

    @classmethod
    def register(cls, connector_cls: Type[BaseConnector]) -> Type[BaseConnector]:
        connector_type = getattr(connector_cls, "connector_type", None) or ""
        if not connector_type:
            raise ConnectorConfigurationError(
                "Connector class must define a non-empty 'connector_type'."
            )
        # Validate marketplace metadata at registration time.
        connector_cls.definition()
        cls._connectors[connector_type] = connector_cls
        return connector_cls

    @classmethod
    def get(cls, connector_type: str) -> Type[BaseConnector]:
        connector_cls = cls._connectors.get(connector_type)
        if connector_cls is None:
            raise ConnectorNotFoundError(f"Unknown connector type '{connector_type}'.")
        return connector_cls

    @classmethod
    def get_definition(cls, connector_type: str) -> ConnectorDefinition:
        """Return marketplace metadata for a registered connector type."""
        return cls.get(connector_type).definition()

    @classmethod
    def create(cls, installation) -> BaseConnector:
        """Instantiate the connector class bound to ``installation``."""
        connector_cls = cls.get(installation.connector_type)
        return connector_cls(installation)

    @classmethod
    def types(cls) -> list[str]:
        return sorted(cls._connectors.keys())

    @classmethod
    def list_available(cls) -> list[dict]:
        """Catalog of registered connector types for Admin / APIs (no secrets)."""
        return [cls.get_definition(code).to_catalog_dict() for code in cls.types()]

    @classmethod
    def list_definitions(cls) -> list[ConnectorDefinition]:
        """Return ConnectorDefinition objects for all registered types."""
        return [cls.get_definition(code) for code in cls.types()]

    @classmethod
    def validate_installation_requirements(
        cls,
        connector_type: str,
        config: dict[str, Any] | None = None,
        *,
        scopes_granted: Sequence[str] | None = None,
    ) -> None:
        """
        Validate install config / granted scopes against connector definition.

        Raises ``ConnectorConfigurationError`` with field validation messages.
        Never logs or echoes secret values.
        """
        definition = cls.get_definition(connector_type)
        requirements = definition.installation_requirements
        cfg = dict(config or {})

        for field_def in requirements.config_fields:
            if not field_def.required:
                continue
            value = cfg.get(field_def.key)
            if value is None or (isinstance(value, str) and not value.strip()):
                message = field_def.validation_message or (
                    f"{field_def.label or field_def.key} is required."
                )
                raise ConnectorConfigurationError(message)

        required_scopes = tuple(definition.required_scopes) or tuple(
            requirements.oauth_scopes
        )
        if scopes_granted is not None and required_scopes:
            granted = set(split_scopes(scopes_granted))
            missing = [scope for scope in required_scopes if scope not in granted]
            if missing:
                raise ConnectorConfigurationError(
                    "Missing required OAuth scopes: " + ", ".join(missing)
                )

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (tests)."""
        cls._connectors.clear()
