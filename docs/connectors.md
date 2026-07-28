# Connector framework (Phase 4.3)

Generic architecture for pulling media / syncing state from external systems
into Turing.


## Package layout

```text
turing/connectors/
  base.py          BaseConnector contract
  registry.py      ConnectorRegistry
  exceptions.py    Connector* errors
  builtins.py      register_builtin_connectors()
  zoom/            Zoom Cloud Recording connector
    client.py
    connector.py
    serializers.py
```


## Connector contract

Every connector implements ``BaseConnector``:

| Method | Purpose |
|--------|---------|
| `name` | Human-readable / registry label |
| `validate_config()` | Fail closed on bad installation config |
| `health_check()` | Lightweight connectivity probe (no secrets in result) |
| `pull_media()` | Discover remote media as ``MediaPullItem`` descriptors |
| `sync()` | Full sync pass → ``ConnectorSyncResult`` (may create media) |

Register with:

```python
from turing.connectors import ConnectorRegistry, BaseConnector

@ConnectorRegistry.register
class ExampleConnector(BaseConnector):
    connector_type = "example"
    display_name = "Example"
    ...
```

Resolve:

```python
ConnectorRegistry.get("zoom")
connector = ConnectorRegistry.create(installation)
```

Core sync services never import vendor SDKs — only ``ConnectorRegistry`` + ``BaseConnector``.


## Installation lifecycle

``ConnectorInstallation`` (org-scoped):

- `connector_type` — registry key
- `name` — unique per organization
- `status` — `active` | `disabled` | `error`
- `config` — JSON (secrets write-only / redacted in Admin)

Disabled installations cannot start sync. Failed syncs may set status to `error`.


## Sync lifecycle

```text
ConnectorSyncService.start_sync(installation)   # manual / API
  → ConnectorSyncJob (PENDING)
  → event connector.sync.started
  → Celery: sync_connector_installation
       → RUNNING
       → connector.validate_config() + sync()
       → COMPLETED | FAILED
       → event connector.sync.completed | connector.sync.failed
```

Manual ``POST .../sync/`` always creates a new job (unchanged).


## Periodic scheduling (Phase 4.3.4)

Celery Beat entry ``turing-schedule-connector-syncs`` runs
``schedule_connector_syncs``, which:

1. Discovers org-scoped installations with status ``active`` or ``error``
   on active organizations (``disabled`` excluded)
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


## Zoom connector (Phase 4.3.3)

First concrete connector. Pulls Zoom cloud recordings and registers them as
Turing media via the existing pipeline.

### Required config

```json
{
  "account_id": "...",
  "api_token": "..."
}
```

Optional: `base_url` (default `https://api.zoom.us/v2/`).

Credentials are write-only (never returned by the installation API, never logged
or included in events).

### Sync flow

```text
ZoomConnector.pull_media()
  → ZoomClient.list_recordings()  (normalized recording files)
  → prefer M4A/MP3 over MP4; skip chat/transcript artifacts

ZoomConnector.sync()
  → skip if ExternalReference(zoom/meeting/<recording_id>) already exists
  → MediaService.create_from_url(media_url, use_case=meeting)
  → ExternalReference attach (zoom / meeting / recording_id)
  → existing media.created event + STT pipeline when jobs are created
```

Mapping:

| Field | Value |
|-------|--------|
| `external_system` | `zoom` |
| `external_type` | `meeting` |
| `external_id` | Zoom recording file id |
| `media_url` | Zoom `download_url` |


## REST API (Phase 4.3.2)

Requires capability `manage_config` (org Admin role). Config secrets are accepted on
write but **never** returned in responses.

### Catalog

```http
GET /api/turing/v1/connectors/
```

```json
[
  {"type": "zoom", "name": "Zoom", "available": true}
]
```

Source: ``ConnectorRegistry`` (no hardcoded vendor list).

### Installations

| Method | Path |
|--------|------|
| `GET`/`POST` | `/api/turing/v1/connector-installations/` |
| `GET`/`PATCH`/`DELETE` | `/api/turing/v1/connector-installations/{id}/` |
| `POST` | `/api/turing/v1/connector-installations/{id}/sync/` → `202` |

Create:

```http
POST /api/turing/v1/connector-installations/
Content-Type: application/json

{
  "connector_type": "zoom",
  "name": "Company Zoom",
  "config": {
    "account_id": "...",
    "api_token": "..."
  }
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
