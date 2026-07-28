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
  teams/           Microsoft Teams meeting recordings (oauth2)
    client.py
    connector.py
    oauth.py
    serializers.py
  google_meet/     Google Meet recordings via Drive (oauth2)
    client.py
    connector.py
    oauth.py
    serializers.py
  salesforce/      Salesforce CRM call/meeting recordings (oauth2)
    client.py
    connector.py
    oauth.py
    serializers.py
  telephony/       Generic telephony / CTI call-recording foundation
    connector.py   TelephonyConnector abstract base
    serializers.py TelephonyCall + normalize_call()
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


## Microsoft Teams connector (Phase 4.3.8)

Pulls Teams / Graph online-meeting recordings into Turing via **OAuth2**.

### App setup

| Setting | Purpose |
|---------|---------|
| `TURING_TEAMS_CLIENT_ID` | Azure AD app client id |
| `TURING_TEAMS_CLIENT_SECRET` | Azure AD app client secret |
| `TURING_TEAMS_OAUTH_REDIRECT_URI` | Must match Azure redirect URI |
| `TURING_TEAMS_OAUTH_SCOPES` | Default includes `OnlineMeetings.Read`, `OnlineMeetingRecording.Read.All`, `offline_access` |

Redirect URI example:

`https://<host>/api/turing/v1/oauth/callback/teams/`

### Flow

```text
POST /connector-installations/  (connector_type=teams) → pending
GET  .../authorize/ → Microsoft authorize URL (OAuthStateService)
GET  /oauth/callback/teams/?code=&state=
  → exchange_code → store_credentials → activate
TeamsConnector.sync()
  → ensure_fresh_credentials()
  → TeamsClient.list_recordings()  (Graph /me/onlineMeetings + recordings)
  → MediaService.create_from_url
  → ExternalReference(teams / meeting / <recording_id>)
```

Mapping:

| Field | Value |
|-------|--------|
| `external_system` | `teams` |
| `external_type` | `meeting` |
| `external_id` | Graph recording id |
| `media_url` | `recordingContentUrl` |


## Google Meet connector (Phase 4.3.9)

Pulls Google Meet recordings (stored in Drive) into Turing via **OAuth2**.

### App setup

| Setting | Purpose |
|---------|---------|
| `TURING_GOOGLE_MEET_CLIENT_ID` | Google OAuth client id |
| `TURING_GOOGLE_MEET_CLIENT_SECRET` | Google OAuth client secret |
| `TURING_GOOGLE_MEET_OAUTH_REDIRECT_URI` | Must match Google redirect URI |
| `TURING_GOOGLE_MEET_OAUTH_SCOPES` | Default includes Drive readonly + openid |

Redirect URI example:

`https://<host>/api/turing/v1/oauth/callback/google_meet/`

### Flow

```text
POST /connector-installations/  (connector_type=google_meet) → pending
GET  .../authorize/ → Google authorize URL (offline access + consent)
GET  /oauth/callback/google_meet/?code=&state=
  → exchange_code → store_credentials → activate
GoogleMeetConnector.sync()
  → ensure_fresh_credentials()
  → GoogleMeetClient.list_recordings()  (Drive files.list)
  → MediaService.create_from_url
  → ExternalReference(google_meet / meeting / <file_id>)
```

Mapping:

| Field | Value |
|-------|--------|
| `external_system` | `google_meet` |
| `external_type` | `meeting` |
| `external_id` | Drive file id |
| `media_url` | Drive `webContentLink` |


## Salesforce connector (Phase 4.3.10)

First CRM connector. Discovers Salesforce call/meeting records that include a
recording URL and ingests them into Turing via **OAuth2**.

### App setup

| Setting | Purpose |
|---------|---------|
| `TURING_SALESFORCE_CLIENT_ID` | Connected App consumer key |
| `TURING_SALESFORCE_CLIENT_SECRET` | Connected App consumer secret |
| `TURING_SALESFORCE_OAUTH_REDIRECT_URI` | Must match Connected App callback |
| `TURING_SALESFORCE_OAUTH_SCOPES` | Default `api refresh_token offline_access` |

Redirect URI example:

