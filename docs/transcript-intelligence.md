# Transcript intelligence (Phase 2.6)

Transcripts are structured, reviewable intelligence documents — not only plain text.

## Data model

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
