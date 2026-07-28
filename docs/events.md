# Event foundation + durable outbox + outbound webhooks

(Phases 4.1.3 / 4.2.1 / 4.2.2 / 4.2.3)

Internal notifications for host integrations. Events **do not** replace Celery and
must never break the speech pipeline.

## Flow

```
service
  → emit_after_commit()
  → EventBus.emit()
       → OutboxEvent (PENDING)   # durable, after commit
       → in-process handlers     # sync; failures swallowed

Celery Beat (optional): dispatch_outbox_events / recover_stuck_outbox_work
  → recover stuck PROCESSING / DELIVERING → PENDING
  → claim PENDING → PROCESSING
  → OutboxDispatcher handlers (isolated failures)
       → enqueue WebhookDelivery rows + deliver_webhook_delivery.delay()
  → OutboxEvent DELIVERED

Celery: deliver_webhook_delivery
  → signed HTTP POST to subscription URL
  → WebhookDelivery DELIVERED | retry | FAILED
```

Webhook HTTP failures never mark the parent `OutboxEvent` failed.

## Celery Beat / settings

| Setting | Default | Meaning |
|---------|---------|---------|
| `TURING_OUTBOX_DISPATCH_ENABLED` | `true` | When false, Beat schedule omits outbox tasks (safe disable) |
| `TURING_OUTBOX_DISPATCH_INTERVAL_SECONDS` | `30` | Beat interval for dispatch + recovery |
| `TURING_OUTBOX_STUCK_TIMEOUT_SECONDS` | `300` | Age after which PROCESSING/DELIVERING is considered stuck |
| `TURING_OUTBOUND_WEBHOOK_MAX_RETRIES` | `5` | Extra retries after the first attempt |
| `TURING_OUTBOUND_WEBHOOK_*_BACKOFF_*` | 2…300s | Exponential backoff bounds |
| `TURING_OUTBOUND_WEBHOOK_TIMEOUT_SECONDS` | `10` | HTTP timeout |

Run Beat alongside workers:

```bash
celery -A config beat -l info
celery -A config worker -l info -Q turing.default,turing.high,turing.export
```

## Retry behavior

Retry (with exponential backoff):

- connection / timeout errors
- HTTP `429`
- HTTP `5xx`

Do **not** retry (mark `FAILED` immediately):

- HTTP `400`, `401`, `403`, `404`
- other non-429 `4xx`

Max attempts = `1 + TURING_OUTBOUND_WEBHOOK_MAX_RETRIES`.

## Failure handling

- Outbox handler failures are isolated; the outbox row still becomes `DELIVERED`
  after fan-out enqueue.
- Webhook delivery failures stay on `WebhookDelivery` (`last_error`, status,
  response preview) and never fail the speech pipeline.
- Permanent HTTP errors and exhausted retries → `FAILED`.

## Operational recovery

If a worker dies mid-flight:

- `OutboxEvent` stuck in `PROCESSING` with `processing_started_at` older than the
  timeout → reset to `PENDING`, `recovery_count += 1`
- `WebhookDelivery` stuck in `DELIVERING` → reset to `PENDING`, re-enqueue HTTP
  task, `recovery_count += 1`

Helpers (`OutboxOpsService`):

- `pending_deliveries()` / `failed_deliveries()` / `stuck_deliveries()`
- `stuck_outbox_events()` / `recover_stuck()`

Admin: filter webhook deliveries by status, event, attempts, and created_at.

## Outbound webhook envelope

```json
{
  "event": "transcript.created",
  "id": "<outbox-event-uuid>",
  "organization_id": "<id>",
  "occurred_at": "<iso8601>",
  "data": { "ids only..." }
}
```

Headers:

- `X-Turing-Event`: event name
- `X-Turing-Signature`: `sha256=<hmac-hex>` over the raw body

Never sent: transcript text, analysis content, secrets.

## Canonical events

### `media.created`

| | |
|---|---|
| **Producer** | `MediaService` (`create_from_upload` / URL registration) |
| **Meaning** | A new media asset was committed and is available for processing |
| **Payload** | `media_id`, `organization_id`, `external_references[]` |

### `job.completed`

| | |
|---|---|
| **Producer** | `JobOrchestrator.mark_succeeded` |
| **Meaning** | A processing job reached SUCCEEDED (STT completed successfully) |
| **Payload** | `job_id`, `organization_id`, optional `media_id` / `transcript_id`, `external_references[]` |

### `transcript.created`

| | |
|---|---|
| **Producer** | `TranscriptService` on first persist (not idempotent re-fetch) |
| **Meaning** | A new transcript row was written; transcript is the source of truth |
| **Payload** | `transcript_id`, `organization_id`, optional `media_id` / `job_id`, `external_references[]` |

### `analysis.completed`

| | |
|---|---|
| **Producer** | `TranscriptAnalysisService` after persisting an analysis suite |
| **Meaning** | One or more AI analysis rows were appended for a transcript |
| **Payload** | `transcript_id`, `organization_id`, `analysis_ids[]`, `analysis_types[]`, `provider`, `external_references[]` |

## Payload rules

- IDs + `organization_id` only
- Optional `external_references` snapshots (`external_system` / `external_type` / `external_id`)
- **No** transcript text, segment text, or analysis `content`
- Outbox stores the same minimal payload JSON

## In-process usage

```python
from turing.domain.events import EventName
from turing.events import EventBus

def on_transcript_created(event):
    ...

EventBus.subscribe(EventName.TRANSCRIPT_CREATED, on_transcript_created)
# or EventBus.subscribe("*", handler) for all events
```

Emission uses `transaction.on_commit()` so handlers and outbox writes see committed
state. Handler exceptions (and outbox persist failures) are logged and swallowed.

## Durable dispatch + webhooks

```python
from turing.tasks.events import dispatch_outbox_events

# Periodically (Celery Beat) or manually:
dispatch_outbox_events.delay()
```

Configure org subscriptions in Admin (`WebhookSubscription`) or via the REST API.
Delivery attempts appear under `WebhookDelivery` (Admin + nested API).

### Subscription API

Requires capability `manage_config` (org Admin role).

```http
POST /api/turing/v1/webhooks/
Content-Type: application/json

{
  "name": "CRM events",
  "url": "https://example.com/hooks/turing",
  "subscribed_events": ["transcript.created", "analysis.completed"]
}
```

Create response (signing secret returned **once**):

```json
{
  "subscription": {
    "id": "...",
    "name": "CRM events",
    "url": "https://example.com/hooks/turing",
    "subscribed_events": ["transcript.created", "analysis.completed"],
    "is_active": true,
    "created_at": "...",
    "updated_at": "..."
  },
  "signing_secret": "<store-this-now>"
}
```

Other routes:

| Method | Path | Notes |
|--------|------|--------|
| `GET` | `/api/turing/v1/webhooks/` | List (org-scoped; secret never included) |
| `GET` | `/api/turing/v1/webhooks/{id}/` | Retrieve |
| `PATCH` | `/api/turing/v1/webhooks/{id}/` | Update name/url/events/`is_active` |
| `DELETE` | `/api/turing/v1/webhooks/{id}/` | Delete |
| `GET` | `/api/turing/v1/webhooks/{id}/deliveries/` | Delivery status (no response body) |

`subscribed_events` must be non-empty and only include canonical event names or `*`.

Connector sync events (Phase 4.3.1) are also supported:

- `connector.sync.started`
- `connector.sync.completed`
- `connector.sync.failed`

Connector installation lifecycle events (Phase 4.4.1):

- `connector.installation.activated`
- `connector.installation.revoked`
