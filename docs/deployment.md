# Deployment guide (Phase 2.3)

This document covers hardening the **Turing demo project** (`config`) for production.
When Turing is installed as a package inside a host Django app, apply the same
principles to the **host** settings module.

## Settings modules

| Module | Use |
|--------|-----|
| `config.settings` / `config.settings.local` | Local development (default) |
| `config.settings.production` | Production |

```bash
# Local (unchanged)
export DJANGO_SETTINGS_MODULE=config.settings
python manage.py runserver

# Production
export DJANGO_SETTINGS_MODULE=config.settings.production
```

## Required production environment variables

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Strong secret (not the dev placeholder) |
| `DJANGO_DEBUG` | Must be `false` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated public hostnames |
| `DATABASE_URL` | `postgres://USER:PASS@HOST:5432/DBNAME` |
| `CELERY_BROKER_URL` | Redis URL for Celery |
| `CELERY_RESULT_BACKEND` | Redis URL for results |

Recommended:

| Variable | Example |
|----------|---------|
| `CSRF_TRUSTED_ORIGINS` | `https://turing.example.com` |
| `TURING_SPEECHMATICS_API_KEY` | Speechmatics key (or configure in Admin) |
| `TURING_BEHIND_PROXY` | `true` (default) when TLS terminates at a load balancer |
| `SECURE_SSL_REDIRECT` | `true` (default in production) |
| `LOG_LEVEL` | `INFO` |

## Provider secrets (Phase 2.4)

Speechmatics API keys configured in Admin are **encrypted at rest** (Fernet, key
derived from `DJANGO_SECRET_KEY`).

| Priority | Source |
|----------|--------|
| 1 | Database `SpeechProviderConfig.api_key` (decrypted in-process) |
| 2 | `TURING_SPEECHMATICS_API_KEY` environment variable |
| 3 | Missing → clear configuration error when submitting jobs |

### Admin UX

- List/detail show a masked value only (e.g. `********abcd`)
- Password field is always empty on edit — enter a new key to replace, or leave blank to keep
- Plaintext keys are never rendered after save

### Local development

Either:

1. Set the key in **Admin → Speech provider configs** (recommended), or  
2. Export `TURING_SPEECHMATICS_API_KEY=...` in `.env`

Existing plaintext rows are migrated/encrypted automatically on upgrade (`0003_encrypt_provider_api_keys`).

### Production recommendations

- Use a strong, stable `DJANGO_SECRET_KEY` (rotating it invalidates encrypted DB secrets — re-enter keys after rotation)
- Prefer Admin-stored encrypted secrets or a secret manager injected as env
- Restrict Admin access to trusted operators
- Never commit API keys to git

## Object storage (Phase 2.9)

Local development keeps files under `MEDIA_ROOT` (`TURING_STORAGE_BACKEND=local`).

Production should use a private S3-compatible bucket:

```bash
pip install "django-turing[s3]"   # boto3

export TURING_STORAGE_BACKEND=s3
export TURING_S3_BUCKET=turing-media
export TURING_S3_REGION=eu-west-1
export TURING_S3_ACCESS_KEY=...          # or AWS_ACCESS_KEY_ID
export TURING_S3_SECRET_KEY=...          # or AWS_SECRET_ACCESS_KEY
export TURING_SIGNED_URL_TTL_SECONDS=3600
# Optional MinIO / custom endpoint:
# export TURING_S3_ENDPOINT_URL=https://minio.example.com
```

Objects are stored **private** (`default_acl=None`) with **querystring signed URLs**.
Transcription jobs prefer those signed URLs so workers do not load entire audio
files into memory for remote backends.

See [media-storage.md](media-storage.md) for the storage architecture.

## Pre-flight checks

```bash
export DJANGO_SETTINGS_MODULE=config.settings.production
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export DJANGO_DEBUG=false
export DJANGO_ALLOWED_HOSTS=turing.example.com
export DATABASE_URL=postgres://turing:turing@localhost:5432/turing
export CELERY_BROKER_URL=redis://localhost:6379/0
export CELERY_RESULT_BACKEND=redis://localhost:6379/1

python manage.py check --deploy
python manage.py migrate
```

Install a PostgreSQL driver in the deployment environment, for example:

```bash
pip install "psycopg[binary]>=3.1"
```

## Process layout

1. **Web** — gunicorn/uvicorn behind HTTPS reverse proxy  
2. **Worker** — `celery -A config worker -l info -Q turing.default,turing.high,turing.export`  
3. **Redis** — broker/result backend  
4. **Postgres** — primary database  

## Local development (unchanged)

```bash
pip install -e ".[dev]"
cp .env.example .env   # optional
python manage.py migrate
python manage.py runserver
```

`DJANGO_SECRET_KEY` may be omitted locally; an insecure development default is used.
Never use that default in production.
