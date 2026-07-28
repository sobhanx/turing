# Phase 4 Architecture Decisions

## Purpose

This document records the architectural decisions locked for Phase 4 implementation.

The goal of Phase 4 is to evolve Turing from an internal speech pipeline into a reusable integration platform for external host applications.

---

# Decision 1 — Host Object Linking

## Decision

Introduce a dedicated `ExternalReference` model.

Do not use metadata JSON fields as the primary integration mechanism.

## Reason

Turing will be embedded into different host applications:

- CRM → Deal / Contact / Call
- Banking → Case / Customer interaction
- HR → Candidate / Interview
- Meetings → Meeting record

Each host needs a stable way to map its objects to Turing objects.

## Model direction

External reference contains:

- organization
- external_system
- external_type
- external_id
- target object (media/transcript)

Example:

crm / deal / 12345

## Rejected alternatives

### Metadata only

Rejected because:
- not queryable
- no indexing
- difficult for integrations

### Adding columns to every model

Rejected because:
- creates duplication
- limits future integrations

---

# Decision 2 — Public Analysis API

## Decision

Expose transcript intelligence through REST API.

Initial scope:

- summary
- topics
- action items

## Reason

Hosts should consume intelligence without accessing:

- database
- Django admin
- internal services

## API direction

Example:

GET

/api/turing/v1/transcripts/{id}/analyses/


Response contains:

- analysis_type
- content
- created_at
- provider information

## Rules

AI output is:

- append-only
- derived data
- never modifies transcript source

---

# Decision 3 — Event Foundation

## Decision

Create an internal event system.

Events are notifications, not replacements for Celery.

## Initial events

- media.created
- job.completed
- transcript.created
- analysis.completed

## Rules

Events must:

- emit after database commit
- not contain sensitive transcript content
- not break processing pipeline

## Future usage

Events will support:

- CRM callbacks
- connector integrations
- outbound webhooks
- automation workflows

---

# Decision 4 — No Pipeline Rewrite

## Decision

Phase 4 extends the existing architecture.

Do not redesign:

- STT pipeline
- Celery workflow
- provider architecture
- transcript model
- storage layer

## Reason

Current foundation is sufficient.

Phase 4 focuses on productization and integrations.

---

# Decision 5 — Speech Center Philosophy

## Decision

Turing is not a standalone destination UI.

It appears inside host applications as a Speech Center.

Examples:

CRM:
Deal → Calls → Transcript

Bank:
Case → Evidence → Transcript

HR:
Interview → Transcript

Meeting:
Meeting → Recording → Transcript

## Principle

The host owns the user experience.

Turing owns:

- speech processing
- transcript lifecycle
- intelligence generation
- integration APIs

---

# Decision 6 — Scope Control

Phase 4.1 includes:

YES:
- External references
- Analysis API
- Event foundation

NO:
- Frontend Speech Center
- UI/UX redesign
- Zoom connector
- CRM connector
- Sentiment analysis
- Vector search
- Multi-app extraction

These belong to later phases.

---

# Final Phase 4 Rule

Build Turing as an integration-ready speech intelligence engine, not as a single company's dashboard.

Every feature added in Phase 4 must answer:

"Can another organization install this and connect it to their own workflow?"