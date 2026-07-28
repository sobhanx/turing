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


## Current Limitations

- No public analysis API
- No external object linking
- No event system
- No connector framework
- UI is not productized
- Permissions are organization-level
- Celery coupling exists inside services


## Current Product Shape

Turing today is an installable Django application.

It can power:
- CRM speech features
- Banking call analysis
- HR interview processing
- Meeting intelligence
- Call center QA

But host applications still need custom integration layers.