# Event foundation (Phase 4.1.3)

Internal notifications for host integrations. Events **do not** replace Celery and
must never break the speech pipeline.

## Canonical events

| Name | When |
|------|------|
| `media.created` | After media upload/URL registration commits |
| `job.completed` | After a processing job is marked succeeded |
| `transcript.created` | After a new transcript is persisted (not idempotent re-fetch) |
| `analysis.completed` | After an AI analysis suite is persisted |

## Payload rules

- IDs + `organization_id` only
- Optional `external_references` snapshots (`external_system` / `external_type` / `external_id`)
- **No** transcript text, segment text, or analysis `content`

## Usage

```python
from turing.domain.events import EventName
from turing.events import EventBus

def on_transcript_created(event):
    ...

EventBus.subscribe(EventName.TRANSCRIPT_CREATED, on_transcript_created)
# or EventBus.subscribe("*", handler) for all events
```

Emission uses `transaction.on_commit()` so handlers see committed state.
Handler exceptions are logged and swallowed.
