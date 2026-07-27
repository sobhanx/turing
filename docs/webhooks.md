# Provider webhooks (Phase 3.1)

Speechmatics batch notifications can drive the transcription pipeline instead of
relying on polling alone. Turing uses **augment** mode: webhooks accelerate
completion; polling remains the safety net.

## Flow

```text
Speechmatics POST /api/turing/v1/webhooks/speechmatics/?id=…&status=…
  → Bearer auth (TURING_SPEECHMATICS_WEBHOOK_SECRET)
  → Celery: process_provider_webhook_event
  → TranscriptionService.ingest_provider_notification
       → ProviderWebhookDelivery dedupe
       → apply_provider_status (shared with poll)
       → fetch_and_persist_transcription.delay on success
```

Polling (`poll_transcription_job`) is unchanged and continues after submit.

## Configuration

| Setting | Env var | Purpose |
|---------|---------|---------|
| Webhook mode | `TURING_WEBHOOK_MODE` | `augment` (default) or `off` |
| Public base URL | `TURING_WEBHOOK_BASE_URL` | e.g. `https://turing.example.com` |
| Bearer secret | `TURING_SPEECHMATICS_WEBHOOK_SECRET` | Sent to Speechmatics in `auth_headers`; validated on callback |

Admin: **Platform configuration** → Webhooks (`webhook_mode`, `webhook_base_url`).

`notification_config` is attached at job submit only when mode is `augment` **and**
both secret and base URL are set.

## Security

- No Django user / membership auth on the webhook endpoint.
- `Authorization: Bearer <secret>` validated with constant-time comparison.
- Invalid secret → **403** (Speechmatics may retry).
- Unknown `external_job_id` → **200** + `unknown_job` audit row (no internal details leaked).

## Idempotency

`ProviderWebhookDelivery` stores each event with a unique `(provider_code, dedupe_key)`.
Duplicate deliveries return `duplicate` without side effects.

Dedupe key: `sha256(provider:external_job_id:status:body_hash)`.

## Local testing

1. Set env vars (see `.env.example`).
2. Expose your dev server (e.g. ngrok) and set `TURING_WEBHOOK_BASE_URL`.
3. Or test in-process:

```bash
pytest tests/unit/test_provider_webhooks.py -v
```

Simulate HTTP callback:

```bash
curl -X POST \
  -H "Authorization: Bearer $TURING_SPEECHMATICS_WEBHOOK_SECRET" \
  "http://localhost:8000/api/turing/v1/webhooks/speechmatics/?id=EXT_JOB_ID&status=success"
```

## Phase 3.1a scope

- Status notifications only (`jobinfo` in `notification_config`).
- Transcript attachments in webhook body are **not** parsed yet (still uses `fetch_result`).

## Related

- [async-pipeline.md](async-pipeline.md) — Celery tasks and poll backoff
- [deployment.md](deployment.md) — production URL / HTTPS (host responsibility)
