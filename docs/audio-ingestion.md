# Audio ingestion (Phase 3.3)

Universal media ingestion runs **before** STT submit. Original uploads are never
overwritten; normalized audio is stored as `MediaProcessingArtifact` rows.

## Flow

```text
create ProcessingJob
    ↓
prepare_media_for_transcription (Celery)
    ↓
ffprobe inspect (real codec/container/duration)
    ↓
normalize via ffmpeg when needed
    ↓
submit_transcription_job (unchanged STT pipeline)
```

## Canonical STT input

| Property | Target |
|----------|--------|
| Container | WAV |
| Codec | PCM signed 16-bit (`pcm_s16le`) |
| Sample rate | 16 kHz |
| Channels | mono |

When input already matches, normalization is skipped and a `skipped` artifact record
is stored for audit.

## Failure strategy

| Condition | Behavior |
|-----------|----------|
| ffprobe missing | Job fails (`INGEST_PROBE_FAILED`) — no STT submit |
| ffprobe unreadable/corrupt | Job fails (`INGEST_UNREADABLE`) — no STT submit |
| ffmpeg missing | Job fails (`INGEST_NORMALIZE_FAILED`) — no STT submit |
| Normalization failure | Job fails (`INGEST_NORMALIZE_FAILED`) — no STT submit |
| Duration > `max_duration_ms` | Hard fail (`VALIDATION_ERROR`) |
| URL media / normalization disabled | Ingestion skipped; original media submitted |

`ProcessingJob.ingest_status` and `ingest_error` record the ingestion outcome.

## Configuration

| Setting | Default |
|---------|---------|
| `TURING_NORMALIZATION_ENABLED` | `true` |
| `TURING_MAX_DURATION_MS` | `0` (no limit) |
| `TURING_POLL_TIMEOUT_MULTIPLIER` | `2.0` |

Platform configuration mirrors these fields in Django Admin.

Poll timeout uses:

```text
max(poll_timeout_seconds, expected_duration_seconds * multiplier)
```

## Worker requirements

Production workers must install **ffmpeg** (includes `ffprobe`). Without ffmpeg,
ingestion fails closed and jobs do not reach STT submit.

Optional overrides: `FFMPEG_PATH`, `FFPROBE_PATH`.

## Boundaries

- STT providers receive ready bytes/URLs only — no ffmpeg in `STTProvider`.
- `Transcript` remains source of truth; ingestion does not touch transcript data.
- URL-sourced media skips normalization in Phase 3.3 (provider fetches URL directly).
