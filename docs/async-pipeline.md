# Async transcription pipeline (Phase 2.1)

Production processing is fully asynchronous. Celery workers never block on
provider polling.

## Flow

```text
create job (auto_enqueue=True)
  → prepare_media_for_transcription
  → submit_transcription_job
  → poll_transcription_job  (reschedules itself with exponential backoff)
  → fetch_and_persist_transcription
  → Transcript + Speakers + Segments + Revision #1
  → generate_transcript_analysis (Phase 3.2, only on first persist)
```

| Task | Responsibility |
|------|----------------|
| `submit_transcription_job` | Idempotent provider submit; stores `external_job_id` |
| `poll_transcription_job` | One status check; reschedule or hand off |
| `fetch_and_persist_transcription` | Fetch + persist (idempotent) |
| `prepare_media_for_transcription` | Inspect + normalize before STT submit |
| `generate_transcript_analysis` | Default AI suite (summary, action items, topics) |

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
- Concurrent workers claim `stage=submitting` before provider I/O; losers return
  `submit_in_progress` (Celery retries shortly) instead of creating a second
  provider job.
- Provider API keys are **Attempt-sticky**: selected once in `begin_attempt`,
  reused for submit/poll/fetch/cancel. Rotation only occurs on a **new** Attempt
  after failure/retry. See [architecture/provider-credential-pool.md](architecture/provider-credential-pool.md).
- If two submits still race after provider I/O, the orphan `external_job_id` is
  best-effort cancelled via `STTProvider.cancel`.
- `persist_from_provider` returns the existing transcript if one exists for the
  job (including `IntegrityError` races).
- `mark_succeeded` is a no-op when the job is already `cancelled`.
- Retries after failure clear `external_job_id` and start a new attempt.

## Cancel

Local cancel always wins for Turing state. When `external_job_id` is set,
`JobOrchestrator.cancel` also calls the provider cancel API (best-effort;
provider errors are logged and do not undo local cancel).

Fetch/persist re-checks cancel under `select_for_update` before writing the
transcript and before marking the job succeeded.

## Lifecycle transitions

`domain/policies.py` defines allowed `ProcessingJob` status transitions.
Enqueue / succeed / fail / cancel paths validate transitions (invalid moves
raise `JobStateError` or are skipped safely for succeed/fail).

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
used by polling. Speechmatics webhooks call the same method via
`ingest_provider_notification`, then `fetch_and_persist_transcription.delay`
when status is success.

See [webhooks.md](webhooks.md) for configuration (`TURING_WEBHOOK_MODE=augment`,
`TURING_WEBHOOK_BASE_URL`, `TURING_SPEECHMATICS_WEBHOOK_SECRET`) and security notes.

## Sync fallback

```bash
python manage.py turing_process_job <job-uuid>
```

Runs submit → poll loop (with sleep) → fetch/persist in-process for debugging
only. Prefer Celery in all normal environments.
