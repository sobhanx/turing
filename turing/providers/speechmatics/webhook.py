"""Speechmatics batch notification webhook parsing (Phase 3.1a)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.http import HttpRequest

from turing.providers.types import ProviderJobStatus
from turing.webhooks.types import ProviderNotification

SPEECHMATICS_PROVIDER_CODE = "speechmatics"

# Query param ``status`` values observed from Speechmatics notifications.
_STATUS_TO_STATE: dict[str, str] = {
    "success": "succeeded",
    "done": "succeeded",
    "rejected": "failed",
    "failed": "failed",
    "deleted": "failed",
    "expired": "failed",
    "running": "running",
}


class WebhookParseError(ValueError):
    """Raised when a webhook request cannot be parsed."""


def hash_request_body(request: HttpRequest) -> str:
    body = request.body or b""
    return hashlib.sha256(body).hexdigest()


def compute_dedupe_key(
    *,
    provider_code: str,
    external_job_id: str,
    status_param: str,
    payload_hash: str,
) -> str:
    material = f"{provider_code}:{external_job_id}:{status_param}:{payload_hash}"
    return hashlib.sha256(material.encode()).hexdigest()


def map_status_param(status_param: str) -> ProviderJobStatus:
    key = (status_param or "").strip().lower()
    state = _STATUS_TO_STATE.get(key, "running")
    return ProviderJobStatus(
        external_job_id="",
        state=state,
        message=status_param or "",
        raw={"status_param": status_param},
    )


def parse_speechmatics_notification(request: HttpRequest) -> ProviderNotification:
    """
    Parse Speechmatics batch callback.

    Speechmatics appends ``id`` and ``status`` query parameters to the callback URL.
    Phase 3.1a ignores multipart transcript attachments.
    """
    external_job_id = (request.GET.get("id") or "").strip()
    status_param = (request.GET.get("status") or "").strip()
    if not external_job_id:
        raise WebhookParseError("Missing required query parameter 'id'.")

    payload_hash = hash_request_body(request)
    dedupe_key = compute_dedupe_key(
        provider_code=SPEECHMATICS_PROVIDER_CODE,
        external_job_id=external_job_id,
        status_param=status_param,
        payload_hash=payload_hash,
    )
    mapped = map_status_param(status_param)
    raw_metadata: dict[str, Any] = {
        "query": dict(request.GET),
        "content_type": request.content_type or "",
        "payload_hash": payload_hash,
    }
    if request.body:
        # Store a short preview only — not full payloads (transcripts deferred to 3.1b).
        try:
            raw_metadata["body_preview"] = request.body[:512].decode("utf-8", errors="replace")
        except Exception:
            raw_metadata["body_preview"] = ""

    return ProviderNotification(
        provider_code=SPEECHMATICS_PROVIDER_CODE,
        external_job_id=external_job_id,
        status_param=status_param,
        provider_state=mapped.state,
        provider_message=mapped.message,
        dedupe_key=dedupe_key,
        payload_hash=payload_hash,
        raw_metadata=raw_metadata,
    )


def notification_config_for_submit(*, callback_url: str, bearer_secret: str) -> list[dict[str, Any]]:
    """Build Speechmatics ``notification_config`` for augment mode (status only)."""
    return [
        {
            "url": callback_url.rstrip("/") + "/",
            "contents": ["jobinfo"],
            "auth_headers": [f"Authorization: Bearer {bearer_secret}"],
        }
    ]

def webhook_callback_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/api/turing/v1/webhooks/speechmatics"
