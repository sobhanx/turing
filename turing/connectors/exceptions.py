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


class AuthenticationError(ConnectorError):
    """
    Auth material is invalid or cannot be refreshed.

    Sync mapping: expire the installation (do not retry as-is).
    """

    code = "connector_authentication_error"


class TemporaryConnectorError(ConnectorError):
    """
    Transient remote / network failure.

    Sync mapping: retry the sync job.
    """

    code = "connector_temporary_error"


class PermanentConnectorError(ConnectorError):
    """
    Non-retryable connector failure.

    Sync mapping: mark sync job failed.
    """

    code = "connector_permanent_error"
