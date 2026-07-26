# Turing


Named after Alan Turing, a pioneer of computer science and artificial intelligence.
Turing represents our goal of building intelligent systems that can process and understand human communication.


Reusable Django **speech intelligence** package.

Turing is the shared speech intelligence core for host products. The same engine supports meeting transcription, CRM call transcription, interviews, and voice file processing without forking the platform per company.

Phase 1 provides a shared transcription engine that host Django projects can install and run: upload audio, transcribe with Speechmatics, store an editable transcript (segments, speakers, revisions), and manage the flow from Django Admin (and a REST API).

**Phase 2.1** adds a production async pipeline: Celery tasks for submit → poll (non-blocking backoff) → fetch/persist, with idempotent retries.

**Phase 2.3** splits Django settings into local vs production modules with env-based secrets and HTTPS cookie hardening. See [docs/deployment.md](docs/deployment.md).

**Phase 2.4** encrypts provider API keys at rest and masks them in Admin (DB secret → env fallback).

**Phase 2.6** upgrades transcripts into structured intelligence (words, confidence, review workflow, search). See [docs/transcript-intelligence.md](docs/transcript-intelligence.md).

**Phase 2.7** adds organization-based ownership, memberships, API queryset scoping, and correct approve/review capabilities. See [docs/authorization-tenancy.md](docs/authorization-tenancy.md).

**Phase 2.8** hardens the async pipeline: submit claiming, provider-aware cancel, persist race safety, and lifecycle transition checks. See [docs/async-pipeline.md](docs/async-pipeline.md).

**Phase 2.9** adds production object storage (S3-compatible), signed URLs, and streaming uploads. See [docs/media-storage.md](docs/media-storage.md) and [docs/deployment.md](docs/deployment.md).

## Current status

- Audio upload → Speechmatics batch transcription → transcript persistence
- Timestamped segments and speaker labels
- Human editing with revision history
- Live validation: **Persian (`fa`) speech transcription** succeeded end-to-end
- Async Celery pipeline (auto-enqueue); sync CLI remains a debug fallback
- Designed as an installable app, not a single-company product

Not included yet: real-time streaming, CRM/meeting product integrations, multi-provider STT, AI summarization/analytics, or file export.

## Tech stack

| Layer | Choice |
|-------|--------|
| Framework | Django |
| API | Django REST Framework |
| Async jobs | Celery + Redis (**required** for production auto-processing) |
| STT provider | Speechmatics Batch API |
| Database | SQLite (local demo) / PostgreSQL (recommended for real deploys) |

## Installation (demo project)

```bash
cd turing
pip install -e ".[dev]"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Default settings module: `config.settings` (local). Local HTTP development still works without a strong `DJANGO_SECRET_KEY`.

For production, use `config.settings.production` and required env vars — see [docs/deployment.md](docs/deployment.md).

Admin: http://127.0.0.1:8000/admin/  
API base: http://127.0.0.1:8000/api/turing/v1/

### Use as a package in another Django project

```python
INSTALLED_APPS = [
    # ...
    "rest_framework",
    "django_filters",
    "turing.apps.TuringConfig",
]

urlpatterns += [
    path("api/turing/", include("turing.api.urls")),
]
```

Run migrations for the `turing` app in the host project.

## Configuration

Speechmatics credentials are managed in **Django Admin** (encrypted at rest; no source-code edits required):

1. **Speech provider configs** → `speechmatics` → set **API key** (shown masked after save)
2. Optionally adjust **Platform configuration** (default provider, default language, upload limits, allowed audio extensions, auto-enqueue)

Supported uploads by default: **mp3, wav, m4a, webm, ogg** (see [docs/media-storage.md](docs/media-storage.md)).

Environment fallback (optional):

```bash
export TURING_SPEECHMATICS_API_KEY=...
```

Priority: **database secret → environment variable → configuration error**.

### Main Admin sections

| Section | Purpose |
|---------|---------|
| Speech provider configs | Provider credentials and defaults |
| Platform configuration | Engine-wide processing / API defaults |
| Media assets | Upload audio (or register an external URL) |
| Processing jobs | Job status, retries, logs |
| Transcripts | Segments, speakers, revisions, review |
| Organizations | Tenant / data-ownership boundary |
| Turing memberships | User ↔ Organization ↔ role (Admin, Reviewer, Editor, …) |

## Basic workflow

```text
Upload audio (Admin or API)
    → Create transcription job (language e.g. fa / en)
    → Auto-enqueue Celery pipeline (if auto_enqueue enabled)
         submit → poll (backoff) → fetch/persist
    → Review transcript (segments + speakers)
    → Edit text / rename speakers
    → Revision history recorded
```

### Celery worker (production path)

```bash
# Redis must be running (CELERY_BROKER_URL)
celery -A config worker -l info -Q turing.default,turing.high,turing.export
```

With `Platform configuration.auto_enqueue=True` (default), job creation schedules processing automatically — no manual `turing_process_job` in normal use.

See [docs/async-pipeline.md](docs/async-pipeline.md) for task names, backoff settings, idempotency, and webhook-ready polling.

### Sync fallback (debug only)

```bash
python manage.py turing_process_job <job-uuid>
```

**Note:** Set **Platform configuration → Default language** to `fa` for Persian (or pass `language_code` when creating a job). Admin bulk “Create transcription jobs” uses that default and will refuse to create jobs if no language is configured.

### Example: create a Persian job (shell)

```python
from turing.models import MediaAsset
from turing.services import JobOrchestrator

media = MediaAsset.objects.get(pk="<media-uuid>")
job = JobOrchestrator().create_transcription_job(
    media=media,
    language_code="fa",
    options={"diarization": True},
    # auto_enqueue=True by default → Celery submit task is scheduled
)
print(job.id)
```

## REST API (Phase 1)

Authenticated endpoints under `/api/turing/v1/`:

| Resource | Path |
|----------|------|
| Media | `POST/GET /media/` |
| Jobs | `POST/GET /jobs/`, `retry/`, `cancel/`, `logs/` |
| Transcripts | `GET /transcripts/`, `.../revisions/`, `.../submit_review/` |
| Segments | `GET/PATCH /segments/{id}/` |
| Speakers | `GET/PATCH /speakers/{id}/` |
| Providers | `GET /providers/` |

Use session auth or DRF Token authentication.

## Roadmap (later phases)

- Provider webhooks (replace/augment poll tasks)
- Export (TXT / DOCX / PDF)
- CRM and meeting product integrations (host apps on top of the same engine)
- Additional STT providers and AI capabilities (summarization, analytics, etc.)

## License

MIT (see package metadata).
