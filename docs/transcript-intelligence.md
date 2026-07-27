# Transcript intelligence

Turing separates **raw transcript content** (source of truth) from **derived AI analyses**
(append-only intelligence linked to a transcript).

## Phase 2.6 — structured transcripts

See the sections below for the STT data model, review workflow, and search. These features
operate on provider-normalized transcript content and human edits — not LLM output.

## Phase 3.2 — AI-derived analyses

### Principles

1. **Raw transcript is authoritative** — `Transcript`, `TranscriptSegment`, and
   `TranscriptRevision` are never modified by AI jobs.
2. **Derived data only** — summaries, action items, and topics are stored in
   `TranscriptAnalysis` rows linked to a transcript.
3. **History preserved** — each analysis run creates a new row; previous results are not
   overwritten.
4. **Provider abstraction** — business logic calls `AIProvider` via `AIProviderRegistry`,
   not a concrete vendor SDK.

### Data model

| Model | Role |
|-------|------|
| `Transcript` | Source of truth (STT + human edits) |
| `TranscriptAnalysis` | Derived AI output (`analysis_type`, `content`, `provider`, `model_name`) |

`TranscriptAnalysis.organization` is copied from the transcript at insert time for
tenant-safe queries and indexing.

### Analysis types

| Type | `content` shape |
|------|-----------------|
| `summary` | `{"summary": "...", "main_points": ["..."]}` |
| `action_items` | `[{"task": "...", "owner": null, "deadline": null}, ...]` |
| `topics` | `["topic-a", "topic-b"]` |

### Architecture

```text
STT completes (fetch_and_persist_transcription)
        |
        v  (only when transcript newly created)
generate_transcript_analysis.delay(transcript_id)
        |
        v
TranscriptAnalysisService.generate_default_suite()
        |
        +--> AIProviderRegistry.get(TURING_AI_PROVIDER)
        |
        v
TranscriptAnalysis rows (SUMMARY, ACTION_ITEMS, TOPICS)
```

The STT pipeline (`submit` → `poll` → `fetch_and_persist`) is unchanged. Analysis is
scheduled from the Celery fetch task when `_fetch_and_persist_with_created` reports
`created=True`.

### Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `TURING_AI_PROVIDER` | `fake` | Registry code (`fake`, `openai`, …) |
| `TURING_OPENAI_API_KEY` | empty | Optional OpenAI provider |
| `TURING_OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |

Local development and tests use the **fake** provider (no network).

### Adding a new AI provider

1. Implement `AIProvider` in `turing/ai/interfaces.py` (`summarize`, `extract_action_items`,
   `extract_topics`).
2. Register with `@AIProviderRegistry.register` in `turing/ai/providers/<name>.py`.
3. Import the module from `turing/apps.py` `ready()` so registration runs at startup.
4. Set `TURING_AI_PROVIDER=<code>`.

### Adding a new analysis type

1. Add a value to `AnalysisType` in `turing/domain/enums.py`.
2. Extend `AIProvider` with a method (or route via `analyze()`).
3. Add validation in `TranscriptAnalysisService._validate_content`.
4. Include the type in `DEFAULT_ANALYSIS_TYPES` if it should run automatically.

### Known behavior

- Re-running analysis creates additional history rows (by design).
- Human transcript edits do not invalidate existing analyses (re-run manually or via a
  future trigger if needed).
- Stale analyses after edits are acceptable for Phase 3.2.

---

## Data model (Phase 2.6)

| Concept | Storage |
|---------|---------|
| Transcript | `status`, `full_text`, `confidence_avg`, `word_count`, `metadata` |
| Segment | timed text, `confidence`, JSON `words`, `provider_payload` |
| Word | `TranscriptWord` rows (+ mirrored JSON on the segment for API compactness) |

Word schema (provider-agnostic):

```json
{"text": "Hello", "start_ms": 0, "end_ms": 400, "confidence": 0.95}
```

Providers map into `NormalizedWord` / `NormalizedSegment`; Speechmatics is only one adapter.

## Review workflow

```text
draft → in_review → approved
         ↘ return to draft
```

Admin actions: submit for review, approve, return to draft.  
API: `POST /api/turing/v1/transcripts/{id}/submit_review/`, `.../approve/`.

## Search

Filter list with `?q=term` (or Admin search on `full_text`).

- PostgreSQL: Django `SearchVector` / `SearchQuery` on `full_text` when available
- SQLite / fallback: case-insensitive match on transcript text, segment text, and words

No Elasticsearch required.

## Compatibility

Existing transcripts without word rows remain valid. `word_count` defaults to `0` and
can be derived from segment text when JSON words are empty.
