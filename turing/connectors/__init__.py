from __future__ import annotations

"""
Generic connector framework (Phase 4.3.1).

Connectors adapt external systems (meetings, CRM, telephony) into Turing media
and sync workflows without coupling core services to a specific vendor.
"""

from turing.connectors.base import BaseConnector, ConnectorSyncResult, MediaPullItem
from turing.connectors.exceptions import (
    ConnectorConfigurationError,
    ConnectorError,
    ConnectorHealthError,
    ConnectorNotFoundError,
    ConnectorSyncError,
)
from turing.connectors.registry import ConnectorRegistry

__all__ = [
    "BaseConnector",
    "ConnectorConfigurationError",
    "ConnectorError",
    "ConnectorHealthError",
    "ConnectorNotFoundError",
    "ConnectorRegistry",
    "ConnectorSyncError",
    "ConnectorSyncResult",
    "MediaPullItem",
]
