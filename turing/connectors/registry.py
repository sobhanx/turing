from __future__ import annotations

from typing import Type

from turing.connectors.base import BaseConnector
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
        cls._connectors[connector_type] = connector_cls
        return connector_cls

    @classmethod
    def get(cls, connector_type: str) -> Type[BaseConnector]:
        connector_cls = cls._connectors.get(connector_type)
        if connector_cls is None:
            raise ConnectorNotFoundError(f"Unknown connector type '{connector_type}'.")
        return connector_cls

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
        rows: list[dict] = []
        for code in cls.types():
            connector_cls = cls._connectors[code]
            caps = connector_cls.capability_metadata()
            rows.append(
                {
                    "connector_type": code,
                    "display_name": getattr(connector_cls, "display_name", "") or code,
                    **caps,
                }
            )
        return rows

    @classmethod
    def clear(cls) -> None:
        """Remove all registrations (tests)."""
        cls._connectors.clear()
