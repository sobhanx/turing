# Turing Current Architecture

## Overview

Turing is a reusable Django speech intelligence platform.

Current flow:

Media
 ↓
Ingestion
 ↓
STT Provider
 ↓
Transcript
 ↓
Review
 ↓
AI Analysis


## Current Modules

- Media management
- Audio ingestion
- STT pipeline
- Transcript management
- Review workflow
- AI analysis
- External object references (host linking)
- Public analysis API (including latest-per-type)
- Internal event foundation + durable outbox
- Outbound signed webhooks (subscriptions + deliveries)
- Connector framework foundation (installations + sync jobs)
- Speech Center backend API (host lookup + timeline aggregation)
- Multi-organization support
- Admin panel
- REST API
- Celery async processing


## Current Architecture Decisions

- Transcript is source of truth
- Original media is immutable
- AI outputs are derived data
- STT providers are abstracted
- Organizations isolate customer data


## Phase 4.1 status

**Phase 4.1 host integration foundation is complete.**

Includes:

- `ExternalReference` model, service, REST, Admin, and media-create convenience
- Public Analysis API (`/analyses/`, nested list, latest-per-type)
- Internal event bus (`media.created`, `job.completed`, `transcript.created`, `analysis.completed`)
- Host-key filters on media/transcripts


## Phase 4.2 status

**Phase 4.2.1–4.2.4 outbox reliability + outbound webhooks (incl. public API) are in place.**

Includes:

- Durable `OutboxEvent` written after commit via `EventBus`
- Celery Beat schedule for `dispatch_outbox_events` (+ stuck recovery)
- `WebhookSubscription` / `WebhookDelivery` with signed HTTP delivery + retries
- Stuck PROCESSING/DELIVERING recovery (`processing_started_at`, `recovery_count`)
- Retry policy (429/5xx/network yes; 400/401/403/404 no)
- Admin filters + `OutboxOpsService` operational queries
- Public REST API for webhook subscription CRUD + delivery listing


## Phase 4.3 status

**Phase 4.3.1–4.3.10 connector framework through Salesforce CRM are in place.**

Includes:

- `BaseConnector` capabilities + auth hooks + classified sync errors
- `ConnectorInstallation` / `ConnectorSyncJob` / `ConnectorCredential` + Admin
- `CredentialEncryptionService` + `ConnectorInstallationService` lifecycle
- `OAuthStateService` (signed state, replay protection)
- Derived installation health (`current_health`, last success/failure)
- REST: catalog capabilities, authorize, OAuth callback, auth_status, health, sync
- Zoom, Teams, Google Meet, and Salesforce OAuth2 connectors
- Celery Beat scheduling + temporary sync retries


## Phase 4.4 status

**Phase 4.4.1–4.4.4 installation API, marketplace catalog, telephony foundation,
and Twilio connector are in place.**

Includes:

- UI-ready installation serializer (`auth_status`, `health`, `last_sync`; no secrets)
- Catalog contract: `connector_type`, `display_name`, `capabilities`,
  `supported_sync_types`, `installation_requirements`
- Explicit `activate` / `revoke` / `sync` actions + installation lifecycle events
- List filters: `connector_type`, `status`, `health`, `created_at`
- `ConnectorDefinition` marketplace metadata (provider, description, category,
  scopes, structured install requirements)
- Registry `get_definition` / `validate_installation_requirements`
- `TelephonyConnector` + `TelephonyCall` normalization / MediaService ingest path
- Twilio call-recording connector (`api_key`, `ExternalReference(twilio/call/…)`)


## Current Limitations (post–Phase 4.4.4)

- No marketplace / product UI for connector install
- No billing / entitlement / payments / app publishing
- No CRM write-back / action-item sync
- No additional enterprise CTI adapters beyond Twilio
- No real-time streaming / agent desktop / QA scoring
- Permissions are organization-level (no record-level ACL)
- Celery coupling exists inside services


## Phase 4.5 status

**Phase 4.5.1 Speech Center backend API foundation is in place.**

Includes:

- `SpeechCenterService` (external lookup, transcript context, intelligence, timeline)
- `GET /api/turing/v1/speech-center/` host-object aggregation
- `GET /api/turing/v1/speech-center/{transcript_id}/timeline/`
- Docs: `docs/speech-center-api.md`


## Current Limitations (post–Phase 4.5.1)

- No frontend Speech Center UI
- No vector / semantic search
- No sentiment analysis
- No marketplace / product UI for connector install
- No billing / entitlement / payments / app publishing
- No CRM write-back / action-item sync
- Permissions are organization-level (no record-level ACL)
- Celery coupling exists inside services


## Current Product Shape

Turing today is an installable Django application.

It can power:
- CRM speech features
- Banking call analysis
- HR interview processing
- Meeting intelligence
- Call center QA

Host applications can link objects, read analyses, subscribe in-process to
domain events, receive signed outbound webhooks from durable outbox dispatch,
and sync media from connector installations (manual API or periodic Beat).
