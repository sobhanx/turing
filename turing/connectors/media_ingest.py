"""
Shared connector media ingest helpers.

Prefer authenticated download into Turing storage; fall back to URL registration
so existing Speechmatics fetch_data behaviour remains available when download fails.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

import requests

from turing.connectors.base import ConnectorSyncResult, MediaPullItem
from turing.domain.enums import UseCase
from turing.domain.exceptions import ValidationError
from turing.services.external_reference import ExternalReferenceService
from turing.services.media import MediaService

logger = logging.getLogger(__name__)

SECRET_META_KEYS = frozenset(
    {
        "api_token",
        "token",
        "secret",
        "access_token",
        "refresh_token",
        "auth_token",
        "password",
    }
)


def scrub_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        k: v
        for k, v in dict(metadata or {}).items()
        if k not in SECRET_META_KEYS
    }


def create_media_from_connector_url(
    *,
    url: str,
    organization,
    use_case: str = UseCase.MEETING,
    original_filename: str = "",
    metadata: dict | None = None,
    headers: Mapping[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    timeout_seconds: float = 120.0,
    media_service: MediaService | None = None,
    fallback_to_url: bool = True,
):
    """
    Download with connector credentials into storage when possible.

    Returns ``(asset, mode)`` where mode is ``\"downloaded\"`` or ``\"url_fallback\"``.
    """
    media_service = media_service or MediaService()
    meta = dict(metadata or {})
    filename = (original_filename or "").strip() or url.rsplit("/", 1)[-1] or "recording.bin"

    try:
        with requests.get(
            url,
            headers=dict(headers or {}),
            auth=auth,
            timeout=timeout_seconds,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            # Materialize via iter_content into a SpooledTemporaryFile-friendly stream
            from tempfile import SpooledTemporaryFile

            spool = SpooledTemporaryFile(max_size=8 * 1024 * 1024)
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    spool.write(chunk)
            spool.seek(0)
            asset = media_service.create_from_upload(
                uploaded_file=spool,
                filename=filename,
                content_type=content_type,
                use_case=use_case,
                organization=organization,
                metadata={
                    **meta,
                    "ingest": "downloaded",
                    "source_url": url,
                },
            )
            # Keep provenance URL without relying on it for STT when object_key exists.
            if not asset.external_url:
                asset.external_url = url
                asset.save(update_fields=["external_url", "updated_at"])
            return asset, "downloaded"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Connector media download failed (%s); fallback=%s",
            type(exc).__name__,
            fallback_to_url,
        )
        if not fallback_to_url:
            raise ValidationError(
                f"Failed to download connector media: {exc}"
            ) from exc
        asset = media_service.create_from_url(
            url=url,
            use_case=use_case,
            organization=organization,
            original_filename=filename,
            metadata={
                **meta,
                "ingest": "url_fallback",
                "download_error": str(exc)[:500],
            },
        )
        return asset, "url_fallback"


def sync_media_pull_items(
    *,
    installation,
    items: list[MediaPullItem],
    external_system: str,
    external_type: str | Callable[[MediaPullItem], str],
    use_case: str | Callable[[MediaPullItem], str],
    metadata_namespace: str,
    default_filename: Callable[[MediaPullItem], str] | None = None,
    download_auth: Callable[[], tuple[Mapping[str, str] | None, tuple[str, str] | None]]
    | None = None,
    attach_metadata: Callable[[MediaPullItem], dict[str, Any]] | None = None,
    media_service: MediaService | None = None,
    refs: ExternalReferenceService | None = None,
) -> ConnectorSyncResult:
    """
    Shared idempotent sync loop used by meeting/CRM/telephony connectors.

    Behaviour preserved: skip empty URLs / existing refs; attach ExternalReference;
    raise ``ConnectorSyncError`` on per-item failure.
    """
    from turing.connectors.exceptions import ConnectorSyncError

    org = installation.organization
    media_service = media_service or MediaService()
    refs = refs or ExternalReferenceService()
    created_items: list[MediaPullItem] = []
    skipped = 0
    downloaded = 0
    url_fallback = 0

    headers: Mapping[str, str] | None = None
    auth: tuple[str, str] | None = None
    if download_auth is not None:
        headers, auth = download_auth()

    for item in items:
        if not item.source_url:
            skipped += 1
            continue
        type_value = (
            external_type(item) if callable(external_type) else external_type
        )
        case_value = use_case(item) if callable(use_case) else use_case
        existing = refs.lookup(
            organization=org,
            external_system=external_system,
            external_type=type_value,
            external_id=item.external_id,
        )
        if existing.filter(media__isnull=False).exists():
            skipped += 1
            continue
        try:
            meta = scrub_metadata(item.metadata)
            filename = item.filename or (
                default_filename(item) if default_filename else f"{external_system}-{item.external_id}"
            )
            asset, mode = create_media_from_connector_url(
                url=item.source_url,
                organization=org,
                use_case=case_value,
                original_filename=filename,
                metadata={
                    "connector": external_system,
                    "connector_installation_id": str(installation.id),
                    metadata_namespace: meta,
                },
                headers=headers,
                auth=auth,
                media_service=media_service,
            )
            if mode == "downloaded":
                downloaded += 1
            else:
                url_fallback += 1
            ref_meta = (
                attach_metadata(item) if attach_metadata is not None else {}
            )
            refs.attach_to_media(
                asset,
                external_system=external_system,
                external_type=type_value,
                external_id=item.external_id,
                metadata=ref_meta,
            )
            # Vendor-independent Meeting → Recording → MediaAsset layer.
            # Failures here must not undo media/ExternalReference (log + continue).
            try:
                from turing.services.meeting import MeetingService

                MeetingService().ingest_from_pull_item(
                    organization=org,
                    provider=external_system,
                    item=item,
                    media=asset,
                    connector_installation=installation,
                )
            except Exception as meeting_exc:  # noqa: BLE001
                logger.warning(
                    "Meeting/Recording upsert failed for %s/%s: %s",
                    external_system,
                    item.external_id,
                    meeting_exc,
                )
            created_items.append(item)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "%s sync failed creating media for %s",
                external_system,
                item.external_id,
            )
            raise ConnectorSyncError(
                f"Failed to ingest '{item.external_id}': {exc}"
            ) from exc

    return ConnectorSyncResult(
        records_processed=len(created_items),
        media_items=created_items,
        details={
            "skipped": skipped,
            "discovered": len(items),
            "downloaded": downloaded,
            "url_fallback": url_fallback,
        },
    )
