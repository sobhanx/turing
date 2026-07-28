# Phase 4 Architecture Direction


## Goal

Transform Turing from a transcription pipeline into an integration-ready speech intelligence platform.


## Product Model

Turing is not the main customer UI.

Turing provides a Speech Center capability that is embedded inside host applications.


Examples:

CRM:
Customer
  -> Calls
      -> Transcript
      -> Analysis


Bank:
Case
  -> Recorded Call
      -> Transcript
      -> Compliance Review


HR:
Candidate
  -> Interview
      -> Transcript
      -> Evaluation


## Phase 4 Priorities


## 1. Integration Foundation

Add ability to connect Turing objects with host objects.

Example:

external_system:
crm

external_type:
deal

external_id:
12345


## 2. Public Analysis API

Expose:

- Summary
- Topics
- Action items
- Sentiment (future)

through stable APIs.


## 3. Event Architecture

Introduce internal events:

- media.created
- job.completed
- transcript.created
- analysis.completed


Allow future integrations:
- CRM sync
- Notifications
- Export
- Automation


## 4. Search Foundation

Prepare transcript indexing.

Future:
- semantic search
- vector search
- intelligent retrieval


## 5. Integration Connectors

Future connectors:

- Zoom
- Teams
- CRM
- Telephony systems


## UI Roadmap

Phase 4:
Backend productization

Phase 5:
Standalone Speech Center UI

Phase 6:
Embedded host UI components


## Non Goals

Phase 4 will NOT:

- redesign frontend
- split Django apps
- replace current pipeline
- rewrite providers


## Success Criteria

After Phase 4:

A company can integrate Turing into an existing application without building custom speech infrastructure.