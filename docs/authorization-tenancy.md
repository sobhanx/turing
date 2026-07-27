# Authorization & Tenancy (Phase 2.7 / 2.9.1 / 2.9.2)

Turing scopes speech data by **Organization**. Users access data through
**memberships** (user ↔ organization ↔ role).

Membership is the **single source of truth** for Turing capabilities.
`is_superuser` is the **only** global bypass. Django `is_staff` grants Admin
login only — it does **not** invent roles or cross-org access.

## Models

| Model | Role |
|-------|------|
| `Organization` | Tenant / ownership boundary (`name`, `slug`, `external_key`) |
| `TuringMembership` | `(user, organization, role)` — unique per user+org |
| `MediaAsset.organization` | Owning org (**required**, PROTECT) |
| `ProcessingJob.organization` | Copied from media (**required**) |
| `Transcript.organization` | Copied from job/media (**required**) |

A seeded **Default** organization (`slug=default`) keeps local/demo workflows working
when no org is specified (CLI / unauthenticated service paths).

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

## Policy (Phase 2.9.1+)

| Principal | Turing capabilities | Cross-org visibility |
|-----------|---------------------|----------------------|
| No membership | None | Empty querysets |
| Staff, no membership | None (Admin UI login only) | Empty |
| Member | Role in that org only | Membership orgs |
| Superuser | Full (ADMIN) | Unscoped |

`user_has_capability(user, capability, organization=…)` always evaluates the
membership **inside that organization**. Unscoped checks are true only if
*any* membership grants the capability — never via a silent staff elevation.

## API scoping

Users only see media, jobs, and transcripts in organizations they belong to
(unless superuser). Job creation rejects foreign `media_id` with **404**.

**Write-path isolation:** `organization_id` / `tenant_key` / org slug on create is
validated against the caller’s active membership. Cross-org creates return **403**
(`permission_denied`). Explicit targets never fall back to Default.
Users with no membership cannot create tenant resources via the API.

Service mutations that receive an actor (`created_by` / `edited_by` / `approved_by` /
`assigned_by` / `decided_by`) assert org-scoped capability in the service layer.

## Admin

- Organizations and memberships require `manage_roles`.
- Platform / provider config require `manage_config`.
- Media / Jobs / Transcripts lists are scoped to membership orgs for non-superusers.
- Admin add / change / delete / view and custom actions require matching Turing
  capabilities in the resource’s organization (status → approved needs
  `approve_transcript`).
- Superuser remains unrestricted.
- Uploads without an organization are assigned the Default org (then gated).

## Local workflow

```bash
python manage.py migrate
python manage.py createsuperuser   # seeded as Admin on Default org
python manage.py runserver
```

Service creates still work without an explicit org when **no user** is passed —
they resolve to Default:

```python
MediaService().create_from_upload(...)  # organization=Default (system path)
```

Authenticated callers without membership are denied.

## Host integration

1. Create an `Organization` (or map host account → `slug` / `external_key`).
2. Create `TuringMembership` rows for users (required for API creates and Admin
   business actions).
3. Pass `organization_id` on upload, or set `tenant_key` to the org slug —
   only for orgs the user belongs to.
4. Do **not** rely on `is_staff` for Turing access — grant membership instead.
