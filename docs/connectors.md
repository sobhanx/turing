# Connector framework (Phase 4.3)

Generic architecture for pulling media / syncing state from external systems
into Turing.


## Package layout

```text
turing/connectors/
  base.py          BaseConnector contract (+ auth hooks)
  registry.py      ConnectorRegistry
  exceptions.py    Connector* errors
  builtins.py      register_builtin_connectors()
  mock_oauth.py    Test-only OAuth2 connector
  zoom/            Zoom Cloud Recording connector (oauth2)
    client.py
    connector.py
    oauth.py
    serializers.py
  oauth_state.py   Shim → OAuthStateService
```


## Connector contract

Every connector implements ``BaseConnector``:

| Method / attr | Purpose |
|---------------|---------|
| `auth_type` | `api_key` (default) or `oauth2` |
| `supports_oauth` / `supports_refresh` / `supports_revoke` | Capability flags |
| `supported_sync_types` | e.g. `("media",)` |
| `validate_config()` | Fail closed on bad installation config |
| `validate_credentials()` | Validate auth material (default: api_key→config; oauth2→token) |
| `refresh_credentials()` | OAuth token refresh (no-op for api_key) |
| `revoke_credentials()` | Optional remote revoke hook |
| `health_check()` | Lightweight connectivity probe (no secrets in result) |
| `pull_media()` | Discover remote media as ``MediaPullItem`` descriptors |
| `sync()` | Full sync pass → ``ConnectorSyncResult`` (may create media) |

Catalog capabilities (no secrets) come from ``BaseConnector.capability_metadata()``.

Register with:

```python
from turing.connectors import ConnectorRegistry, BaseConnector

@ConnectorRegistry.register
class ExampleConnector(BaseConnector):
    connector_type = "example"
    display_name = "Example"
    auth_type = "api_key"
    ...
```

Resolve:

```python
ConnectorRegistry.get("zoom")
connector = ConnectorRegistry.create(installation)
```

Core sync services never import vendor SDKs — only ``ConnectorRegistry`` + ``BaseConnector``.


## Auth models: API key vs OAuth2

| | `api_key` | `oauth2` |
|--|-----------|----------|
| Storage | Non-secret + key material in `installation.config` | Tokens on `ConnectorCredential` |
| Encryption | Config JSON (redacted in API); prefer migrating secrets later | Fernet via `CredentialEncryptionService` before DB write |
| Decrypt | N/A (config read) | Only inside connector execution |
| Default create status | `active` | `pending` until authorized |

### Credential lifecycle

``ConnectorCredential`` (org-scoped, one row per installation):

- `encrypted_access_token` / `encrypted_refresh_token` — ciphertext only
- `expires_at`, `metadata` (non-secret)
- Never serialized on API; Admin shows presence flags only

``ConnectorInstallationService``:

| Method | Effect |
|--------|--------|
| `store_credentials(...)` | Encrypt + upsert; sets `last_refreshed_at`, clears `revoked_at` |
| `activate()` | status → `active` |
| `expire()` | status → `expired` |
| `revoke()` | clear tokens, set `revoked_at`, status → `revoked` |
| `auth_status()` | Public summary: auth_type, has_credentials, expires_at, is_expired |

### Sync health (derived)

Installation helpers (not duplicated status columns):

| Helper | Meaning |
|--------|---------|
| `last_successful_sync()` | Latest COMPLETED `ConnectorSyncJob` |
| `last_failed_sync()` | Latest FAILED job |
| `current_health()` | `pending` / `healthy` / `degraded` / `unhealthy` / `expired` / `revoked` |

API exposes `health` on installation GET (timestamps + truncated last error; no secrets).

### Error classification (Phase 4.3.7)

| Exception | Sync behavior |
|-----------|----------------|
| `AuthenticationError` | Expire installation + fail job |
| `TemporaryConnectorError` | Reset job to PENDING + Celery retry |
| `PermanentConnectorError` | Fail job (installation → `error` unless already expired/revoked) |

