from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import requests
from django.db import transaction
from django.utils import timezone

from turing.conf import get_turing_settings
from turing.domain.enums import OutboundWebhookDeliveryStatus
from turing.models import OutboxEvent, WebhookDelivery, WebhookSubscription
from turing.services.webhook_retry import is_retryable_failure

logger = logging.getLogger(__name__)

RESPONSE_PREVIEW_CHARS = 500
SIGNATURE_HEADER = "X-Turing-Signature"
EVENT_HEADER = "X-Turing-Event"


def sign_payload(secret: str, body: bytes) -> str:
    """Return ``sha256=<hex>`` HMAC-SHA256 of ``body`` using ``secret``."""
    digest = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def build_webhook_envelope(outbox_event: OutboxEvent) -> dict[str, Any]:
    """
    Minimal host-facing envelope.

    Never includes transcript text, analysis content, or secrets.
    """
    payload = dict(outbox_event.payload or {})
    organization_id = payload.pop("organization_id", outbox_event.organization_id)
    # Drop anything that should never leave the platform (defense in depth).
    for forbidden in ("full_text", "content", "text", "secret", "api_key"):
        payload.pop(forbidden, None)

    occurred_at = outbox_event.created_at
    return {
        "event": outbox_event.event_name,
        "id": str(outbox_event.id),
        "organization_id": str(organization_id),
        "occurred_at": occurred_at.isoformat() if occurred_at else "",
        "data": payload,
    }


