from __future__ import annotations

"""
Generic telephony connector contract (Phase 4.4.3).

Enterprise CTI adapters subclass ``TelephonyConnector`` and implement
``list_calls`` / ``get_recording``. Sync reuses MediaService + ExternalReference
+ the existing STT pipeline. No real-time streaming in this phase.
"""

import logging
from abc import abstractmethod
from typing import Any, ClassVar

from turing.connectors.base import BaseConnector, ConnectorSyncResult, MediaPullItem
from turing.connectors.definition import (
    ConnectorCategory,
    InstallationRequirementField,
    InstallationRequirements,
)
from turing.connectors.exceptions import ConnectorError, ConnectorSyncError
from turing.connectors.telephony.serializers import (
    DEFAULT_EXTERNAL_SYSTEM,
    EXTERNAL_TYPE_CALL,
    TelephonyCall,
    normalize_call as normalize_call_payload,
)
from turing.domain.enums import ConnectorAuthType, UseCase
from turing.services.external_reference import ExternalReferenceService
from turing.services.media import MediaService

logger = logging.getLogger(__name__)


class TelephonyConnector(BaseConnector):
    """
    Abstract telephony call-recording connector.

    Subclasses implement vendor discovery; this base owns normalization helpers
    and the MediaService ingest path:

    ``list_calls`` → ``MediaService.create_from_url`` →
    ``ExternalReference(telephony/call/<id>)`` → STT pipeline.
    """

    connector_type = "telephony"
    display_name = "Telephony"
    description = (
        "Ingest contact-center / telephony call recordings into Turing for "
        "transcription."
    )
    provider = "Turing"
    category = ConnectorCategory.TELEPHONY
    documentation_url = ""
    auth_type = ConnectorAuthType.API_KEY
    supports_oauth = False
    supports_refresh = False
    supports_revoke = False
    supported_sync_types: ClassVar[tuple[str, ...]] = ("calls",)
    # Host/UI install checklist — CTI adapters override with vendor fields.
    installation_requirements = InstallationRequirements(
        config_fields=(
            InstallationRequirementField(
                key="api_token",
                label="API token",
                secret=True,
                description="Vendor telephony API credential.",
                validation_message="API token is required.",
            ),
        ),
        messages=(
            "Provide telephony API credentials in installation config.",
            "Recording URLs must be reachable by the Turing media ingest path.",
        ),
    )
    # ExternalReference system key (CTI adapters may override, e.g. "genesys").
    external_system: ClassVar[str] = DEFAULT_EXTERNAL_SYSTEM

    def validate_config(self) -> None:
        """Default: no-op. CTI adapters should enforce vendor-required config."""
        return None

    def health_check(self) -> dict[str, Any]:
        """Default lightweight probe (no secrets). Override in CTI adapters."""
        return {"ok": True, "connector_type": self.connector_type}

    @abstractmethod
    def list_calls(self, **kwargs: Any) -> list[TelephonyCall]:
        """Discover call recordings available for ingest."""

    @abstractmethod
    def get_recording(self, call_id: str) -> TelephonyCall | None:
        """Fetch a single call recording descriptor by vendor call id."""

    def normalize_call(self, raw: dict[str, Any]) -> TelephonyCall | None:
        """Normalize a vendor payload into ``TelephonyCall`` (no secrets)."""
        return normalize_call_payload(
            raw,
            external_system=self.external_system or DEFAULT_EXTERNAL_SYSTEM,
        )

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        """Map normalized telephony calls to ``MediaPullItem`` descriptors."""
        calls = self.list_calls(**kwargs)
        items: list[MediaPullItem] = []
        for call in calls:
            if not call.recording_url:
                continue
            filename = f"{call.external_system}-{call.external_id}.mp3"
            items.append(
                MediaPullItem(
                    external_id=call.external_id,
                    source_url=call.recording_url,
                    filename=filename,
                    metadata=call.to_public_dict(),
                )
            )
        return items

    def sync(self) -> ConnectorSyncResult:
        """
        Ingest discovered call recordings via MediaService + ExternalReference.

        Idempotent on ``(organization, external_system, call, external_id)``.
        """
        try:
            items = self.pull_media()
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorSyncError(
                f"Telephony pull_media failed: {exc}"
            ) from exc

        org = self.installation.organization
        media_service = MediaService()
        refs = ExternalReferenceService()
        created_items: list[MediaPullItem] = []
        skipped = 0
        system = self.external_system or DEFAULT_EXTERNAL_SYSTEM

        for item in items:
            if not item.source_url:
                skipped += 1
                continue
            existing = refs.lookup(
                organization=org,
                external_system=system,
                external_type=EXTERNAL_TYPE_CALL,
                external_id=item.external_id,
            )
            if existing.filter(media__isnull=False).exists():
                skipped += 1
                continue
            try:
                meta = dict(item.metadata or {})
                asset = media_service.create_from_url(
                    url=item.source_url,
                    use_case=UseCase.CRM_CALL,
                    organization=org,
                    original_filename=item.filename
                    or f"{system}-{item.external_id}.mp3",
                    metadata={
                        "connector": system,
                        "connector_installation_id": str(self.installation.id),
                        "telephony": {
                            k: v
                            for k, v in meta.items()
                            if k
                            not in {
                                "api_token",
                                "token",
                                "secret",
                                "access_token",
                                "refresh_token",
                            }
                        },
                    },
                )
                refs.attach_to_media(
                    asset,
                    external_system=system,
                    external_type=EXTERNAL_TYPE_CALL,
                    external_id=item.external_id,
                    metadata={
                        "caller": meta.get("caller", ""),
                        "callee": meta.get("callee", ""),
                        "started_at": meta.get("started_at", ""),
                        "duration": meta.get("duration"),
                    },
                )
                created_items.append(item)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Telephony sync failed creating media for call %s",
                    item.external_id,
                )
                raise ConnectorSyncError(
                    f"Failed to ingest telephony call '{item.external_id}': {exc}"
                ) from exc

        return ConnectorSyncResult(
            records_processed=len(created_items),
            media_items=created_items,
            details={"skipped": skipped, "discovered": len(items)},
        )
