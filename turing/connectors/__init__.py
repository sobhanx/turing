from __future__ import annotations

"""
Generic connector framework (Phase 4.3.1).

Connectors adapt external systems (meetings, CRM, telephony) into Turing media
and sync workflows without coupling core services to a specific vendor.
"""

from turing.connectors.base import BaseConnector, ConnectorSyncResult, MediaPullItem
from turing.connectors.definition import (
    ConnectorCategory,
    ConnectorDefinition,
    InstallationRequirementField,
    InstallationRequirements,
)
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
from turing.connectors.telephony import (
    TelephonyCall,
    TelephonyConnector,
    normalize_call,
)

__all__ = [
    "AuthenticationError",
    "BaseConnector",
    "ConnectorCategory",
    "ConnectorConfigurationError",
    "ConnectorDefinition",
    "ConnectorError",
    "ConnectorHealthError",
    "ConnectorNotFoundError",
    "ConnectorRegistry",
    "ConnectorSyncError",
    "ConnectorSyncResult",
    "InstallationRequirementField",
    "InstallationRequirements",
    "MediaPullItem",
    "PermanentConnectorError",
    "TelephonyCall",
    "TelephonyConnector",
    "TemporaryConnectorError",
    "normalize_call",
]
