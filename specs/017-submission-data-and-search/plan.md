# Implementation Plan: Submission Data and Cross-Entity Search (Iteration 12)

**Branch**: `017-submission-data-and-search` | **Date**: 2026-09-17 | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes
**Blocked by:** Nothing. T-09 (export-form pre-fill, FR-011) is Deferred until
spec 014 is on `main`; it does not block the rest.

## Design summary

- **Modeling status keeps its tables.** `submission.status_code`,
  `submission_status_kind` and `submission_status_event` stay as they are —
  the constitution's Article 4 names them — and only the labels change to
  "Modeling status". Nothing about reasons, reversibility or the Active gate
  moves (T-01).
- **Submission status is one new column and one new kind table.**
  `submission.deal_status_code` (NOT NULL, default `IN_PROCESS`) references
  `deal_status_kind` (`WON`, `LOST`, `IN_PROCESS`). `POST
  /submissions/{id}/statuses` saves it beside Modeling status in one
  transaction under the one `updated_at` marker — in place, no reason, no event
  row, in every Modeling status (T-01, T-02, P-12, P-14).
- **Dates gain two optional overrides per CRM ID.** `submission.expiration_date`
  (nullable) is added; `submission_crm_id` gains nullable `inception_date` and
  `expiration_date`. A CRM ID's effective date is `COALESCE(override, deal)`
  per column. `POST /submissions/{id}/crm-ids/{tag}/dates` edits one CRM ID;
  `POST /submissions/{id}/crm-ids/same-dates` nulls every override after an
  `hx-confirm`. Both are gated on Modeling status Active like add and remove
  (T-03).
- **One view defines the effective-date rule.** `v_submission_crm_id` emits
  one row per CRM ID (a blank-CRM row for a submission with none) with the
  effective dates, both statuses and the client. It is the FR-013 extract, and
  the in-force predicate reads it: `s.deal_status_code = 'WON' AND EXISTS (…
  WHERE effective_inception_date <= :asof AND effective_expiration_date >=
  :asof)`. The Won test sits on the outer submission row so the covering list
  index drops Lost and In Process deals before the join. A NULL expiration
  never compares true, so P-09 falls out of the SQL (T-04).
- **One predicate builder serves three lists.**
  `submission_service.submission_filter_clauses(filters, alias)` returns the
  ANDed clauses and bound params for owner, cedant, client, treaty type,
  treaty year, CRM IDs, Submission status, inception and in-force, plus
  Modeling status for the submissions list only (P-13). `list_submissions`
  applies them directly; `list_edms` and
  `list_rdms` wrap them in a single `EXISTS` over `submission_edm` /
  `submission_rdm` joined to `submission`, which is exactly FR-016's "one
  linked submission satisfies every filter together". No submission filter set
  → no `EXISTS`, so unlinked EDMs stay listed (T-05).
- **CRM ID becomes multi-value everywhere.** The values are one `IN` list
  compared against `LOWER(TRIM(crm_id))` inside one `EXISTS` over
  `submission_crm_id` (exact match, P-10); the list's text input becomes a
  chip input on the `yearChips` pattern.
- **Client is a stored integer, not a foreign key.** `submission.client_id INT
  NULL`. New `app/services/client_service.py` reads `dbo.Client` over the
  `LOSS` connection through `db.execute`: `list_clients()` for the form and the
  filter menus, `client_names(ids)` for row display. Both fail open — an
  unreachable repository yields `None`/`{}`, the form says the list is
  unavailable, and the submission saves without a client. A posted client is
  checked against the list only while the list is reachable (T-06).
- **Treaty types come from the kind table.** The seed becomes the eleven
  FR-012 codes; the router's `TREATY_TYPES` constant is deleted and the form
  and every treaty-type filter read `submission_service.treaty_type_kinds()`
  (T-07).
- **Filter parsing moves to one module.** `app/routers/_list_filters.py`
  parses text and multi-value params, applies the twenty-value cap
  (`_MAX_FILTER_VALUES` 400 → 20) and the existing one-line messages, and
  builds the `filters` dict; `submissions.py`, `edms.py` and `rdms.py` call it
  (T-08).
