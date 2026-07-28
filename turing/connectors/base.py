from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MediaPullItem:
    """
    Descriptor for media discovered by a connector.

    Host/provider adapters map these into ``MediaService`` create paths later.
    No transcript text or secrets belong here.
    """

    external_id: str
    source_url: str = ""
    filename: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorSyncResult:
    """Outcome of ``BaseConnector.sync()``."""

    records_processed: int = 0
    media_items: list[MediaPullItem] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class BaseConnector(ABC):
    """
    Vendor-agnostic connector contract.

    Implementations register with ``ConnectorRegistry`` under ``connector_type``.
    Core sync services depend only on this interface.
    """

    connector_type: str = ""
    display_name: str = ""

    def __init__(self, installation) -> None:
        self.installation = installation
        self.config: dict[str, Any] = dict(installation.config or {})

    @property
    def name(self) -> str:
        """Human-readable connector name (defaults to display_name / type)."""
        return self.display_name or self.connector_type or self.installation.name

    @abstractmethod
    def validate_config(self) -> None:
        """Raise ``ConnectorConfigurationError`` if installation config is invalid."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """
        Probe remote connectivity / credentials.

        Returns a small status dict (e.g. ``{"ok": True}``). Must not include secrets.
        """

    @abstractmethod
    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        """Discover remote media candidates without persisting Turing media yet."""

    @abstractmethod
    def sync(self) -> ConnectorSyncResult:
        """
        Run a full sync pass for this installation.

        Typically validates config, optionally health-checks, pulls media
        descriptors, and returns counts for the sync job record.
        """