### OAuth state (`OAuthStateService`)

Shared signed state binds `organization` + `installation` + `connector` + nonce.
Callback validation consumes the nonce (cache) to prevent replay.

### Security rules

- Never return tokens, ciphertext, or raw config secrets from the API
- Never log access/refresh tokens
- No token read endpoint
- Decrypt only during connector execution (`BaseConnector._decrypt_*`)
- Key material derived from Django ``SECRET_KEY`` (same Fernet scheme as provider API keys)


## Installation lifecycle

``ConnectorInstallation`` (org-scoped):

- `connector_type` — registry key
- `name` — unique per organization
- `status` — `pending` \| `active` \| `expired` \| `revoked` \| `error`
- `config` — JSON (secrets write-only / redacted in Admin)

Sync is allowed for `active` and recoverable `error` only. `pending` /
`expired` / `revoked` cannot start sync. Failed syncs may set status to `error`.

Legacy `disabled` rows are migrated to `revoked`.


## Sync lifecycle

```text
ConnectorSyncService.start_sync(installation)   # manual / API
  → ConnectorSyncJob (PENDING)
  → event connector.sync.started
  → Celery: sync_connector_installation
       → RUNNING
       → connector.validate_credentials() + sync()
       → COMPLETED | FAILED
       → event connector.sync.completed | connector.sync.failed
```

Manual ``POST .../sync/`` always creates a new job (unchanged).


## Periodic scheduling (Phase 4.3.4)

Celery Beat entry ``turing-schedule-connector-syncs`` runs
``schedule_connector_syncs``, which:

1. Discovers org-scoped installations with status ``active`` or ``error``
   on active organizations (`pending` / `expired` / `revoked` excluded)
2. Calls ``start_sync_if_idle`` — skips when a ``PENDING``/``RUNNING`` job exists
3. Enqueues through existing ``ConnectorSyncService`` / ``sync_connector_installation``

Failed jobs (`FAILED`) and installations in ``error`` do **not** block the next
Beat tick. Concurrent schedulers use ``select_for_update`` on the installation.

### Settings

| Setting | Default | Meaning |
|---------|---------|---------|
| `TURING_CONNECTOR_SYNC_ENABLED` | `true` | When `false`, Beat omits the connector entry (outbox schedule unchanged) |
| `TURING_CONNECTOR_SYNC_INTERVAL_SECONDS` | `3600` | Beat interval (seconds) |

Outbox and connector Beat features are independently toggleable.

### Observability

Logs include installation id, connector type, sync job id, start/end, and
failure reason. Config secrets are never logged.


## Zoom connector (Phase 4.3.3 / 4.3.6)

Pulls Zoom cloud recordings into Turing via **OAuth2** (not static api_token).

### App setup

Configure the Zoom OAuth app and Turing env:

| Setting | Purpose |
|---------|---------|
| `TURING_ZOOM_CLIENT_ID` | Zoom app client id |
| `TURING_ZOOM_CLIENT_SECRET` | Zoom app client secret |
| `TURING_ZOOM_OAUTH_REDIRECT_URI` | Must match Zoom app redirect URL |
| `TURING_ZOOM_OAUTH_SCOPES` | Default `recording:read user:read:user` |

Redirect URI example:

`https://<host>/api/turing/v1/oauth/callback/zoom/`

Optional installation `config`: `account_id`, `base_url` (metadata / API base only).

### OAuth lifecycle

```text
POST /connector-installations/  (connector_type=zoom) → status pending
GET  /connector-installations/{id}/authorize/ → authorization_url (+ signed state)
  → user consents at Zoom
GET  /oauth/callback/zoom/?code=&state=
  → exchange_code() → CredentialEncryptionService / store_credentials()
  → ConnectorInstallationService.activate() → status active
```

Authorize requires `manage_config` and org-scoped installation access.
Callback is unauthenticated; ownership is enforced by signed `state`
(`installation_id` + `organization_id`). Tokens are never returned.

