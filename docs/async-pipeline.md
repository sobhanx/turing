# Async transcription pipeline (Phase 2.1)

Production processing is fully asynchronous. Celery workers never block on
provider polling.

## Flow

```text
create job (auto_enqueue=True)
  → submit_transcription_job
  → poll_transcription_job  (reschedules itself with exponential backoff)
  → fetch_and_persist_transcription
  → Transcript + Speakers + Segments + Revision #1
```

| Task | Responsibility |
|------|----------------|
| `submit_transcription_job` | Idempotent provider submit; stores `external_job_id` |
| `poll_transcription_job` | One status check; reschedule or hand off |
| `fetch_and_persist_transcription` | Fetch + persist (idempotent) |

Entry alias: `process_transcription_job` starts the same submit step (compat).

## Worker

```bash
celery -A config worker -l info -Q turing.default,turing.high,turing.export
```

Requires Redis (or another Celery broker) via `CELERY_BROKER_URL`.

When `Platform configuration.auto_enqueue` is enabled (default), creating a job
from Admin or API schedules `submit_transcription_job` automatically.

## Idempotency

- Re-submit is skipped when `external_job_id` is already set.
- `persist_from_provider` returns the existing transcript if one exists for the job.
- Retries after failure clear `external_job_id` and start a new attempt.

## Backoff & limits

| Setting | Default | Meaning |
|---------|---------|---------|
| `TURING_POLL_BACKOFF_BASE_SECONDS` | poll interval (3) | Base poll delay |
| `TURING_POLL_BACKOFF_MAX_SECONDS` | 60 | Cap for poll backoff |
| `TURING_POLL_TIMEOUT_SECONDS` | 1800 | Fail job if provider not done |
| `TURING_MAX_POLL_ATTEMPTS` | 0 (unlimited) | Optional poll attempt cap |
| `TURING_RETRY_BACKOFF_BASE_SECONDS` | 5 | Delay before auto re-submit |
| `TURING_RETRY_BACKOFF_MAX_SECONDS` | 300 | Cap for submit retry backoff |
| `default_max_attempts` (Admin) | 3 | Max processing attempts |

Poll delay ≈ `min(base * 2^poll_count, max)` with light jitter.

## Failures

Failed jobs store `error_code` and `error_message` (visible in Admin).
Retryable codes auto-reschedule a new attempt until `max_attempts`.

Enqueue/broker failures set `error_code=ENQUEUE_FAILED` and leave the job
`pending` with a clear Admin message.

## Webhook-ready polling

`TranscriptionService.apply_provider_status(...)` is the shared transition
used by polling. A future Speechmatics webhook can call the same method and
then `fetch_and_persist_transcription.delay(job_id)` when status is success —
without changing persist logic.

## Sync fallback

```bash
python manage.py turing_process_job <job-uuid>
```

Runs submit → poll loop (with sleep) → fetch/persist in-process for debugging
only. Prefer Celery in all normal environments.