- **Library filter bar.** `edm_library.html` / `rdm_library.html` gain the
  submission-attribute filters beside name search and import status; the
  `#lib-live` poll URL carries every filter param, not just `q` and `status`.
  Owner has no default on the libraries. The sync screen is untouched.
- **The submission page above the tables is redesigned, not patched
  (T-10).** Today `submission_detail.html` stacks a title with a status chip,
  a created / links-to / directory line, a `deal-facts` row (Cedant, Treaty
  type, Treaty year, Inception), an Owner band with an inline reassign form,
  a CRM band whose `crm_tags.html` is an add box plus removable chips, a
  read-only banner, and a separate "Status" section with three forms (Mark
  complete, Cancel deal with reason, Reopen) over the history list. This
  iteration adds a second status, a client, an expiration, and dates per CRM
  ID to that stack, so the block from the title to the history list is
  rebuilt as one metadata section and one set of editing controls: Modeling
  status (transition, reason, history) and Submission status (a plain select,
  live in every Modeling status, P-12) shown and edited apart; CRM ID
  creation and removal together with each CRM ID's inception and expiration,
  inherited dates marked, one "make them all the same" control; deal-level
  inception and expiration edited in place. The read-only banner's "Reopen
  it to make changes" wording changes, since Submission status stays
  editable when the deal is Completed or Cancelled. The EDM, RDM, analyses
  and exports tables below the history are untouched.
- **Labels.** "Cedant" unchanged, "Client" as "ID - name". EDM detail treaty
  grid: "Cedent" → "Cedant" (spelling only, P-05).
- **Export pre-fill waits for spec 014.** `client_id` and the view's effective
  inception are the inputs the 014 form will read; the form change itself is
  one gated task (T-09).
- **Docs.** `docs/FUNCTIONAL_REQUIREMENTS.md` (FR-020's three corrections plus
  the status and filter rows), `docs/DATA_MODEL.md` §4 and the seed table,
  `docs/PRD.md` §7.2a.
- **Previews first** for the two screens with new layout: the redesigned
  submission page above the tables (T-10) and the library filter bar. The
  submission page preview covers Active with CRM IDs carrying inherited and
  entered dates, Active with no CRM ID, Completed (read-only except
  Submission status), and the client with the repository unreachable. No
  template or route for that block is written before the preview is approved
  (docs/UI_WORKFLOW.md rule 1). The submissions list's four extra filters
  reuse `multi_picker` and need none.

## Material changes

