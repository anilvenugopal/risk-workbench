# Route Contract: Templates & Metadata (009)

All routes live in a new `app/routers/templates.py` (registered before `shell.router`; the
existing `/templates` stub handler in `app/routers/shell.py:49` is removed). Literal sub-paths are
declared before parameterized ones (EDM-router precedent). Every state-changing POST validates
CSRF; mutating routes additionally require `is_admin` via the `_require_admin` helper pattern
(`app/routers/admin.py:19`) — viewing, sync, and export are open to every analyst per P-01, except
sync/import/mutations as marked.

## Nav manifest (`app/nav/manifest.py`)

Existing rail root `templates` (route `/templates`, roles `[]`) gains two children:

| key | label | route | roles |
|---|---|---|---|
| `templates.suites` | Template Suites | `/templates` | `[]` |
| `templates.metadata` | Analysis Metadata | `/templates/metadata` | `[]` |

## Pages & fragments

| Method + path | Who | Behavior |
|---|---|---|
| `GET /templates` | all | Administration page: suite list (name, item count, author, unresolved badge) + template list (filterable). Create/edit/delete/import controls rendered only for `is_admin`. |
| `GET /templates/analysis-templates/new` | admin | Template builder form. Pick lists from live cache; scheme list filtered by chosen profile's peril/region (fragment below); analysis settings pre-filled with R8 defaults. |
| `POST /templates/analysis-templates` | admin | Create; on validation error re-render form with errors (form-banner pattern); on success redirect to `/templates`. |
| `GET /templates/analysis-templates/{id}` | all | Template detail; edit form when admin; unresolved references flagged inline (R9). |
| `POST /templates/analysis-templates/{id}` | admin | Update (same validation as create). |
| `POST /templates/analysis-templates/{id}/delete` | admin | Soft delete; blocked with referencing suite names when live suites use it (FR-010). |
| `GET /templates/analysis-templates/scheme-options` | all | HTMX fragment: `<option>` list of live schemes matching `?profile=<name>`'s peril/region, pre-selected when exactly one (T-03). Triggered on profile change. |
| `GET /templates/suites/new` | admin | Suite form: name + ordered template picker. |
| `POST /templates/suites` | admin | Create suite with items (order, per-item portfolio-name override). |
| `GET /templates/suites/{id}` | all | Suite detail: ordered items, empty-state marker for zero items. |
| `POST /templates/suites/{id}` | admin | Update name/items/order (items rewritten, positions renumbered). |
| `POST /templates/suites/{id}/delete` | admin | Soft delete. |
| `GET /templates/metadata` | all | Metadata page: four tabs (`?tab=model-profiles` default, `output-profiles`, `event-rate-schemes`, `currencies`), `.tabs` CSS component, tab links `hx-get` the fragment + `hx-push-url`. Shows the last-synced time and status/failure from the latest `sync_irp_metadata` rwb_job. |
| `GET /templates/metadata/table` | all | HTMX fragment: one tab's read-only table, filter input (`hx-trigger="input delay:300ms"`, edm_library pattern), model profiles show the
DLM/HD/Accumulation marker + raw software version. Shared context builder with the page route so they cannot drift. |
| `POST /templates/metadata/sync` | all | Enqueue `sync_irp_metadata` rwb_job + dispatch; PRG back to `/templates/metadata?sync=queued`. When a sync job is already pending or running, nothing is enqueued and the PRG lands on `?sync=already-running`, rendered as a "sync already in progress" message (FR-002); `ensure_pending_rwb_job` with the sentinel requestor makes the check race-safe. |
| `GET /templates/export.xlsx` | all | Workbook per `contracts/transfer-workbook.md`; `?suite=<id>` repeatable. Absent = all live suites plus every live template, including templates in no suite (FR-016); with `?suite=`, only the selected suites and their referenced templates. Plain `Response` + `Content-Disposition` (treaty-export precedent). |
| `POST /templates/import` | admin | Multipart `UploadFile`; validate whole workbook, apply all-or-nothing in one transaction; re-render with the full error list (sheet/row/message) or redirect with created/updated counts. |

## Gateway contract (`app/services/irp_gateway.py`)

Five new frozen dataclasses and `IRPGateway` Protocol methods, implemented via
`client.reference_data` and mirrored in `tests/unit/fakes/fake_irp.py`:

```
list_model_profiles() -> list[ModelProfileEntry]      # irp_id, name, software_version_code,
                                                      # peril_code, model_region_code, peril,
                                                      # region, analysis_type, rms_default
list_accumulation_profiles() -> list[AccumulationProfileEntry]  # T-02: backed by the new
                                                      # irp-integration read; field list pinned by
                                                      # the T-02 spike; rows land in
                                                      # irp_model_profile with is_accumulation=1
list_output_profiles() -> list[OutputProfileEntry]    # irp_id, name, rms_default
list_event_rate_schemes() -> list[EventRateSchemeEntry]  # irp_id, name, peril_code,
                                                      # model_region_code, model_version_code, is_hd
list_currencies() -> list[CurrencyEntry]              # code, name, country_name, symbol
```

`list_accumulation_profiles` requires the new irp-integration read (built in
`../../IRP/irp-integration`, consumed via `make irp-local` until published). Called only by the
`sync_irp_metadata` worker (T-01). A gateway failure fails the rwb_job with its reason; no cache
write happens (FR-002).

## Worker contract

`sync_irp_metadata` Dramatiq actor in `app/workers/metadata_jobs.py` (name-based dispatch — actor
name = `rwb_job_type`), body via `runtime.run_job`: fetch all five sets, then one WORKBENCH
transaction: snapshot-upsert each set keyed on `irp_id` (currencies: `code`), hard-delete rows the
fetch no longer returned. Returns `JobResult.ok(synced counts)` / `.fail(reason)`.