`https://<host>/api/turing/v1/oauth/callback/salesforce/`

OAuth token response ``instance_url`` is stored in credential metadata (not a
secret) and used as the REST API base.

### Flow

```text
POST /connector-installations/  (connector_type=salesforce) → pending
GET  .../authorize/ → Salesforce authorize URL
GET  /oauth/callback/salesforce/?code=&state=
  → exchange_code → store_credentials (+ instance_url) → activate
SalesforceConnector.sync()
  → ensure_fresh_credentials()
  → SalesforceClient.list_recordings()  (VoiceCall SOQL, Task fallback)
  → MediaService.create_from_url (crm_call | meeting)
  → ExternalReference(salesforce / call|meeting / <Id>)
```

No CRM write-back in this phase.

Mapping:

| Field | Value |
|-------|--------|
| `external_system` | `salesforce` |
| `external_type` | `call` or `meeting` |
| `external_id` | Salesforce record `Id` |
| `media_url` | `RecordingUrl` / custom link fields |


## Telephony connector model (Phase 4.4.3)

Generic CTI / contact-center foundation. No enterprise vendor adapters ship
yet — subclass ``TelephonyConnector`` and register with ``ConnectorRegistry``.

### Contract

| Method | Purpose |
|--------|---------|
| `list_calls()` | Discover call recordings |
| `get_recording(call_id)` | Fetch one call descriptor |
| `normalize_call(raw)` | Normalize vendor payload → ``TelephonyCall`` |
| `sync()` | Ingest via MediaService + ExternalReference |

Marketplace metadata (on the base class):

| Field | Value |
|-------|--------|
| `category` | `telephony` |
| `supported_sync_types` | `["calls"]` |
| `auth_type` | `api_key` (CTI adapters may switch to oauth2) |
| `capabilities` | oauth/refresh/revoke default false |
| `installation_requirements` | `api_token` (secret) + host checklist messages |

### Normalized call

```json
{
  "external_system": "telephony",
  "external_type": "call",
  "external_id": "call-100",
  "recording_url": "https://cdn.example/calls/100.mp3",
  "caller": "+15551110000",
  "callee": "+15552220000",
  "started_at": "2026-01-01T10:00:00Z",
  "duration": 120,
  "metadata": {"queue": "support"}
}
```

### Sync flow

```text
TelephonyConnector.list_calls()
  → MediaService.create_from_url (use_case=crm_call)
  → ExternalReference(telephony / call / <id>)
  → existing STT pipeline
```

Events reused: ``connector.sync.*``, ``media.created``.

Out of scope for this foundation: real-time streaming, agent desktop UI, QA
scoring, and specific enterprise CTI providers.


## REST API (Phase 4.3.2 / 4.3.5 / 4.3.6 / 4.4.1 / 4.4.2)

Requires capability `manage_config` (org Admin role). Config secrets and OAuth
tokens are accepted on write paths only and **never** returned in responses.

Installation GET/list contract (UI-ready):

| Field | Notes |
|-------|--------|
| `id` | UUID |
| `connector_type` | Registry key |
| `name` | Display label |
| `status` | `pending` / `active` / `expired` / `revoked` / `error` |
| `auth_status` | `auth_type`, `has_credentials`, `expires_at`, `is_expired`, `status` |
| `health` | Derived: `current_health`, last success/fail timestamps + truncated error |
| `last_sync` | Latest sync job summary, or `null` |
| `created_at` / `updated_at` | Timestamps |

Never exposed: credentials, tokens, secrets, raw provider `config`.

### Connector definition model (Phase 4.4.2)

Marketplace metadata is declared on each connector class and surfaced as a
``ConnectorDefinition`` (never secrets):

| Field | Purpose |
|-------|---------|
| `connector_type` | Registry key |
| `display_name` | Product label |
| `description` | Short marketplace blurb |
| `provider` | Vendor / publisher name |
| `category` | `meetings` / `crm` / `telephony` / `other` |
| `documentation_url` | Optional docs link |
| `icon_url` | Optional icon URL |
| `auth_type` | `oauth2` or `api_key` |
| `capabilities` | `{oauth, refresh, revoke}` |
| `supported_sync_types` | e.g. `["media"]` |
| `required_scopes` | OAuth scopes the connector needs |
| `installation_requirements` | Structured install schema (below) |

