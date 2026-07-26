# Authorization & Tenancy (Phase 2.7)

Turing scopes speech data by **Organization**. Users access data through
**memberships** (user ↔ organization ↔ role).

## Models

| Model | Role |
|-------|------|
| `Organization` | Tenant / ownership boundary (`name`, `slug`, `external_key`) |
| `TuringMembership` | `(user, organization, role)` — unique per user+org |
| `MediaAsset.organization` | Owning org (PROTECT) |
| `ProcessingJob.organization` | Copied from media |
| `Transcript.organization` | Copied from job/media |

A seeded **Default** organization (`slug=default`) keeps local/demo workflows working
when no org is specified.

`tenant_key` remains as an optional host string (often mirrors `organization.slug`).

## Roles & capabilities

Unchanged capability map (`domain/policies.py`):

| Role | Can approve? | Can edit? |
|------|--------------|-----------|
| Admin / Reviewer | Yes | Yes |
| Editor | **No** | Yes |
| User / Viewer | No | No |

API wiring:

- `POST .../transcripts/{id}/submit_review/` → `edit_transcript` (editors may submit)
- `POST .../transcripts/{id}/approve/` → `approve_transcript` (editors **cannot**)
- Segment/speaker edits → `edit_transcript`

Editors receive **403** on approve.

## API scoping

Non-staff users only see media, jobs, and transcripts in organizations they belong to.
Job creation rejects foreign `media_id` with **404**.

**Write-path isolation:** `organization_id` / `tenant_key` / org slug on create is
validated against the caller’s active membership. Cross-org creates return **403**
(`permission_denied`). Explicit targets never fall back to Default.
Users with no membership cannot create tenant resources via the API.

Capabilities are evaluated **per organization** (never via a global max-role for
org-scoped actions).

Staff and superusers remain **unscoped** (ops / Admin convenience).

## Admin

- Organizations and memberships are editable in Django Admin.
- Media/Jobs/Transcripts list filters include organization.
- Admin stays unscoped for staff (sees all orgs).
- Uploads without an organization are assigned the Default org.

## Local workflow

```bash
python manage.py migrate
python manage.py createsuperuser   # seeded as Admin on Default org
python manage.py runserver
```

Service creates still work without an explicit org — they resolve to Default:

```python
MediaService().create_from_upload(...)  # organization=Default
```

## Host integration

1. Create an `Organization` (or map host account → `slug` / `external_key`).
2. Create `TuringMembership` rows for users (required for API creates).
3. Pass `organization_id` on upload, or set `tenant_key` to the org slug —
   only for orgs the user belongs to.
