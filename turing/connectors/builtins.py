from __future__ import annotations

"""Register built-in connectors (called from AppConfig.ready and tests)."""

from turing.connectors.google_meet.connector import GoogleMeetConnector
from turing.connectors.registry import ConnectorRegistry
from turing.connectors.salesforce.connector import SalesforceConnector
from turing.connectors.teams.connector import TeamsConnector
from turing.connectors.zoom.connector import ZoomConnector


def register_builtin_connectors() -> None:
    """Idempotently register shipped connectors."""
    if "zoom" not in ConnectorRegistry.types():
        ConnectorRegistry.register(ZoomConnector)
    if "teams" not in ConnectorRegistry.types():
        ConnectorRegistry.register(TeamsConnector)
    if "google_meet" not in ConnectorRegistry.types():
        ConnectorRegistry.register(GoogleMeetConnector)
    if "salesforce" not in ConnectorRegistry.types():
        ConnectorRegistry.register(SalesforceConnector)
