# Semantic search (Phase 4.5.3–4.5.5)

Provider-agnostic indexing for Speech Center transcript segments, with
**PostgreSQL pgvector** for vector storage/ranking and a separate
**EmbeddingProvider** for text → vector generation.


## Architecture

```text
transcript.created / analysis.completed
        │
        ▼ (EventBus — failures isolated)
SearchIndexService.index_transcript()
        │
        ├─ content_hash + provider/model unchanged → metadata refresh only
        └─ else
              ├─ EmbeddingProvider.embed(text)     ← local neural (default)
              └─ SemanticSearchProvider.index_document(vector=…)
                    │
                    ├─ PgVectorSearchProvider — persist + cosine search
                    └─ NullSemanticSearchProvider — no remote index
```

Two registries:

| Concern | Registry | Default |
|---------|----------|---------|
| Text → vector | `EmbeddingProviderRegistry` | `local` |
| Store / rank vectors | `SemanticSearchRegistry` | `pgvector` |

Core services never import a vendor SDK.


## Package layout

```text
turing/search/
  base.py / registry.py / handlers.py
  embedder.py              cosine helper (+ legacy hashing util)
  providers/pgvector.py    PgVectorSearchProvider
  embeddings/
    base.py                EmbeddingProvider + NullEmbeddingProvider
    registry.py            EmbeddingProviderRegistry
    local.py               LocalNeuralEmbeddingProvider
    exceptions.py
    __init__.py            register_builtin_embedding_providers()

turing/models/embedding.py
turing/services/search_index.py
```


## Embedding architecture (Phase 4.5.5)

```python
class EmbeddingProvider(ABC):
    def embed(self, text: str) -> list[float]: ...
    def dimensions(self) -> int: ...
    def model_name(self) -> str: ...
```

### Local neural provider

``LocalNeuralEmbeddingProvider`` (``code=local``):

- Real embedding *model interface* (named model + fixed dims + ``embed``)
- No external API / no PyTorch / no network
- Deterministic multi-hash feature → dense projection
- Configurable ``model_name`` (changes the vector space)

Shipped profiles:

| Model | Dims |
|-------|------|
| `turing-local-v1` (default) | 256 |
| `turing-local-small` | 64 |
| `turing-local-large` | 384 |

Unknown model names still embed (dims from ``TURING_SEARCH_EMBEDDING_DIMS``).

### Fallback

Unknown ``TURING_EMBEDDING_PROVIDER`` → ``NullEmbeddingProvider``
(empty vectors; search returns no hits).


## PgVector provider

``PgVectorSearchProvider`` (``code=pgvector``) — unchanged public contract:

| Method | Behavior |
|--------|----------|
| `index_document` | Upsert org-scoped ``Embedding`` (uses EmbeddingProvider vectors) |
| `delete_document` | Delete Embedding row (org-scoped when `organization_id` set) |
| `search` | Cosine rank within **one** organization only |

Query embedding uses the same ``EmbeddingProvider`` as indexing so spaces match.


## Configuration

| Setting | Default | Notes |
|---------|---------|--------|
| `TURING_SEARCH_PROVIDER` | `pgvector` | Search backend (`pgvector` / `null`) |
| `TURING_SEARCH_EMBEDDING_DIMS` | `256` | Fallback dims for unknown models |
| `TURING_SEARCH_PGVECTOR_SQL` | `false` | Optional Postgres `<=>` distance |
| `TURING_EMBEDDING_PROVIDER` | `local` | Embedding backend (`local` / `null`) |
| `TURING_EMBEDDING_MODEL` | `turing-local-v1` | Model name for local provider |

```bash
export TURING_SEARCH_PROVIDER=pgvector
export TURING_EMBEDDING_PROVIDER=local
export TURING_EMBEDDING_MODEL=turing-local-v1
# Optional:
# export TURING_SEARCH_PGVECTOR_SQL=true
```

Fallbacks:

- ``TURING_SEARCH_PROVIDER=null`` — Embedding rows without ranking
- ``TURING_EMBEDDING_PROVIDER=<unknown>`` — null embedder (empty vectors)


## Migration notes

- **0019** — ``Embedding`` table
- **0020** — ``dimensions`` + best-effort ``CREATE EXTENSION vector``
- **0021** — ``provider`` + ``model_name`` columns

Re-index after changing embedding provider or model:

```python
from turing.services.search_index import SearchIndexService
SearchIndexService().index_transcript(transcript)
```


## Embedding model

| Field | Notes |
|-------|--------|
| `organization` | Data boundary (required) |
| `object_type` / `object_id` | Unique per org |
| `content_hash` | SHA-256 of indexed text |
| `vector` | float list (storage unchanged) |
| `dimensions` | Vector size |
| `provider` | EmbeddingProvider code (`local`, `null`, …) |
| `model_name` | Model identifier |
| `metadata` | transcript_id, segment_id, speaker, times, text, external_references |


## Indexing rules

- Same ``content_hash`` **and** same embedding ``provider``/``model_name`` →
  metadata refresh only
- Text or embedder change → re-embed + upsert
- EventBus failures never block STT/analysis


## API

```http
GET /api/turing/v1/search/?q=renewal&external_system=salesforce&external_type=call&limit=20
```

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
      "external_references": [...]
    }
  ],
  "provider": "pgvector"
}
```


## Security

- Org-scoped search + ``view_transcript``
- No cross-tenant vector lookup
- External filters applied inside the resolved organization


## RAG foundation (Phase 4.5.6 / 4.5.7)

Retrieval-augmented answers sit on top of semantic search:

```text
question
   │
   ▼
RAGService.retrieve_context()  →  SemanticSearchProvider.search (org-scoped)
   │
   ▼
RAGService.build_context()     →  transcript/segment/speaker/times/refs/text
   │
   ▼
LLMProvider.generate()         →  openai | null (fallback on failure)
```

| Setting | Default | Notes |
|---------|---------|--------|
| `TURING_LLM_PROVIDER` | `null` | `null` or `openai` |
| `TURING_LLM_MODEL` | `gpt-4o-mini` | Chat model (falls back to `TURING_OPENAI_MODEL`) |
| `TURING_OPENAI_API_KEY` | empty | Required when provider is `openai` |

```bash
export TURING_LLM_PROVIDER=openai
export TURING_LLM_MODEL=gpt-4o-mini
export TURING_OPENAI_API_KEY=sk-...
```

``RAGService`` falls back to ``NullLLMProvider`` if generation fails (missing key,
network, HTTP errors). API keys and prompt/context content are never logged.

```http
POST /api/turing/v1/speech-center/ask/
```

See ``docs/speech-center-api.md`` for the ask contract.


## Other backends

Still pluggable:

- Remote embedding APIs (OpenAI, etc.) via new ``EmbeddingProvider``
- OpenSearch / Pinecone / Weaviate via ``SemanticSearchProvider``
- Additional chat vendors via ``LLMProvider`` (Anthropic, …)

Out of scope: full chat assistant UX, sentiment, frontend UI.