Registry APIs:

| Method | Role |
|--------|------|
| `list_available()` / `list_definitions()` | Catalog discovery |
| `get_definition(type)` | Single connector metadata |
| `validate_installation_requirements(type, config, scopes_granted=…)` | Pre-install checks |

### Installation requirements schema

```json
{
  "oauth_scopes": ["recording:read", "user:read"],
  "config_fields": [
    {
      "key": "account_id",
      "label": "Account ID",
      "required": true,
      "secret": false,
      "description": "",
      "validation_message": "Account ID is required."
    }
  ],
  "messages": [
    "Configure OAuth client credentials on the host."
  ]
}
```

``secret: true`` means the host UI should treat the field as sensitive input —
responses never include secret *values*.

### Catalog

```http
GET /api/turing/v1/connectors/
```

```json
[
  {
    "connector_type": "zoom",
    "display_name": "Zoom",
    "provider": "Zoom",
    "description": "Sync Zoom cloud meeting recordings into Turing for transcription.",
    "category": "meetings",
    "documentation_url": "https://developers.zoom.us/...",
    "icon_url": "",
    "auth_type": "oauth2",
    "capabilities": {
      "oauth": true,
      "refresh": true,
      "revoke": true
    },
    "supported_sync_types": ["media"],
    "required_scopes": ["recording:read", "user:read:user"],
    "installation_requirements": {
      "oauth_scopes": ["recording:read", "user:read:user"],
      "config_fields": [],
      "messages": [
        "Configure Zoom OAuth client settings on the host (TURING_ZOOM_*)."
      ]
    }
  }
]
```

Source: ``ConnectorRegistry.list_available()`` ← ``BaseConnector.definition()``
(no hardcoded vendor list, no secrets).

### Installations

| Method | Path |
|--------|------|
| `GET`/`POST` | `/api/turing/v1/connector-installations/` |
| `GET`/`PATCH`/`DELETE` | `/api/turing/v1/connector-installations/{id}/` |
| `GET` | `/api/turing/v1/connector-installations/{id}/authorize/` |
| `POST` | `/api/turing/v1/connector-installations/{id}/activate/` |
| `POST` | `/api/turing/v1/connector-installations/{id}/revoke/` |
| `POST` | `/api/turing/v1/connector-installations/{id}/sync/` → `202` |
| `GET` | `/api/turing/v1/oauth/callback/{connector}/` |

List filters (Phase 4.4.1):

| Query param | Meaning |
|-------------|---------|
| `connector_type` | Exact registry type |
| `status` | Exact installation status |
| `health` | Derived: `pending` / `healthy` / `degraded` / `unhealthy` / `expired` / `revoked` |
| `created_at__gte` / `created_at__lte` | Created range |
| `created_at__date` | Calendar day |

Lifecycle actions use ``ConnectorInstallationService`` (org-scoped via
``get_object()``), emit domain events, and return the public installation
serializer (no secrets). PATCH ``{"status": "revoked"}`` still runs the revoke
lifecycle. There is no token read endpoint.

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

Activate / revoke responses return the full public installation payload.

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
| `connector.installation.activated` | Installation marked active |
| `connector.installation.revoked` | Installation revoked (tokens cleared) |
| `connector.sync.started` | Sync job created |
| `connector.sync.completed` | Sync finished successfully |
| `connector.sync.failed` | Sync failed (payload has `error_code`, not stack traces) |
| `media.created` | Emitted by ``MediaService`` when connector ingest creates media |

Emitted via existing ``EventBus`` + durable outbox.


## Future provider adapters

Still planned (not in this phase):

- Enterprise CTI providers (Genesys, Twilio, etc.) on ``TelephonyConnector``
- Marketplace / product UI (consumes Phase 4.4 catalog contracts)
- Billing / entitlements / app publishing
- CRM write-back / action-item sync

Each adapter lives under ``turing/connectors/<vendor>/`` and registers via
``register_builtin_connectors()`` without changing ``ConnectorSyncService``.
