# Contract — HTTP Routes (Iteration 2)

Server-rendered FastAPI + Jinja2 + HTMX (Article 8). All routes require an authenticated session (existing `SessionMiddleware`). Every **state-changing** route requires a valid CSRF token (Article 13). **No route applies row-level access** — every authenticated analyst may load and act on every EDM/RDM/package/job (Article 6 / FR-037/FR-041). **No route calls Risk Modeler polling/result methods** (Article 11); Save-and-Sync / Delete / import only *enqueue* work and return.

Response convention: full-page GETs return the shell-embedded page (`hx-boost` handles nav); HTMX POSTs return the affected **partial** (card, member row, job row, warning fragment, browse listing). JS-off falls back to full-page redirects. The Jobs list additionally exposes an **SSE** stream for live status (research R9).

---

## EDM / RDM import + libraries

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/edms` | EDM library — every EDM, any analyst | Nav `irp.edm_library`. No scoping (FR-037/SC-009). Import entry point (FR-038). |
| GET | `/rdms` | RDM library — every RDM | Nav `irp.rdm_library`. |
| GET | `/edms/{id}` / `/rdms/{id}` | Entity detail + import job status | Real URL (Article 8); visible to any analyst. |
| GET | `/edms/import` / `/rdms/import` | Import form (browse/name) | Browse seeded from a submission's `directory_path` when arriving from one. |
| POST | `/edms/import` | Create EDM + enqueue import | Returns detail/row partial (or dup-name warning). **No Risk Modeler call** (FR-042). |
| POST | `/rdms/import` | Create RDM (applied / review-only) + enqueue | Body carries `applied_edm_ids` (empty → review-only, FR-002/FR-016). |
| POST | `/edms/{id}/retry` / `/rdms/{id}/retry` | Re-enqueue a failed import (FR-045) | Idempotent; single head row. |
| POST | `/edms/{id}/replace-file` / `/rdms/{id}/replace-file` | Replace source file + retry (FR-046) | Re-browse → new path (validated) → re-import; carries `updated_at` (FR-039). |
| GET | `/edms/name-check?name=` / `/rdms/name-check?name=` | HTMX name-collision check | Returns the **non-blocking** `name_collision` fragment (FR-012/SC-005); never blocks. |

---

## Shared-drive browse (read-only)

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/browse?path=` | Live directory listing under `SHARED_DRIVE_ROOT` | HTMX fragment; multi-select. Traversal outside root → 422 (`InvalidSourceFile`). No cached inventory (FR-009/R11). Read-only — never mutates the drive (FR-008). |

---

## Packages (modal + sync/delete on the submission detail)

| Method | Path | Purpose | Success | Errors |
|---|---|---|---|---|
| GET | `/submissions/{id}/packages/new` | Package modal (browse + multi-select + per-member name) | modal partial | gate 409 if submission not ACTIVE |
| POST | `/submissions/{id}/packages` | **Create** — persist package + member names, run collision check, attach to submission. Body carries `action`: `save` (default) submits nothing (FR-014); `save` **and** `sync` in one step (FR-013) — `action=sync` also enqueues member work via the same non-blocking path as `/packages/{pid}/sync` (FR-015/FR-042). | package-card partial (queued state when `action=sync`) | `EmptyPackageError`→422; `InvalidSourceFile`→422; collision warning inline (non-blocking) |
| POST | `/packages/{pid}` | Edit a saved package (names/members) | card partial | `ConcurrencyConflict`→409 (FR-039); `EmptyPackageError`→422 |
| POST | `/packages/{pid}/sync` | **Save and Sync** — enqueue member work, return immediately (FR-015/FR-042) | card partial (queued state) | gate 409; `EmptyPackageError`→422 |
| POST | `/packages/{pid}/delete` | **Delete** — enqueue reverse-order removals (FR-019) | card partial (deleting state) | gate 409 |
| POST | `/packages/{pid}/members/{mid}/retry` | Per-member retry (FR-045) | member-row partial | gate 409 |

**Save-and-Sync / Delete are non-blocking (FR-042 / SC-014):** the POST records `rwb_job` head rows and returns the card in a "queued/syncing/deleting" state. The card then advances live as the poller/workers progress (SSE, below). **No Risk Modeler submit happens in any of these handlers.**

**Name-collision (FR-012):** each member name field runs `/…/name-check`; a hit highlights the field with a non-blocking warning and an override — Save/Sync are never blocked (SC-005).

**Read-only gate (FR-025 / SC-011):** package create/sync/delete/retry routes reject with `SubmissionClosed` (409) when the owning submission is COMPLETED/CANCELLED; the detail view hides the affordances. Only viewing is permitted until reopened (inherits the Iteration-1 gate).

---

## Submission detail — package cards (edit)

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/submissions/{id}` | Detail — now renders **one full-width card per package** (FR-022) | EDIT of the Iteration-1 route: real cards replace the read-only placeholder. |

Each card shows (FR-023): upload progress; the member **EDM status chip** and **RDM status chip** (no rolled-up package status, FR-018); the source file path(s); portfolio-summary + analysis counts rendered **empty** (R13); and job counts **all / active / failed** scoped to the package's members, each linking to the Jobs list pre-filtered to that package (FR-024).

---

## Jobs list + filtering + live status

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/workflows/irp-jobs` | IRP Jobs list (async `irp_job`s) | Nav `workflows.irp_jobs` (made real). Filters from the query string. |
| GET | `/workflows/rwb-jobs` | RWB Jobs list (app-side `rwb_job`s) | Nav `workflows.rwb_jobs`. |
| GET | `/workflows/*-jobs/stream` | **SSE** live-status stream (scoped to the same filter) | Pushes server-rendered `job_row` fragments as the poller advances jobs (FR-036/SC-001/R9). |

**Filter vocabulary (FR-032/FR-033):** `submission`, `package`, `status`, `job_type` — read from the URL query string on **every** request (full page load or partial swap), using the **same code path** for both. Each list accepts the subset it understands and ignores the rest (shared vocabulary). Active filters render as **clearable chips** (FR-034). A package card's job-count link deep-links here with the filter in the query string, so refresh / bookmark / back-forward preserve it (FR-035 / SC-008).

---

## Cross-cutting

- **CSRF** on every POST (Article 13); token from `app/auth/csrf.py`.
- **Nav manifest** (Article 1): add `irp.edm_library` + `irp.rdm_library` under the `irp` rail root; the `workflows.irp_jobs`/`workflows.rwb_jobs` nodes already exist (research R12). Breadcrumb/active-state derive from position.
- **HTMX idle-timeout**: inherits the Iteration-0 `HX-Redirect` on session expiry.
- **No IRP polling/result calls** on any route (Article 11) — imports/sync/delete only enqueue; the poller and workers own every `get_*` / synchronous-delete / notify call (see [worker-poller.md](worker-poller.md)).
- **Roles gate functions, not rows** (Article 6): the import/package/jobs routes are available to any authenticated analyst; any admin-only maintenance (none added this iteration) would be role-gated server-side.
