# Turing

🇮🇷 Persian documentation: [README.fa.md](README.fa.md)

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Django](https://img.shields.io/badge/django-4.2%2B-092E20.svg)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Status](https://img.shields.io/badge/status-beta-orange.svg)](https://pypi.org/project/django-turing/)

**Turing** is a reusable Django package for speech intelligence — upload audio, transcribe speech, review structured transcripts, and derive AI insights. One engine powers meetings, CRM calls, interviews, and voice files across host products without forking the platform per customer.

Named after Alan Turing, the project reflects a goal of building systems that process and understand human communication at scale.

## Features

- **Media ingestion** — Upload audio or register external URLs; S3-compatible object storage and signed URLs
- **Speech-to-text** — Batch transcription via Speechmatics with speaker diarization
- **Async processing** — Celery pipeline with idempotent submit, poll, and persist
- **Structured transcripts** — Segments, speakers, word-level timing, confidence, and revision history
- **Review workflow** — Human editing with audit trail and approval states
- **AI analyses** — Summary, topics, and action items derived without mutating source text
- **Multi-tenancy** — Organizations, memberships, and API queryset scoping
- **REST API** — Media, jobs, transcripts, analyses, search, connectors, and webhooks
- **Speech Center** — Demo UI and host-facing API for transcript lookup and intelligence
- **Connectors** — OAuth integrations (Zoom, Teams, Google Meet, Salesforce, Twilio, and more)
- **Semantic search & RAG** — Segment embeddings and question-answering over transcripts

## Architecture

```text
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│   Upload /  │     │    Audio     │     │     STT     │     │  Transcript  │
│  Connector  │ ──► │  preparation │ ──► │  (batch)    │ ──► │  + speakers  │
└─────────────┘     └──────────────┘     └─────────────┘     └──────┬───────┘
                                                                    │
                    ┌──────────────┐     ┌──────────────┐           ▼
                    │   Export /   │ ◄── │  AI analysis │ ◄── Review & edit
                    │   webhooks   │     │  + search    │
                    └──────────────┘     └──────────────┘
```

For design decisions, module map, and limitations, see [docs/architecture.md](docs/architecture.md).

## Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | Django 4.2+ |
| API | Django REST Framework |
| Background jobs | Celery + Redis |
| STT provider | Speechmatics Batch API |
| Database | SQLite (local) / PostgreSQL (production) |
| Storage | Local filesystem or S3-compatible backends |

## Installation

**Requirements:** Python 3.11+, Redis (for Celery in production).

```bash
git clone <repository-url>
cd turing
pip install -e ".[dev]"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- **Admin:** http://127.0.0.1:8000/admin/
- **API base:** http://127.0.0.1:8000/api/turing/v1/
- **Speech Center UI:** http://127.0.0.1:8000/speech-center/

Default settings: `config.settings` (local development).

### Install as a package in another Django project

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

Run `turing` migrations in the host project.

## Quick Start

1. Open **Admin → Speech provider configs** and set your Speechmatics API key.
2. Upload audio under **Media assets** (or use the Speech Center upload page).
3. Create a **Processing job** with a language code (e.g. `fa`, `en`).
4. Start a Celery worker (production path):

   ```bash
   celery -A config worker -l info -Q turing.default,turing.high,turing.export
   ```

5. When the job completes, open the **Transcript** — segments, speakers, and optional AI analyses are available via Admin and the REST API.

With `Platform configuration → auto_enqueue` enabled (default), jobs are scheduled automatically after creation.

## Configuration

| Area | Where to configure |
|------|-------------------|
| Provider credentials | Admin → Speech provider configs (encrypted at rest) |
| Defaults (language, upload limits) | Admin → Platform configuration |
| Production secrets & TLS | Environment variables — see [docs/deployment.md](docs/deployment.md) |
| Object storage | `django-storages` / S3 settings — see [docs/media-storage.md](docs/media-storage.md) |

Optional environment fallback:

```bash
export TURING_SPEECHMATICS_API_KEY=your-key
```

Priority: **database secret → environment variable → configuration error**.

Supported audio formats by default: `mp3`, `wav`, `m4a`, `webm`, `ogg`.

## REST API

Authenticated endpoints under `/api/turing/v1/` (session or token auth):

| Resource | Path |
|----------|------|
| Media | `/media/` |
| Jobs | `/jobs/` (`retry`, `cancel`, `logs`) |
| Transcripts | `/transcripts/` (`revisions`, `submit_review`) |
| Segments & speakers | `/segments/`, `/speakers/` |
| Analyses | `/analyses/` |
| Speech Center | `/speech-center/` (lookup, timeline, intelligence, ask) |
| Search | `/search/` |
| Connectors | `/connectors/` |
| Webhooks | `/webhooks/` |
| Providers | `/providers/` |

Provider callbacks: `POST /api/turing/v1/webhooks/speechmatics/`

## Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System design and module map |
| [docs/deployment.md](docs/deployment.md) | Production settings and operations |
| [docs/async-pipeline.md](docs/async-pipeline.md) | Celery tasks, retries, idempotency |
| [docs/media-storage.md](docs/media-storage.md) | Uploads, storage backends, signed URLs |
| [docs/webhooks.md](docs/webhooks.md) | Inbound provider and outbound delivery |
| [docs/authorization-tenancy.md](docs/authorization-tenancy.md) | Organizations, roles, API scoping |

Additional guides: [transcript intelligence](docs/transcript-intelligence.md), [Speech Center API](docs/speech-center-api.md), [semantic search](docs/search.md), [connectors](docs/connectors.md), [audio ingestion](docs/audio-ingestion.md), [events](docs/events.md).

## Roadmap

- Real-time streaming transcription
- Additional STT and embedding providers
- Deeper CRM and meeting product integrations
- Marketplace UI for connector installation
- Billing and entitlement layer

## License

MIT — see [package metadata](pyproject.toml).