Revoke: `PATCH ... {"status": "revoked"}` → Zoom revoke API (best effort) + clear ciphertext.

### Token refresh during sync

Before pull/sync, `ensure_fresh_credentials()`:

1. If access token expires within 60s → `refresh_credentials()`
2. Persist new tokens via `store_credentials()`
3. On refresh failure → `expire()` installation + raise → sync job `FAILED`
   (`connector.sync.failed`); status stays `expired` (not overwritten to `error`)

### Sync flow

```text
ZoomConnector.pull_media()
  → ensure_fresh_credentials()
  → ZoomClient.list_recordings()  (Bearer access token)
  → prefer M4A/MP3 over MP4; skip chat/transcript artifacts

ZoomConnector.sync()
  → skip if ExternalReference(zoom/meeting/<recording_id>) already exists
  → MediaService.create_from_url(media_url, use_case=meeting)
  → ExternalReference attach (zoom / meeting / recording_id)
```

Mapping:

| Field | Value |
|-------|--------|
| `external_system` | `zoom` |
| `external_type` | `meeting` |
| `external_id` | Zoom recording file id |
| `media_url` | Zoom `download_url` |


## REST API (Phase 4.3.2 / 4.3.5 / 4.3.6)

Requires capability `manage_config` (org Admin role). Config secrets and OAuth
tokens are accepted on write paths only and **never** returned in responses.
GET returns ``auth_status`` (type, has_credentials, expires_at, is_expired) — not
tokens. PATCH ``{"status": "revoked"}`` runs the revoke lifecycle (clears
credentials). There is no token read endpoint.

### Catalog

```http
GET /api/turing/v1/connectors/
```

```json
[
  {
    "type": "zoom",
    "name": "Zoom",
    "auth_type": "oauth2",
    "supports_oauth": true,
    "supports_refresh": true,
    "supports_revoke": true,
    "supported_sync_types": ["media"],
    "available": true
  }
]
```

Source: ``ConnectorRegistry`` (no hardcoded vendor list).

### Installations

| Method | Path |
|--------|------|
| `GET`/`POST` | `/api/turing/v1/connector-installations/` |
| `GET`/`PATCH`/`DELETE` | `/api/turing/v1/connector-installations/{id}/` |
| `GET` | `/api/turing/v1/connector-installations/{id}/authorize/` |
| `POST` | `/api/turing/v1/connector-installations/{id}/sync/` → `202` |
| `GET` | `/api/turing/v1/oauth/callback/{connector}/` |

Create (Zoom → `pending` until OAuth completes):

```http
POST /api/turing/v1/connector-installations/
Content-Type: application/json

{
  "connector_type": "zoom",
  "name": "Company Zoom",
  "config": {}
}
```

Authorize response:

```json
{
  "authorization_url": "https://zoom.us/oauth/authorize?...",
  "installation_id": "<uuid>",
  "connector_type": "zoom"
}
```

Sync trigger response:

```json
{ "sync_job_id": "<uuid>" }
```

### Sync jobs

```http
GET /api/turing/v1/connector-sync-jobs/{id}/
```

Exposes status, timestamps, `records_processed`, and `error` (org-scoped).


## Events (IDs only)

| Event | Meaning |
|-------|---------|
| `connector.sync.started` | Sync job created |
| `connector.sync.completed` | Sync finished successfully |
| `connector.sync.failed` | Sync failed (payload has `error_code`, not stack traces) |
| `media.created` | Emitted by ``MediaService`` when Zoom ingest creates media |

Emitted via existing ``EventBus`` + durable outbox.


## Future provider adapters

Still planned (not in this phase):

- Microsoft Teams / Google Meet
- CRM call attachment connectors
- Telephony / contact center recording ingest

Each adapter lives under ``turing/connectors/<vendor>/`` and registers via
``register_builtin_connectors()`` without changing ``ConnectorSyncService``.