class WebhookDeliveryService:
    """Create outbound deliveries and perform signed HTTP posts."""

    def matching_subscriptions(
        self,
        *,
        organization_id: int,
        event_name: str,
    ) -> list[WebhookSubscription]:
        qs = WebhookSubscription.objects.filter(
            organization_id=organization_id,
            is_active=True,
        )
        return [sub for sub in qs if sub.accepts_event(event_name)]

    def enqueue_for_outbox(self, outbox_event: OutboxEvent) -> list[WebhookDelivery]:
        """
        Create PENDING deliveries for matching subscriptions and enqueue HTTP tasks.

        Safe to call repeatedly: unique (subscription, outbox_event) is idempotent.
        Never raises to the caller for per-subscription failures.
        """
        from turing.tasks.webhooks import deliver_webhook_delivery

        created: list[WebhookDelivery] = []
        subscriptions = self.matching_subscriptions(
            organization_id=outbox_event.organization_id,
            event_name=outbox_event.event_name,
        )
        for subscription in subscriptions:
            try:
                delivery, was_created = WebhookDelivery.objects.get_or_create(
                    subscription=subscription,
                    outbox_event=outbox_event,
                    defaults={"status": OutboundWebhookDeliveryStatus.PENDING},
                )
                if was_created or delivery.status == OutboundWebhookDeliveryStatus.PENDING:
                    deliver_webhook_delivery.delay(str(delivery.id))
                    created.append(delivery)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to enqueue webhook delivery for subscription %s outbox %s",
                    subscription.id,
                    outbox_event.id,
                )
        return created

    def attempt_delivery(self, delivery_id: str) -> str:
        """
        Perform one HTTP delivery attempt.

        Returns:
            ``delivered`` | ``retry`` | ``failed``
        """
        settings = get_turing_settings()
        max_attempts = max(1, int(settings.outbound_webhook_max_retries) + 1)

        with transaction.atomic():
            try:
                delivery = (
                    WebhookDelivery.objects.select_for_update()
                    .select_related("subscription", "outbox_event")
                    .get(pk=delivery_id)
                )
            except WebhookDelivery.DoesNotExist:
                logger.warning("Webhook delivery %s not found", delivery_id)
                return "failed"

            if delivery.status == OutboundWebhookDeliveryStatus.DELIVERED:
                return "delivered"
            if delivery.status == OutboundWebhookDeliveryStatus.FAILED:
                return "failed"

            subscription = delivery.subscription
            if not subscription.is_active:
                delivery.status = OutboundWebhookDeliveryStatus.FAILED
                delivery.last_error = "Subscription is inactive."
                delivery.processing_started_at = None
                delivery.save(
                    update_fields=[
                        "status",
                        "last_error",
                        "processing_started_at",
                        "updated_at",
                    ]
                )
                return "failed"

            delivery.status = OutboundWebhookDeliveryStatus.DELIVERING
            delivery.attempts = delivery.attempts + 1
            delivery.processing_started_at = timezone.now()
            delivery.save(
                update_fields=[
                    "status",
                    "attempts",
                    "processing_started_at",
                    "updated_at",
                ]
            )

        envelope = build_webhook_envelope(delivery.outbox_event)
        body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        secret = subscription.secret or ""
        headers = {
            "Content-Type": "application/json",
            EVENT_HEADER: delivery.outbox_event.event_name,
            SIGNATURE_HEADER: sign_payload(secret, body),
            "User-Agent": "turing-outbound-webhook/1.0",
        }
        timeout = float(settings.outbound_webhook_timeout_seconds)

        try:
            response = requests.post(
                subscription.url,
                data=body,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            return self._record_failure(
                delivery_id,
                error=str(exc),
                status_code=None,
                body_preview="",
                max_attempts=max_attempts,
                network_error=True,
            )

        preview = (response.text or "")[:RESPONSE_PREVIEW_CHARS]
        if 200 <= response.status_code < 300:
            return self._record_success(
                delivery_id,
                status_code=response.status_code,
                body_preview=preview,
            )
        return self._record_failure(
            delivery_id,
            error=f"HTTP {response.status_code}",
            status_code=response.status_code,
            body_preview=preview,
            max_attempts=max_attempts,
            network_error=False,
        )

    def _record_success(
        self,
        delivery_id: str,
        *,
        status_code: int,
        body_preview: str,
    ) -> str:
        with transaction.atomic():
            delivery = WebhookDelivery.objects.select_for_update().get(pk=delivery_id)
            delivery.status = OutboundWebhookDeliveryStatus.DELIVERED
            delivery.response_status_code = status_code
            delivery.response_body_preview = body_preview
            delivery.last_error = ""
            delivery.delivered_at = timezone.now()
            delivery.processing_started_at = None
            delivery.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "response_body_preview",
                    "last_error",
                    "delivered_at",
                    "processing_started_at",
                    "updated_at",
                ]
            )
        return "delivered"

    def _record_failure(
        self,
        delivery_id: str,
        *,
        error: str,
        status_code: int | None,
        body_preview: str,
        max_attempts: int,
        network_error: bool = False,
    ) -> str:
        retryable = is_retryable_failure(
            status_code=status_code,
            network_error=network_error,
        )
        with transaction.atomic():
            delivery = WebhookDelivery.objects.select_for_update().get(pk=delivery_id)
            delivery.response_status_code = status_code
            delivery.response_body_preview = body_preview
            delivery.last_error = (error or "")[:4000]
            delivery.processing_started_at = None
            exhausted = delivery.attempts >= max_attempts
            if (not retryable) or exhausted:
                delivery.status = OutboundWebhookDeliveryStatus.FAILED
                if not retryable and not exhausted:
                    delivery.last_error = (
                        f"{delivery.last_error} (non-retryable)".strip()
                    )[:4000]
                delivery.save(
                    update_fields=[
                        "status",
                        "response_status_code",
                        "response_body_preview",
                        "last_error",
                        "processing_started_at",
                        "updated_at",
                    ]
                )
                return "failed"
            delivery.status = OutboundWebhookDeliveryStatus.PENDING
            delivery.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "response_body_preview",
                    "last_error",
                    "processing_started_at",
                    "updated_at",
                ]
            )
            return "retry"

    def retry_countdown(self, attempts: int) -> float:
        settings = get_turing_settings()
        base = float(settings.outbound_webhook_backoff_base_seconds)
        cap = float(settings.outbound_webhook_backoff_max_seconds)
        # attempts is post-increment count for the failed try
        exponent = max(0, attempts - 1)
        return min(base * (2**exponent), cap)
