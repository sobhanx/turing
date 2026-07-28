from __future__ import annotations

"""
Generic connector framework (Phase 4.3.1).

Connectors adapt external systems (meetings, CRM, telephony) into Turing media
and sync workflows without coupling core services to a specific vendor.
"""

from turing.connectors.base import BaseConnector, ConnectorSyncResult, MediaPullItem
from turing.connectors.exceptions import (
    AuthenticationError,
    ConnectorConfigurationError,
    ConnectorError,
    ConnectorHealthError,
    ConnectorNotFoundError,
    ConnectorSyncError,
    PermanentConnectorError,
    TemporaryConnectorError,
)
from turing.connectors.registry import ConnectorRegistry

__all__ = [
    "AuthenticationError",
    "BaseConnector",
    "ConnectorConfigurationError",
    "ConnectorError",
    "ConnectorHealthError",
    "ConnectorNotFoundError",
    "ConnectorRegistry",
    "ConnectorSyncError",
    "ConnectorSyncResult",
    "MediaPullItem",
    "PermanentConnectorError",
    "TemporaryConnectorError",
]
