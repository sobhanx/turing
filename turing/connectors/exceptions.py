from __future__ import annotations

from turing.domain.exceptions import TuringError


class ConnectorError(TuringError):
    """Base error for connector framework failures."""

    code = "connector_error"


class ConnectorNotFoundError(ConnectorError):
    code = "connector_not_found"


class ConnectorConfigurationError(ConnectorError):
    code = "connector_configuration_error"


class ConnectorHealthError(ConnectorError):
    code = "connector_health_error"


class ConnectorSyncError(ConnectorError):
    code = "connector_sync_error"