| Area | Change |
|---|---|
| Database | `rwb_workbench`, edited in `alembic/versions/0001_initial.py` then Rebuild: `deal_status_kind` + seed; `submission.deal_status_code`, `submission.expiration_date`, `submission.client_id`; `submission_crm_id.inception_date`, `.expiration_date`; view `v_submission_crm_id`; `treaty_type_kind` reseeded to eleven codes. `ix_submission_list_order` INCLUDE gains `deal_status_code`, `expiration_date`, `client_id`. `rwb_loss`: read-only read of `dbo.Client`; no DDL from this branch. |
| Worker | None. |
| Service | `submission_service`: `submission_filter_clauses`, `treaty_type_kinds`, `deal_status_kinds`, `set_deal_status`, `set_crm_dates`, `reset_crm_dates`, `CrmTag` gains effective dates + inherited flags, `Submission`/`SubmissionRow` gain `deal_status_*`, `expiration_date`, `client_id`, `client_name`. `edm_service.list_edms` / `rdm_service.list_rdms` accept `submission_filters`. New `client_service`. |
| UI | Submission page: metadata section, status controls and CRM ID section rebuilt as one screen above the unchanged tables (T-10); three new POST routes; submissions list filter bar; library filter bars and poll URL; `_list_filters` module; label changes. |
| Library | None. No irp-integration call. |
| Docs | FR doc, DATA_MODEL §4 + seed table, PRD §7.2a. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Modeling status keeps `submission.status_code` / `submission_status_kind` / `submission_status_event`; Submission status is `submission.deal_status_code` → new `deal_status_kind`. "Submission status" in the UI is `deal_status` in code and schema | Approved | [research.md#R1](research.md#r1--two-statuses-two-columns-one-of-them-new-t-01-t-02) |
| T-02 | Submission status is editable in every Modeling status, including Completed and Cancelled; plain in-place UPDATE with the `updated_at` check; no reason, no event | Approved | [research.md#R1](research.md#r1--two-statuses-two-columns-one-of-them-new-t-01-t-02) |
| T-03 | Deal-level `inception_date` stays NOT NULL; `expiration_date` added nullable; per-CRM overrides nullable per column; effective = `COALESCE` per column; "make them all the same" nulls the overrides; date writes gated on Active | Approved | [research.md#R2](research.md#r2--dates-deal-defaults-with-per-crm-id-overrides-t-03) |
| T-04 | View `v_submission_crm_id` is both the FR-013 extract and the in-force source; the predicate names `WON` through one module constant | Approved | [research.md#R3](research.md#r3--one-view-for-the-extract-and-for-in-force-t-04) |
| T-05 | One clause builder in `submission_service`; the libraries wrap it in a single `EXISTS` over the association table, which is FR-016's semantics by construction | Approved | [research.md#R4](research.md#r4--one-predicate-builder-and-one-exists-per-library-t-05) |
| T-06 | `submission.client_id INT NULL`, no FK; `client_service` reads `dbo.Client` over `LOSS` via `db.execute`, fails open; the posted id is checked against the list only when the list is reachable | Approved | [research.md#R5](research.md#r5--client-a-stored-id-read-over-loss-t-06) |
| T-07 | Eleven snake_case codes reseed `treaty_type_kind`; the six provisional codes are dropped (Rebuild, not migrated); router constant deleted; form and filters read the table | Approved | [research.md#R6](research.md#r6--treaty-types-from-the-kind-table-t-07) |
| T-08 | `_MAX_FILTER_VALUES` goes from 400 to 20 and moves to `app/routers/_list_filters.py` with the shared parser | Approved | [research.md#R7](research.md#r7--the-filter-cap-is-twenty-t-08) |
| T-09 | Export-form pre-fill (FR-011) lands as one task after spec 014 merges; `export_service.list_clients` is then replaced by `client_service.list_clients` | Deferred | [research.md#R8](research.md#r8--export-pre-fill-waits-for-spec-014-t-09) |
| T-10 | The submission page from the title to the status history is redesigned as one metadata section plus one set of controls for Modeling status, Submission status, CRM IDs and dates, built only after a rendered preview is approved; the EDM, RDM, analyses and exports tables are untouched | Approved | [research.md#R10](research.md#r10--the-submission-page-above-the-tables-is-rebuilt-t-10) |

---

## Technical Context

<!-- Only what changed or constrains the design. The stack is documented in
     docs/PRD.md §3 (Technology stack & environment); architecture rules in
     .specify/memory/constitution.md. Do not restate either. -->

**New dependencies**: None.
**Databases touched**: `rwb_workbench` (schema edits above, all reads and
writes); `rwb_loss` (read-only `SELECT` on `dbo.Client` through the `LOSS`
connection; the table is created by spec 014's `bootstrap_loss.py`, not by
this branch). `rwb_exposure` and DATABRIDGE untouched. Unit tests register a
second SQLite engine as `LOSS` with an attached `dbo` schema holding `Client`
(the spec 014 conftest pattern).

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: **no
violations** (re-checked after Phase 1 design — unchanged).

Material interactions — where an article actively shapes this design:

- **Article 4 (event-sourced status where it earns it)**: the article names
  `submission.status_code` and `submission_status_event`; renaming them for a
  label change would be an amendment with no behavior gain, so Modeling
  status keeps them (T-01). Submission status is "other status" in the
  article's terms — a plain value with no reason (P-02) — and is updated in
  place (T-02); constitution v4.1.1 names `submission.deal_status_code` in the
  Article 4 in-place list.
- **Article 3 (kind tables)**: `deal_status_kind` is a new kind table; the
  hardcoded `TREATY_TYPES` list in the router is deleted in favour of the
  table (T-07). The in-force rule must name Won; it does so through one
  module constant that names the business rule, not a literal spread through
  queries (T-04).
- **Article 7 (one data-access package)**: the `dbo.Client` read is a bound
  `db.execute` on the `LOSS` connection — the safe path — never
  `db.scripts`, and never a write (T-06).
- **Article 6 (no row-level security)**: the libraries' owner filter is a
  plain predicate with no default; the submissions list keeps its "my
  submissions" default. Neither restricts what an analyst can open.
- **Article 8 (server-rendered)**: every filter is a GET with the values in
  the URL; the libraries keep `hx-select="#lib-live"`; the chip inputs and
  the confirm are Alpine slivers.
- **Article 11 (IRP behind an interface)**: no Risk Modeler call anywhere in
  the feature; the sync-from-Risk-Modeler screen is unchanged (FR-017).
- **Article 1 (navigation manifest)**: no new page, no new nav node.

## Project Structure

<!-- Changed areas only, real paths. -->

```text
alembic/versions/0001_initial.py          # deal_status_kind, new columns, view, treaty seed, index INCLUDE
app/routers/_list_filters.py              # new: shared filter parsing, cap 20, messages
app/routers/submissions.py                # statuses + CRM-date routes; filters via _list_filters; TREATY_TYPES deleted
app/routers/edms.py                       # _library_context takes submission filters; poll URL carries them
app/routers/rdms.py                       # same
app/services/submission_service.py        # filter clauses, kinds reads, deal status, CRM dates, row models
app/services/client_service.py            # new: dbo.Client over LOSS, fail-open
app/services/edm_service.py               # list_edms(submission_filters=)
app/services/rdm_service.py               # list_rdms(submission_filters=)
app/templates/pages/submission_detail.html    # title-to-history block rebuilt (T-10); tables unchanged
app/templates/pages/submission_form.html      # client picker, expiration, treaty types from table
app/templates/pages/submissions.html          # CRM ID chips, client, submission status, in force
app/templates/pages/edm_library.html          # filter bar
app/templates/pages/rdm_library.html          # filter bar
app/templates/partials/crm_tags.html          # replaced: CRM ID add/remove with per-CRM dates, inherited marks, same-dates control
app/templates/partials/library_table.html     # poll URL carries every filter
app/templates/partials/submission_list.html   # column header
app/templates/partials/edm_detail_body.html   # "Cedent" → "Cedant"
app/static/js/                                # chip input generalised from yearChips
app/static/css/submissions.css                # redesigned metadata section and controls; inherited mark
infra/scripts/seed_db.py                      # treaty_type_kind MERGE → eleven codes
tests/iteration1_mirror.py                    # new columns, kind table, view DDL, TREATY_SEED
tests/conftest.py                             # LOSS SQLite engine with attached dbo.Client
tests/loss_mirror.py                          # new: Client DDL (spec 014 name and shape)
tests/unit/                                   # see Testing
tests/sqlserver/test_submission_migration.py  # seeds, view, index
docs/FUNCTIONAL_REQUIREMENTS.md · docs/DATA_MODEL.md · docs/PRD.md
docs/ui_previews/submission_detail.html · docs/ui_previews/edm_rdm_library.html
```

## Complexity Tracking

> Only if the Constitution Check has a violation to justify.

None.

## Testing

<!-- Strategy by tier. Not a test-file inventory. -->

- **Unit** (baseline 1725 passed on this branch before any change): the clause
  builder — values OR within, filters AND across, the library `EXISTS` (an EDM
  shared by a Won deal of one owner and an In Process deal of another is not
  listed for Won + that owner; an EDM with no submission is listed only with no
  submission filter); in-force through the view — inclusive bounds, a NULL
  expiration never qualifies, a deal with no CRM ID uses its own dates, a
  per-CRM override wins per column; effective dates and inherited flags on
  `CrmTag`; "make them all the same" nulls every override and is refused when
  not Active; Submission status set without a reason, in Completed, with the
  concurrency 409; `client_service` returns `None`/`{}` when no `LOSS` engine
  is registered and the form still saves; treaty and deal-status kinds read
  from the tables; twenty-one values refused with the filter's message on all
  three lists; the view emits one row per CRM ID and one blank row for a deal
  with none; route tests for the three new POSTs and every new query param.
- **SQL Server integration**: the migration seeds eleven treaty codes and three
  deal statuses; `v_submission_crm_id` exists and `COALESCE` on `DATE` returns
  `DATE`; `ix_submission_list_order` carries the new INCLUDE columns; the
  `dbo.Client` read over `LOSS` (skipped when the table is absent, since spec
  014 owns its bootstrap). Unverified until someone runs `make test-sql`.
- **IRP sandbox**: N/A — no Risk Modeler call.
