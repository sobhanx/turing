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

## Secure defaults (production module)

Enabled when using `config.settings.production`:

- `DEBUG = False`
- `SESSION_COOKIE_SECURE = True`
- `CSRF_COOKIE_SECURE = True`
- `SECURE_SSL_REDIRECT = True`
- `SECURE_HSTS_SECONDS = 31536000` (1 year)
- `SECURE_PROXY_SSL_HEADER` when `TURING_BEHIND_PROXY=true`
- SQLite refused unless `TURING_ALLOW_SQLITE_IN_PRODUCTION=true`

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
