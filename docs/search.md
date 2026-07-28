# Semantic search (Phase 4.5.3 / 4.5.4)

Provider-agnostic indexing for Speech Center transcript segments, with
**PostgreSQL pgvector** as the first production-oriented vector backend.


## Architecture

```text
transcript.created / analysis.completed
        │
        ▼ (EventBus — failures isolated)
SearchIndexService.index_transcript()
        │
        ├─ content_hash unchanged → metadata refresh only
        └─ else → SemanticSearchProvider.index_document()
                    │
                    ├─ PgVectorSearchProvider (default) — embed + persist vector
                    └─ NullSemanticSearchProvider — Embedding row, empty vector
```

Core services never import a vendor SDK — only ``SemanticSearchRegistry`` +
``SemanticSearchProvider``.


## Package layout

```text
turing/search/
  base.py              SemanticSearchProvider + SearchDocument / SearchHit
  registry.py          SemanticSearchRegistry
  embedder.py          Deterministic hashing-trick embedder (no LLM)
  exceptions.py
  handlers.py
  providers/
    pgvector.py        PgVectorSearchProvider
  __init__.py          register_builtin_search_providers()

turing/models/embedding.py
turing/services/search_index.py
```


## PgVector provider

``PgVectorSearchProvider`` (``code=pgvector``):

| Method | Behavior |
|--------|----------|
| `index_document` | Embed text → upsert org-scoped ``Embedding`` with float vector |
| `delete_document` | Delete Embedding row (org-scoped when `organization_id` set) |
| `search` | Cosine rank within **one** organization only |

Vectors are stored as JSON float lists on ``Embedding.vector`` so the same
path works on **SQLite (tests)** and **PostgreSQL**. Optional SQL distance via
the Postgres ``vector`` extension can be enabled separately (see below).

Default embedder is a deterministic local hashing trick (no OpenAI/LLM call).
Hosts can later wrap a model-backed embedder without changing the provider
interface.


## Configuration

| Setting | Default | Notes |
|---------|---------|--------|
| `TURING_SEARCH_PROVIDER` | `pgvector` | Registry code (`pgvector` or `null`) |
| `TURING_SEARCH_EMBEDDING_DIMS` | `256` | Vector dimensionality |
| `TURING_SEARCH_PGVECTOR_SQL` | `false` | Use Postgres `<=>` distance when extension exists |

```bash
export TURING_SEARCH_PROVIDER=pgvector
export TURING_SEARCH_EMBEDDING_DIMS=256
# Optional (Postgres + CREATE EXTENSION vector):
# export TURING_SEARCH_PGVECTOR_SQL=true
```

Fallback: set ``TURING_SEARCH_PROVIDER=null`` to keep Embedding rows without
ranking (search API returns empty ``results`` with ``provider: "null"``).


## Migration notes

- **0019** — creates ``Embedding`` (org, object_type/id, content_hash, vector JSON, metadata)
- **0020** — adds ``dimensions``; updates vector help text; best-effort
  ``CREATE EXTENSION IF NOT EXISTS vector`` on PostgreSQL (no-op if missing
  privileges or on SQLite)

Re-index after switching from null → pgvector (or changing dims) so vectors
are populated:

```python
from turing.services.search_index import SearchIndexService
SearchIndexService().index_transcript(transcript)
```


## Embedding model

| Field | Notes |
|-------|--------|
| `organization` | Data boundary (required) |
| `object_type` / `object_id` | Unique per org |
| `content_hash` | SHA-256 of indexed text (skip re-embed when unchanged) |
| `vector` | pgvector-compatible float list |
| `dimensions` | Embedding size (0 for null provider rows) |
| `metadata` | transcript_id, segment_id, speaker, start/end_ms, text, external_references |


## Indexing rules

``SearchIndexService`` indexes **segments**.

- Same ``content_hash`` → metadata refresh only (no duplicate embedding)
- Hash change → provider re-embeds and upserts
- Failures on EventBus handlers are logged and never block STT/analysis


## API

```http
GET /api/turing/v1/search/?q=renewal&external_system=salesforce&external_type=call&limit=20
```

Requires ``view_transcript``. Response:

```json
{
  "results": [
    {
      "transcript_id": "...",
      "segment_id": "...",
      "speaker": "S1",
      "start_time": 0,
      "end_time": 2000,
      "text": "Discuss renewal pricing today.",
      "score": 0.82,
      "external_references": [
        {"external_system": "salesforce", "external_type": "call", "external_id": "SF-1"}
      ]
    }
  ],
  "provider": "pgvector"
}
```

``start_time`` / ``end_time`` are milliseconds (same units as segment
``start_ms`` / ``end_ms``). External filters are applied **inside** the
resolved organization — never across tenants.


## Security

- Every search requires a resolved organization + ``view_transcript``
- Vector lookup is always ``organization_id``-scoped
- External reference filters cannot escape the org boundary


## Other backends

Still pluggable via ``SemanticSearchRegistry``:

- OpenSearch / Elasticsearch k-NN
- Managed: Pinecone, Weaviate, Qdrant

Out of scope: RAG/chat assistants, sentiment, frontend UI, LLM workflow changes.
