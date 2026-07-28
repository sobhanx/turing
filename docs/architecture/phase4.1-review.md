# Phase 4.1 Review

**Status:** Completed slices 4.1.1–4.1.3 reviewed; **review fixes applied**  
**Scope reviewed:** ExternalReference · Public Analysis API · Event foundation  
**Follow-up:** Phase 4.1 review fixes (job.completed emit + ExternalReference service/API)

---

## Executive verdict

Phase 4.1 delivers a **credible integration foundation** inside the existing monolith.

| Slice | Verdict |
|-------|---------|
| 4.1.1 ExternalReference | Solid data model |
| 4.1.2 Analysis API | Consistent with DRF patterns; **read path ready** |
| 4.1.3 Event foundation | Post-commit design correct |
| **Review fixes** | **`job.completed` emit gap closed**; **ExternalReference is host-usable** via service + REST |

---

## Review fixes completed

### job.completed emission — FIXED

- All `ProcessingJob → SUCCEEDED` paths now go through `JobOrchestrator.mark_succeeded()`.
- `mark_succeeded(job, attempt=None)` supports succeed-without-attempt edge cases.
- Emit still uses `transaction.on_commit()` and fires **once** on transition into `SUCCEEDED`.
- Regression tests cover no-attempt succeed and idempotent re-call.

### ExternalReference host usability — FIXED

- `ExternalReferenceService` validates org↔target ownership in the service layer (not `clean()` alone).
- REST:
  - `POST/GET /v1/media/{id}/external-references/`
  - `POST/GET /v1/transcripts/{id}/external-references/`
  - `DELETE /v1/external-references/{id}/`
- Read-only `external_references` embedded on media/transcript serializers.
- Lookup filters: `GET /v1/media/?external_system=&external_type=&external_id=` (same for transcripts).

---

## 1. ExternalReference implementation quality

### Strengths

- Dedicated model matches Decision 1 (not metadata-primary).
- Explicit `media` / `transcript` FKs — no `GenericForeignKey`.
- Check constraint enforces exactly one target.
- Conditional unique constraints allow one host object → many Turing targets and one Turing object → many host links.
- Service + API + filters make host linking operational.

### Remaining weaknesses

| Issue | Severity |
|-------|----------|
| No Admin registration for ExternalReference | Low–Medium |
| `media.created` still often has empty `external_references` if links are added after create | Medium (event timing) |
| No accept-refs-on-media-create in a single POST | Convenience gap for 4.2 |
| Case/whitespace normalization is trim-only | Low |

---

## 2. Analysis API consistency

Unchanged by review fixes. Still strong read path; still missing latest-per-type helper and generate endpoint (deferred).

---

## 3. Event foundation correctness

Emit gap closed. Remaining notes:

| Issue | Severity |
|-------|----------|
| Legacy helpers (`processing_job.succeeded`, etc.) still exported and unused | Low |
| In-process bus only — no outbox / outbound webhooks | Expected until later Phase 4 |
| Synchronous handlers can add latency under load | Medium at scale |

---

## 4. Missing integration points (still open for Phase 4.2+)

1. Accept `external_references` on `POST /v1/media/` in one round-trip.
2. Combined host-key → transcript + latest analyses convenience endpoint.
3. Outbox / outbound webhook delivery for separate host processes.
4. Opt-in/out of auto AI suite.
5. Regenerate analyses after edits.
6. Admin for ExternalReference.
7. Analysis “latest per type” helper.

---

## 5–7. Naming / security / tests

- Naming overload of `external_*` remains (document vocabulary; don’t rename in 4.2 unless needed).
- Analysis API org scoping remains good; ExternalReference writes now validated in service + API permissions.
- New coverage: service attach/detach/lookup/cross-org, API CRUD/filter/isolation, job.completed without attempt.

---

## 8. Recommendations before Phase 4.2

**Done before 4.2 (this fix pass):**
1. ~~Emit `job.completed` from every succeed path~~
2. ~~ExternalReference service + write validation~~
3. ~~ExternalReference REST + host-key filters~~

**Should include early in 4.2:**
4. Optional refs on media create
5. Host lookup convenience (deal → transcript + latest summary)
6. Analysis latest-per-type helper
7. Minimal event outbox if hosts are out-of-process

**Still defer (Decision 6):** connectors, outbound webhook product UI, sentiment/search/UI, app split.

---

## Summary scorecard

| Area | Score | Note |
|------|-------|------|
| ExternalReference model | Strong | |
| ExternalReference API/service | Strong | Host-usable after fixes |
| Analysis API | Strong | Needs latest semantics later |
| Event foundation | Strong | Emit gap fixed |
| Host-ready integration | Good | Outbox/connectors still later |
| Decision compliance | High | Scope control respected |

**Bottom line:** Phase 4.1 review fixes close the critical gaps identified in the original review. Safe to start Phase 4.2 on ExternalReference DX (create-with-refs, host lookup) and analysis latest helpers — still without connectors.
