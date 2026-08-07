# Provider Credential Pool — Architecture Decision

## Status

Accepted (implemented).

## Context

Turing’s STT pipeline runs as async Celery work: submit → poll → fetch (with
cancel and webhook-driven fetch as alternate paths). Provider API keys may be
exhausted or rate-limited independently. The platform needs a pool of encrypted
credentials with automatic failover on **retry**, without breaking in-flight
provider jobs (Speechmatics job IDs are account-scoped).

## Decision

**Provider credentials are scoped to `ProcessingAttempt`, not `ProcessingJob`.**

### Rules

1. A credential is selected **once** when a new Attempt starts
   (`JobOrchestrator.begin_attempt` → `CredentialManager.acquire`).
2. That credential remains **sticky** for the Attempt’s entire provider I/O:
   - submit
   - poll
   - fetch
   - cancel
   - webhook-triggered fetch
3. Credential **rotation** happens only by creating a **new** Attempt
   (failure → enqueue with cleared `external_job_id` → new Attempt → new acquire).
4. A RUNNING Attempt is **reused** without calling `acquire` again
   (`TranscriptionService._ensure_running_attempt`).
5. When `ProcessingAttempt.provider_credential` is set, settings cache /
   `SpeechProviderConfig.api_key` / adapter legacy singleton resolution must
   **never** override that sticky key.
6. When the FK is `NULL` (pre-migration rows or empty pool), the legacy
   singleton path remains (`get_turing_settings` / `SpeechProviderConfig.api_key`).

### Model sketch

```text
SpeechProviderConfig          # provider defaults + legacy api_key fallback
  └── ProviderCredential[]    # pool of encrypted API keys + cooldown state
ProcessingAttempt
  └── provider_credential FK (nullable, PROTECT)
ProcessingJob
  └── provider_code only      # no credential FK
```

## Alternatives considered

### Job-level credential FK

**Rejected.** Retries create a new Attempt and may need a **different** pool
key after quota/auth failure. A Job-level FK would either pin one key across
retries (defeating failover) or require mutating the Job mid-flight (racing
with poll/fetch).

### Generic secrets / OAuth credential framework

**Rejected for v1.** Current STT integration (`SpeechmaticsClient`) authenticates
with a single Bearer API key. Connector OAuth already has a separate
`ConnectorCredential` model. A polymorphic secret bag would add Admin and
migration complexity without a second STT auth scheme in tree.

## Consequences

- Failover cost is one Attempt slot (`max_attempts`); operators may raise
  `max_attempts` when the pool is large.
- Cooldown (`CredentialManager.mark_failure`) affects **new** acquires only;
  sticky Attempts keep using their key until the Attempt ends.
- Deleting a `ProviderCredential` referenced by Attempts is blocked (`PROTECT`);
  deactivate with `is_active=False` instead.
- Production concurrency relies on PostgreSQL
  `SELECT … FOR UPDATE SKIP LOCKED` in `CredentialManager.acquire`.

## Related code

- `turing/models/configuration.py` — `ProviderCredential`
- `turing/models/job.py` — `ProcessingAttempt.provider_credential`
- `turing/services/credential_manager.py`
- `turing/services/job_orchestrator.py` — `begin_attempt`
- `turing/services/transcription.py` — sticky `_provider_for_attempt`
- `turing/providers/speechmatics/adapter.py` — injected vs legacy client
