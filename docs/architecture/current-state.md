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
- Internal event foundation
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


## Current Limitations (post–Phase 4.1)

- No connector framework (Zoom / CRM / telephony)
- No outbound webhook / event outbox delivery
- UI is not productized
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

Host applications can link objects, read analyses, and subscribe in-process to
domain events. Custom glue is still needed for out-of-process callbacks and
connectors (Phase 4.2+).
