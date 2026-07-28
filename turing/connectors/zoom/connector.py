from __future__ import annotations

import logging
from typing import Any

from turing.connectors.base import BaseConnector, ConnectorSyncResult, MediaPullItem
from turing.connectors.exceptions import (
    ConnectorConfigurationError,
    ConnectorError,
    ConnectorHealthError,
    ConnectorSyncError,
)
from turing.connectors.zoom.client import ZoomClient
from turing.connectors.zoom.serializers import pick_primary_recording
from turing.domain.enums import UseCase
from turing.services.external_reference import ExternalReferenceService
from turing.services.media import MediaService

logger = logging.getLogger(__name__)

EXTERNAL_SYSTEM = "zoom"
EXTERNAL_TYPE = "meeting"


class ZoomConnector(BaseConnector):
    """
    Zoom Cloud Recording → Turing media connector.

    Sync creates media via ``MediaService.create_from_url`` and links
    ``ExternalReference(zoom/meeting/<recording_id>)`` for idempotency.
    """

    connector_type = "zoom"
    display_name = "Zoom"
    auth_type = "api_key"

    def __init__(self, installation, *, client: ZoomClient | None = None) -> None:
        super().__init__(installation)
        self._client = client

    @property
    def name(self) -> str:
        return "zoom"

    def _build_client(self) -> ZoomClient:
        if self._client is not None:
            return self._client
        return ZoomClient(
            account_id=str(self.config.get("account_id") or ""),
            api_token=str(self.config.get("api_token") or ""),
            base_url=str(self.config.get("base_url") or "") or "https://api.zoom.us/v2/",
        )

    def validate_config(self) -> None:
        account_id = str(self.config.get("account_id") or "").strip()
        api_token = str(self.config.get("api_token") or "").strip()
        if not account_id:
            raise ConnectorConfigurationError("account_id is required.")
        if not api_token:
            raise ConnectorConfigurationError("api_token is required.")

    def health_check(self) -> dict[str, Any]:
        self.validate_config()
        try:
            result = self._build_client().health_check()
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorHealthError(f"Zoom health check failed: {exc}") from exc
        # Never leak credentials.
        return {
            "ok": bool(result.get("ok")),
            "account_id": self.config.get("account_id"),
            "account_name": result.get("account_name") or "",
        }

    def pull_media(self, **kwargs: Any) -> list[MediaPullItem]:
        self.validate_config()
        client = self._build_client()
        recordings = client.list_recordings(
            from_date=kwargs.get("from_date"),
            to_date=kwargs.get("to_date"),
        )
        # One primary file per meeting_id.
        by_meeting: dict[str, list] = {}
        for recording in recordings:
            by_meeting.setdefault(recording.meeting_id, []).append(recording)

        items: list[MediaPullItem] = []
        for meeting_id, group in by_meeting.items():
            primary = pick_primary_recording(group)
            if primary is None:
                continue
            ext = primary.file_extension or "mp4"
            filename = f"zoom-{primary.recording_id}.{ext}"
            items.append(
                MediaPullItem(
                    external_id=primary.recording_id,
                    source_url=primary.download_url,
                    filename=filename,
                    metadata={
                        "external_system": EXTERNAL_SYSTEM,
                        "external_type": EXTERNAL_TYPE,
                        "external_id": primary.recording_id,
                        "media_url": primary.download_url,
                        "meeting_id": meeting_id,
                        "topic": primary.topic,
                        "file_type": primary.file_type,
                        "file_size": primary.file_size,
                        "recording_start": primary.recording_start,
                        "recording_end": primary.recording_end,
                        **dict(primary.metadata or {}),
                    },
                )
            )
        return items

    def sync(self) -> ConnectorSyncResult:
        self.validate_config()
        try:
            items = self.pull_media()
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorSyncError(f"Zoom pull_media failed: {exc}") from exc

        org = self.installation.organization
        media_service = MediaService()
        refs = ExternalReferenceService()
        created_items: list[MediaPullItem] = []
        skipped = 0

        for item in items:
            if not item.source_url:
                skipped += 1
                continue
            existing = refs.lookup(
                organization=org,
                external_system=EXTERNAL_SYSTEM,
                external_type=EXTERNAL_TYPE,
                external_id=item.external_id,
            )
            if existing.filter(media__isnull=False).exists():
                skipped += 1
                continue
            try:
                asset = media_service.create_from_url(
                    url=item.source_url,
                    use_case=UseCase.MEETING,
                    organization=org,
                    original_filename=item.filename or f"zoom-{item.external_id}.mp4",
                    metadata={
                        "connector": EXTERNAL_SYSTEM,
                        "connector_installation_id": str(self.installation.id),
                        "zoom": {
                            k: v
                            for k, v in (item.metadata or {}).items()
                            if k not in {"api_token", "token", "secret"}
                        },
                    },
                )
                refs.attach_to_media(
                    asset,
                    external_system=EXTERNAL_SYSTEM,
                    external_type=EXTERNAL_TYPE,
                    external_id=item.external_id,
                    metadata={
                        "meeting_id": (item.metadata or {}).get("meeting_id", ""),
                        "topic": (item.metadata or {}).get("topic", ""),
                    },
                )
                created_items.append(item)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Zoom sync failed creating media for recording %s",
                    item.external_id,
                )
                raise ConnectorSyncError(
                    f"Failed to ingest Zoom recording '{item.external_id}': {exc}"
                ) from exc

        return ConnectorSyncResult(
            records_processed=len(created_items),
            media_items=created_items,
            details={"skipped": skipped, "discovered": len(items)},
        )
