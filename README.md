# Turing


Named after Alan Turing, a pioneer of computer science and artificial intelligence.
Turing represents our goal of building intelligent systems that can process and understand human communication.


Reusable Django **speech intelligence** package.

Turing is the shared speech intelligence core for host products. The same engine supports meeting transcription, CRM call transcription, interviews, and voice file processing without forking the platform per company.

Phase 1 provides a shared transcription engine that host Django projects can install and run: upload audio, transcribe with Speechmatics, store an editable transcript (segments, speakers, revisions), and manage the flow from Django Admin (and a REST API).

## Current status — Phase 1 complete

- Audio upload → Speechmatics batch transcription → transcript persistence
- Timestamped segments and speaker labels
- Human editing with revision history
- Live validation: **Persian (`fa`) speech transcription** succeeded end-to-end
- Designed as an installable app, not a single-company product

Not in Phase 1: real-time streaming, CRM/meeting product integrations, multi-provider STT, AI summarization/analytics, or file export.

## Tech stack

| Layer | Choice |
|-------|--------|
| Framework | Django |
| API | Django REST Framework |
| Async jobs | Celery + Redis (optional; sync command available) |
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

Speechmatics credentials are managed in **Django Admin** (no source-code edits required):

1. **Speech provider configs** → `speechmatics` → set **API key** (and base URL if needed)
2. Optionally adjust **Platform configuration** (default provider, upload limits, auto-enqueue, diarization defaults)

Environment fallback (optional):

```bash
export TURING_SPEECHMATICS_API_KEY=...
```

Admin key overrides the env value when set.

### Main Admin sections

| Section | Purpose |
|---------|---------|
| Speech provider configs | Provider credentials and defaults |
| Platform configuration | Engine-wide processing / API defaults |
| Media assets | Upload audio (or register an external URL) |
| Processing jobs | Job status, retries, logs |
| Transcripts | Segments, speakers, revisions, review |
| Turing memberships | Roles: Admin, Reviewer, Editor, User, Viewer |

## Basic workflow

```text
Upload audio (Admin or API)
    → Create transcription job (language e.g. fa / en)
    → Process job (Celery worker or sync command)
    → Review transcript (segments + speakers)
    → Edit text / rename speakers
    → Revision history recorded
```

### Process a job without Celery

```bash
python manage.py turing_process_job <job-uuid>
```

With Celery (optional):

```bash
celery -A config worker -l info -Q turing.default,turing.high,turing.export
```

**Note:** For Persian audio, set the job `language_code` to `fa`. The Admin bulk “create jobs” action may not set language; create the job via shell/API with `language_code="fa"` when needed.

### Example: create a Persian job (shell)

```python
from turing.models import MediaAsset
from turing.services import JobOrchestrator

media = MediaAsset.objects.get(pk="<media-uuid>")
job = JobOrchestrator().create_transcription_job(
    media=media,
    language_code="fa",
    options={"diarization": True},
    auto_enqueue=False,
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

- More reliable async processing (non-blocking poll / webhooks)
- Stronger production packaging (secrets, Postgres, object storage)
- Export (TXT / DOCX / PDF)
- CRM and meeting product integrations (host apps on top of the same engine)
- Additional STT providers and AI capabilities (summarization, analytics, etc.)

## License

MIT (see package metadata).
