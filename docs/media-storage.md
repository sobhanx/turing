# Media storage (Phase 2.5)

## Supported upload formats

Default allow-list (configurable):

| Extension | Typical MIME |
|-----------|----------------|
| `mp3` | `audio/mpeg` |
| `wav` | `audio/wav` |
| `m4a` | `audio/mp4` |
| `webm` | `audio/webm` |
| `ogg` | `audio/ogg` |

Configure via **Platform configuration** or environment:

- `max_upload_bytes` / `TURING_MAX_UPLOAD_BYTES`
- `allowed_audio_extensions` / `TURING_ALLOWED_AUDIO_EXTENSIONS`
- `allowed_audio_mime_types` / `TURING_ALLOWED_AUDIO_MIME_TYPES` (optional; blank = built-in defaults)

## Storage design

```text
MediaService
  → MediaStorageService
      → StorageGateway (port)
          → DjangoStorageGateway (default_storage)
              → local filesystem today
              → S3 / Azure / GCS later via django-storages
```

- Business logic and Speechmatics integration read bytes through `MediaService` /
  `MediaStorageService`, not hard-coded filesystem paths.
- `MediaAsset.file` remains for Admin/Django compatibility; `object_key` +
  `storage_backend` identify the object in the active backend.

## Metadata

After upload Turing best-effort extracts:

- `duration_ms`
- `sample_rate_hz`
- `channels`
- `audio_format`
- `audio_codec`

Extraction failures are logged; the file remains usable for transcription.
Optional richer tags: `pip install "django-turing[media]"` (mutagen).
