from __future__ import annotations

"""Register built-in connectors (called from AppConfig.ready and tests)."""

from turing.connectors.registry import ConnectorRegistry
from turing.connectors.zoom.connector import ZoomConnector


def register_builtin_connectors() -> None:
    """Idempotently register shipped connectors."""
    if "zoom" not in ConnectorRegistry.types():
        ConnectorRegistry.register(ZoomConnector)
