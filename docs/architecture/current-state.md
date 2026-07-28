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

**Phase 4.3.1–4.3.7 connector framework through hardening are in place.**

Includes:

- `BaseConnector` capabilities + auth hooks + classified sync errors
- `ConnectorInstallation` / `ConnectorSyncJob` / `ConnectorCredential` + Admin
- `CredentialEncryptionService` + `ConnectorInstallationService` lifecycle
- `OAuthStateService` (signed state, replay protection)
- Derived installation health (`current_health`, last success/failure)
- REST: catalog capabilities, authorize, OAuth callback, auth_status, health, sync
- Zoom OAuth2 with automatic token refresh
- Celery Beat scheduling + temporary sync retries


## Current Limitations (post–Phase 4.3.7)

- No marketplace / product UI for connector install
- No Teams / Meet / CRM / telephony connectors yet
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
