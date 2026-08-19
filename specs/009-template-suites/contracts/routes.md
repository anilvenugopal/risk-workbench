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
| `GET /templates` | all | Administration page: suite list (name, item count, author, unresolved badge) + template list (filterable) + export control (all analysts, links `GET /templates/export.xlsx`). Create/edit/delete/import controls rendered only for `is_admin`. |
| `GET /templates/analysis-templates/new` | admin | Template builder form. Pick lists from live cache, substring-filtered (FR-006 — never live RM queries) — model-profile options carry their DLM/HD/Accumulation marker (FR-004); scheme list filtered by chosen profile's peril/region (fragment below); currency required, currency scheme optional (blank = "Default", P-10) — choosing a scheme loads its vintage options (fragment below) with the latest by effective date pre-selected, and a vintage-less scheme blocks save naming the scheme; analysis settings pre-filled with R8 defaults. |
| `POST /templates/analysis-templates` | admin | Create; on validation error re-render form with errors (form-banner pattern); on success redirect to `/templates`. |
| `GET /templates/analysis-templates/{id}` | all | Template detail; edit form when admin; unresolved references flagged inline (R9); a NULL currency-scheme/vintage pair renders as "Default" (P-10). |
| `POST /templates/analysis-templates/{id}` | admin | Update (same validation as create). |
| `POST /templates/analysis-templates/{id}/delete` | admin | Soft delete; blocked with referencing suite names when live suites use it (FR-010). |
| `GET /templates/analysis-templates/scheme-options` | all | HTMX fragment: `<option>` list of live schemes matching `?profile=<name>`'s peril/region, pre-selected when exactly one (T-03). Triggered on profile change. |
| `GET /templates/analysis-templates/vintage-options` | all | HTMX fragment: `<option>` list of cached vintages for `?scheme=<code>` (vintage code + effective date), latest by effective date pre-selected (T-09/P-10); empty scheme param → empty list (pair blank = "Default"). Triggered on currency-scheme change. Gated on the T-07 rework like the scheme/vintage pick lists. |
| `GET /templates/suites/new` | admin | Suite form: name + template picker (unordered membership, P-08). |
| `POST /templates/suites` | admin | Create suite with items. |
| `GET /templates/suites/{id}` | all | Suite detail: items (sorted by template name for display), empty-state marker for zero items. |
| `POST /templates/suites/{id}` | admin | Update name/items (items rewritten). |
| `POST /templates/suites/{id}/delete` | admin | Soft delete. |
| `GET /templates/metadata` | all | Metadata page: four tabs (`?tab=model-profiles` default, `output-profiles`, `event-rate-schemes`, `currency-schemes` — P-07; the tab lists schemes with their vintages; the built `currencies` tab is swapped for it when the T-07 reads ship — individual currencies stay cached but untabbed), `.tabs` CSS component, tab links `hx-get` the fragment + `hx-push-url`. Shows the last-synced time and status/failure from the latest `sync_irp_metadata` rwb_job. |
| `GET /templates/metadata/table` | all | HTMX fragment: one tab's read-only table, filter input (`hx-trigger="input delay:300ms"`, edm_library pattern), model profiles show the
DLM/HD/Accumulation marker + raw software version. Shared context builder with the page route so they cannot drift. |
| `POST /templates/metadata/sync` | all | Enqueue `sync_irp_metadata` rwb_job + dispatch; PRG back to `/templates/metadata?sync=queued`. When a sync job is already pending or running, nothing is enqueued and the PRG lands on `?sync=already-running`, rendered as a "sync already in progress" message (FR-002); `ensure_pending_rwb_job` with the sentinel requestor makes the check race-safe. |
| `GET /templates/export.xlsx` | all | Workbook per `contracts/transfer-workbook.md`; no parameters — always everything: all live suites plus every live template, including templates in no suite (FR-016). Plain `Response` + `Content-Disposition` (treaty-export precedent). |
| `POST /templates/import` | admin | Multipart `UploadFile`; validate whole workbook, apply all-or-nothing in one transaction; re-render with the full error list (sheet/row/message) or redirect with created/updated counts. |

## Gateway contract (`app/services/irp_gateway.py`)

Four new frozen dataclasses and `IRPGateway` Protocol methods, implemented via
`client.reference_data` and mirrored in `tests/unit/fakes/fake_irp.py`:

```
list_model_profiles() -> list[ModelProfileEntry]      # irp_id, name, software_version_code,
                                                      # peril_code, model_region_code, peril,
                                                      # region, analysis_type, rms_default
list_output_profiles() -> list[OutputProfileEntry]    # irp_id, name, rms_default
list_event_rate_schemes() -> list[EventRateSchemeEntry]  # irp_id, name, peril_code,
                                                      # model_region_code, model_version_code, is_hd
list_currencies() -> list[CurrencyEntry]              # code, name, country_name, symbol — built
                                                      # during US1 and KEPT (P-07 as amended:
                                                      # submission needs the currency code)
list_currency_schemes() -> list[CurrencySchemeEntry]  # irp_id, name, code — PROVISIONAL until the
                                                      # irp-integration search_currency_schemes
                                                      # read ships in a release (T-07)
list_currency_scheme_vintages()
    -> list[CurrencySchemeVintageEntry]               # irp_id, vintage, currency_scheme_code,
                                                      # effective_date — PROVISIONAL (T-07);
                                                      # submission's asOfDate derives from
                                                      # effective_date
```

A further read, `list_accumulation_profiles() -> list[AccumulationProfileEntry]`, is **deferred**
with the tabled T-02 accumulation read (2026-08-18; tasks *Deferred: accumulation*) — its field
list is pinned by the T-02 spike when that work resumes, and its rows land in `irp_model_profile`
with `is_accumulation=1`. Gateway reads are called only by the `sync_irp_metadata` worker (T-01).
A gateway failure fails the rwb_job with its reason; no cache write happens (FR-002).

The gateway is distinct from the T-06 validation utility: that utility is a pure (no-I/O)
irp-integration import used by `template_service` for classification and save/import validation —
not a gateway method.

## Worker contract

`sync_irp_metadata` Dramatiq actor in `app/workers/metadata_jobs.py` (name-based dispatch — actor
name = `rwb_job_type`), body via `runtime.run_job`: fetch all the sets (four today; currency
schemes + vintages join with T-07), then one WORKBENCH
transaction: snapshot-upsert each set keyed on its natural key (`irp_id`; `code` for currencies),
hard-delete rows the
fetch no longer returned. Returns `JobResult.ok(synced counts)` / `.fail(reason)`. (The deferred
accumulation read becomes a further fetch when T-02 resumes.)
