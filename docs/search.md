# Semantic search (Phase 4.5.3)

Provider-agnostic foundation for indexing Speech Center objects (transcript
segments) and querying them later. No production vector vendor is locked in.


## Architecture

```text
transcript.created / analysis.completed
        │
        ▼ (EventBus — failures isolated)
SearchIndexService.index_transcript()
        │
        ├─► Embedding rows (org-scoped, provider-neutral)
        └─► SemanticSearchProvider.index_document()
                    │
                    └─► NullSemanticSearchProvider (default no-op)
                        or future pgvector / OpenSearch / Pinecone adapter
```

Core services never import a vendor SDK — only ``SemanticSearchRegistry`` +
``SemanticSearchProvider``.


## Package layout

```text
turing/search/
  base.py        SemanticSearchProvider + SearchDocument / SearchHit
  registry.py    SemanticSearchRegistry
  exceptions.py  SemanticSearch* errors
  handlers.py    EventBus subscribers (non-blocking)
  __init__.py    register_builtin_search_providers()

turing/models/embedding.py   Embedding (vector JSON placeholder)
turing/services/search_index.py
```


## Provider abstraction

```python
class SemanticSearchProvider(ABC):
    code: str
    def index_document(self, document: SearchDocument) -> None: ...
    def delete_document(self, document_id: str, *, organization_id=None) -> None: ...
    def search(self, query, *, organization_id, limit=20, filters=None) -> SearchResult: ...
```

Default: ``NullSemanticSearchProvider`` (``code=null``) — no remote index.

Register a future backend:

```python
@SemanticSearchRegistry.register
class PgvectorSearchProvider(SemanticSearchProvider):
    code = "pgvector"
    ...
SemanticSearchRegistry.set_default("pgvector")
```


## Embedding model

Org-scoped, provider-neutral:

| Field | Notes |
|-------|--------|
| `organization` | Data boundary |
| `object_type` | e.g. `transcript_segment` |
| `object_id` | Segment UUID string |
| `content_hash` | SHA-256 of indexed text |
| `vector` | JSON list placeholder (empty until a provider fills it) |
| `metadata` | transcript_id, media_id, speaker, timestamps, external_references |


## Indexing rules

``SearchIndexService`` indexes **segments**, not full transcript text alone.

Each chunk metadata includes:

- `transcript_id`
- `media_id`
- `speaker`
- `start_ms` / `end_ms`
- `external_references` snapshot


## Events

Subscribed (in-process ``EventBus``):

- `transcript.created` → index segments
- `analysis.completed` → re-index (metadata refresh)

Failures are logged and swallowed — they never block STT or analysis.


## API foundation

```http
GET /api/turing/v1/search/?q=renewal&external_system=salesforce&external_type=call&external_id=SF-1
```

Requires ``view_transcript``. Placeholder response:

```json
{
  "results": [],
  "provider": null,
  "indexed": false
}
```

``indexed`` becomes ``true`` when the org has at least one ``Embedding`` row.
Query params are accepted for forward compatibility.


## Future vector providers

Planned adapters (not in this phase):

- PostgreSQL + pgvector
- OpenSearch / Elasticsearch k-NN
- Managed: Pinecone, Weaviate, Qdrant

Out of scope here: RAG/chat assistants, sentiment, frontend UI, LLM workflow changes.
