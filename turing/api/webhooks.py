from __future__ import annotations

import logging

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from turing.providers.speechmatics.webhook import WebhookParseError, parse_speechmatics_notification
from turing.tasks.webhooks import process_provider_webhook_event
from turing.webhooks.auth import verify_speechmatics_webhook_bearer

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def speechmatics_webhook(request):
    """
    Speechmatics batch notification callback.

    Authenticated via Bearer token (``TURING_SPEECHMATICS_WEBHOOK_SECRET``).
    Always returns 200 for accepted/queued events; 403 when auth fails.
    """
    if not verify_speechmatics_webhook_bearer(request.headers):
        logger.warning("Rejected Speechmatics webhook: invalid or missing Bearer token.")
        return HttpResponse(status=403)

    try:
        notification = parse_speechmatics_notification(request)
    except WebhookParseError as exc:
        logger.warning("Rejected Speechmatics webhook: %s", exc)
        return HttpResponse(status=400)

    process_provider_webhook_event.delay(notification.to_dict())
    return HttpResponse(status=200)
