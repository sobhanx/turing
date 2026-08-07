# Architecture

Turing is a reusable Django speech-intelligence package. Host applications install it as an app, run migrations, and expose the REST API alongside their own product UI.

## Core pipeline

```text
Media upload / connector ingest
    → Audio preparation & normalization
    → STT provider (Speechmatics batch)
    → Transcript (segments, speakers, words)
    → Human review & revisions
    → AI analyses (summary, topics, action items)
```

Processing is asynchronous by default (Celery). See [async-pipeline.md](async-pipeline.md).

## Design principles

- **Transcript is the source of truth** — provider output is persisted, then edited in place with revision history.
- **Media is immutable** — uploads are stored once; derived artifacts (normalized audio, exports) are separate.
- **AI outputs are derived** — analyses never mutate raw transcript content.
- **Organizations isolate data** — memberships and API scoping enforce tenant boundaries. See [authorization-tenancy.md](authorization-tenancy.md).
- **Providers are pluggable** — STT, embeddings, and LLM integrations sit behind service abstractions.

## Related documentation

| Topic | Document |
|-------|----------|
| Current modules & limitations | [architecture/current-state.md](architecture/current-state.md) |
| Provider credential pool (Attempt-sticky) | [architecture/provider-credential-pool.md](architecture/provider-credential-pool.md) |
| Async jobs & idempotency | [async-pipeline.md](async-pipeline.md) |
| Media & object storage | [media-storage.md](media-storage.md) |
| Audio ingestion | [audio-ingestion.md](audio-ingestion.md) |
| Transcript intelligence | [transcript-intelligence.md](transcript-intelligence.md) |
| Connectors | [connectors.md](connectors.md) |
| Events & outbox | [events.md](events.md) |
| Webhooks | [webhooks.md](webhooks.md) |
| Speech Center API | [speech-center-api.md](speech-center-api.md) |
| Semantic search | [search.md](search.md) |
| Deployment | [deployment.md](deployment.md) |
