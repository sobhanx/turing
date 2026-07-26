# Media storage (Phase 2.5 + 2.9)

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
  → spool_upload (temp file + SHA-256, size limit)
  → MediaStorageService
      → StorageGateway (port)
          → DjangoStorageGateway (default_storage)
              → local FileSystemStorage (development)
              → S3Boto3Storage via django-storages (production)
```

- Business logic and Speechmatics integration use `MediaService` /
  `MediaStorageService`, not hard-coded filesystem paths.
- `MediaAsset.file` remains for Admin/Django compatibility; `object_key` +
  `storage_backend` identify the object in the active backend.
- Uploads are **streamed** through a disk spool — large files are not held
  entirely in RAM before storage write.
- Object storage backends use **private** objects + **signed URLs**
  (`StorageGateway.signed_url` / django-storages querystring auth).
- Transcription prefers a signed HTTPS URL for S3/Azure/GCS so the STT provider
  can fetch media without the worker loading full bytes.

## Local vs S3

| Mode | Env | Behavior |
|------|-----|----------|
| Local (default) | `TURING_STORAGE_BACKEND=local` | `MEDIA_ROOT` filesystem |
| S3-compatible | `TURING_STORAGE_BACKEND=s3` | Private bucket; signed URLs |

Install S3 extras: `pip install "django-turing[s3]"` (boto3).

See [deployment.md](deployment.md) for production env vars (bucket, keys, MinIO endpoint).

## Metadata

After upload Turing best-effort extracts (from the spool path, not a full RAM copy):

- `duration_ms`
- `sample_rate_hz`
- `channels`
- `audio_format`
- `audio_codec`

Extraction failures are logged; the file remains usable for transcription.
Optional richer tags: `pip install "django-turing[media]"` (mutagen).
