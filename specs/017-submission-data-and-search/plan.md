# Implementation Plan: Submission Data and Cross-Entity Search (Iteration 12)

**Branch**: `017-submission-data-and-search` | **Date**: 2026-09-17, amended 2026-09-21 | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** Yes
**Blocked by:** Nothing. The 9/18 build (tasks T001–T053) is on this branch
and is reshaped, not discarded: the filter parser, `client_service`, the chip
inputs, the kind reads and the deal card survive; the schema, the submission
service and the templates that read deal status and deal dates change.

## Design summary

- **Modeling status keeps its tables.** `submission.status_code`,
  `submission_status_kind` and `submission_status_event` stay; only labels
  changed (T-01). `POST /submissions/{id}/statuses` goes back to Modeling
  status alone.
- **`contract` replaces `submission_crm_id`.** One row per CRM ID with
  `treaty_type_code`, `inception_date`, `expiration_date` (all NOT NULL) and
  `contract_status_code` (FK `contract_status_kind`, the renamed
  `deal_status_kind`), plus `updated_at` / `updated_by` as the in-place
  concurrency marker. `submission` loses `treaty_type_code`, `inception_date`,
  `expiration_date` and `deal_status_code` and gains `data_vintage DATE NULL`
  (T-02, T-03).
- **The create form writes the submission and its contracts in one
  transaction.** Contract rows post as parallel repeated fields
  (`contract_crm_id`, `contract_treaty_type`, `contract_inception`,
  `contract_expiration`, `contract_status`, positionally aligned);
  `create_submission` validates every row (CRM ID present and unique across
  the Workbench — one lookup over `v_contract` for the whole form names the
  owning submission; `uq_contract_crm_id` catches the race — treaty type in
  the list, dates parse) and inserts them after the submission row. A blank expiration is filled server-side as inception
  plus one year minus one day; the same rule runs on the page's add and edit
  (T-12).
- **Contract edits on the page are three routes.** `POST …/contracts` adds,
  `POST …/contracts/{cid}` edits attributes (gated on Modeling status Active),
  `POST …/contracts/{cid}/status` sets contract status in any Modeling status
  (P-12), `POST …/contracts/{cid}/delete` removes. Each re-renders the
  contract table fragment. `set_crm_dates`, `reset_crm_dates` and the
  `same-dates` route are deleted (T-02, T-12).
- **In force reads `contract` directly.** `EXISTS (… c.contract_status_code =
  :won AND c.inception_date <= :asof AND c.expiration_date >= :asof)` with
  `:won` from the one module constant. The view `v_contract` is the FR-013
  extract only, a plain join, no `COALESCE` (T-04).
- **The clause builder returns two groups.** `submission_filter_clauses`
  yields submission-level clauses (owner, cedant, client, treaty year,
  Modeling status, name) applied to `s`, and contract-level clauses (CRM IDs,
  treaty types, inception, contract status, in force) wrapped in one `EXISTS`
  over `contract`, so P-18 holds by construction. The libraries wrap the whole
  in their existing `EXISTS` over the association table (T-05).
- **The list orders on an aggregate.** `COALESCE((SELECT MAX(c.inception_date)
  …), s.inserted_at) DESC, s.name`; the sortable Inception column uses the same
  expression; `ix_submission_list_order` is dropped (T-11).
- **The list row summarises its contracts.** One extra query per page
  (`WHERE submission_id IN (…)`, as `_attach_crm_ids` does today) feeds the
  CRM IDs, distinct treaty type labels and latest inception per row, shown as
  "first + N more"; contract status is a filter, not a list column.
- **Treaty year** stays on the submission; `_default_treaty_year` takes the
  earliest contract inception when the field is blank, `None` with no
  contract (P-20).
- **Client and treaty types are unchanged** from 9/18 (T-06, T-07); the
  client label becomes "Client ID" (P-05).
- **Export pre-fill (FR-011).** The export form's `client_id` typeahead
  pre-selects the submission's client, `data_vintage` pre-fills from the
  submission, and a Contract select (one option per contract, pre-selected
  when there is one) fills `crm_id` and `treaty_incept` from data attributes
  in an Alpine sliver; both inputs stay editable. `export_service.list_clients`
  is replaced by `client_service.list_clients` (T-09).
- **`deal_status` is renamed `contract_status`** in code, query params,
  filter labels and the kind table; nothing keeps the old name (T-14).
- **Constitution Article 4** names `submission.deal_status_code` in its
  in-place list; a patch version renames it to `contract.contract_status_code`
  with no rule change (T-13).
- **Preview first** for the two screens whose layout changes: the create
  form with its contract editor and the deal card with the contract table
  replacing the Treaty and Term groups and the CRM band. Five states
  (FR-021). No template or route for either is written before the approval
  (docs/UI_WORKFLOW.md rule 1); the 9/18 waiver and its cost are recorded
  in tasks.md T013.
- **Docs.** FR doc lines 44–62 and 115; `DATA_MODEL.md` §4 and the seed
  table; `PRD.md` §7.2a; the constitution patch.

## Material changes

| Area | Change |
|---|---|
| Database | `rwb_workbench`, edited in `alembic/versions/0001_initial.py` then Rebuild: `deal_status_kind` → `contract_status_kind`; `submission` −`treaty_type_code` −`inception_date` −`expiration_date` −`deal_status_code` +`data_vintage`; `ix_submission_treaty_type_code` and `ix_submission_list_order` dropped; `submission_crm_id` → `contract` with the columns of data-model.md §3; view `v_submission_crm_id` → `v_contract`. `rwb_loss`: read-only. |
| Worker | None. |
| Service | `submission_service`: `Contract` replaces `CrmTag`; `create_submission(…, contracts=[…])`; `add_contract`, `update_contract`, `set_contract_status`, `remove_contract`; `submission_filter_clauses` returns the two groups; the sort expression; `_default_treaty_year` from contracts; `set_statuses` back to Modeling status; `set_crm_dates`, `reset_crm_dates`, `deal_status_kinds` → `contract_status_kinds`. `client_service`, `edm_service`, `rdm_service` unchanged. `export_service.list_clients` deleted. |
| UI | Create/edit form: contract editor rows, data vintage, label Client ID; deal card: contract table with in-place row editing; submissions list: columns and filter labels; libraries: filter label; export form: Contract select and the two pre-fills; four contract POST routes; `statuses` route narrowed. |
| Library | None. No irp-integration call. |
| Docs | FR doc, DATA_MODEL §4 + seed table, PRD §7.2a, constitution Article 4 patch. |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Modeling status keeps `submission.status_code` / `submission_status_kind` / `submission_status_event`; contract status is `contract.contract_status_code` → `contract_status_kind` (the 9/18 `deal_status_kind`, renamed) | Approved | [research.md#R1](research.md#r1--two-statuses-two-tables-t-01-t-02) |
| T-02 | Contract status is an in-place UPDATE on `contract` under its own `updated_at` marker, no reason, no event, in every Modeling status; every other contract attribute is gated on Active | Approved | [research.md#R1](research.md#r1--two-statuses-two-tables-t-01-t-02) |
| T-03 | `contract` replaces `submission_crm_id`; treaty type, both dates and status are NOT NULL on it; `submission` drops the four columns and gains `data_vintage` | Approved | [research.md#R2](research.md#r2--the-contract-grain-t-03) |
| T-04 | `v_contract` is the FR-013 extract only, a plain join; in-force and every filter read `contract` directly | Approved | [research.md#R3](research.md#r3--the-view-is-the-extract-the-predicates-read-the-table-t-04) |
| T-05 | One clause builder returning submission-level and contract-level groups; the contract group is one `EXISTS` over `contract`; the libraries wrap both in one `EXISTS` over the association table | Approved | [research.md#R4](research.md#r4--two-clause-groups-one-exists-each-t-05) |
| T-06 | `submission.client_id INT NULL`, no FK; `client_service` over `LOSS`, fails open | Approved | [research.md#R5](research.md#r5--client-a-stored-id-read-over-loss-t-06) |
| T-07 | Eleven snake_case codes reseed `treaty_type_kind`; the FK moves to `contract` | Approved | [research.md#R6](research.md#r6--treaty-types-from-the-kind-table-t-07) |
| T-08 | `_MAX_FILTER_VALUES` 20 in `app/routers/_list_filters.py` | Approved | [research.md#R7](research.md#r7--the-filter-cap-is-twenty-t-08) |
| T-09 | Export pre-fill: client and data vintage from the submission; a Contract select fills CRM ID and inception client-side; `export_service.list_clients` replaced | Approved | [research.md#R8](research.md#r8--export-pre-fill-t-09) |
| T-10 | The deal card's Treaty and Term groups and the CRM band become one contract table; the create form gains the same row editor; preview before build | Approved | [research.md#R10](research.md#r10--the-deal-card-and-the-create-form-t-10) |
| T-11 | Default sort is `COALESCE(MAX(contract inception), submission.inserted_at) DESC, name`; `ix_submission_list_order` dropped; no denormalised copy | Approved | [research.md#R11](research.md#r11--the-list-sorts-on-a-contract-aggregate-t-11) |
| T-12 | Contract rows post as parallel repeated fields; `create_submission` writes submission and contracts in one transaction; blank expiration filled server-side | Approved | [research.md#R12](research.md#r12--posting-contracts-with-the-form-t-12) |
| T-13 | Constitution Article 4 patch: `contract.contract_status_code` in the in-place list; no rule change | Approved | [research.md#R13](research.md#r13--constitution-patch-t-13) |
| T-14 | `deal_status` → `contract_status` everywhere (kind table, column, query param, labels, service names); no alias kept | Approved | [research.md#R2](research.md#r2--the-contract-grain-t-03) |
| T-15 | `uq_contract_crm_id` unique index on `contract.crm_id` plus one service lookup over `v_contract` that names the owner; the refusal links the owning submission; the owner's status never frees a CRM ID | Approved | [research.md#R14](research.md#r14--crm-id-unique-across-the-workbench-t-15) |
| T-16 | The bulk update is one T-SQL script over a `#crm_status` temp table with a dry-run flag; it updates `contract` directly (no view, no service, no event), clears `updated_by`, and refuses the whole run on an unknown status or a duplicate CRM ID | Approved | [research.md#R15](research.md#r15--the-bulk-update-is-a-script-t-16) |

---

## Technical Context

<!-- Only what changed or constrains the design. The stack is documented in
     docs/PRD.md §3 (Technology stack & environment); architecture rules in
     .specify/memory/constitution.md. Do not restate either. -->

**New dependencies**: None.
**Databases touched**: `rwb_workbench` (schema edits above, all reads and
writes); `rwb_loss` (read-only `SELECT` on `dbo.Client` through the `LOSS`
connection). `rwb_exposure` and DATABRIDGE untouched. Unit tests run the same
SQL text on SQLite through `tests/iteration1_mirror.py`, which mirrors the
`contract` DDL and the view.

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: **no
violations**. One text patch is required (T-13): Article 4's in-place list
names `submission.deal_status_code`, a column this amendment removes.

Material interactions:

- **Article 4 (event-sourced status where it earns it)**: Modeling status
  keeps the named tables (T-01). Contract status is "other status", updated
  in place on `contract` with its own `updated_at` marker (T-02). The
  article's list is patched to the new column name, not its rule.
- **Article 3 (kind tables)**: `contract_status_kind` and `treaty_type_kind`
  feed every menu and filter; the in-force rule names Won through the one
  module constant (T-04).
- **Article 7 (one data-access package)**: the `dbo.Client` read stays a
  bound `db.execute` on `LOSS` (T-06); every contract write goes through
  `submission_service` on `WORKBENCH`; the CRM ID owner lookup is one bound
  `db.execute` over `v_contract` (T-15).
- **Article 8 (server-rendered)**: contract rows are HTMX fragments; the
  create form's row editor and the export form's Contract select are Alpine
  slivers that only add rows and copy values.
- **Article 11 (IRP behind an interface)**: no Risk Modeler call; the sync
  screen is unchanged (FR-017).
- **Article 1 (navigation manifest)**: no new page, no new nav node.

## Project Structure

<!-- Changed areas only, real paths. -->

```text
alembic/versions/0001_initial.py          # contract, contract_status_kind, submission columns, v_contract, indexes
app/routers/_list_filters.py              # contract_status param and label
app/routers/submissions.py                # create/edit with contract rows; contract routes; statuses narrowed; export pre-fill
app/routers/edms.py · app/routers/rdms.py # filter label only
app/services/submission_service.py        # Contract model, contract writes, two clause groups, sort expression, treaty year
app/services/export_service.py            # list_clients deleted
app/templates/pages/submission_form.html      # contract editor rows, data vintage, Client ID label
app/templates/pages/submission_detail.html    # unchanged below the card
app/templates/partials/submission_head.html   # deal card: contract table replaces Treaty/Term groups and CRM band
app/templates/partials/crm_tags.html          # replaced by partials/contract_table.html
app/templates/partials/contract_row.html      # new: one row, display and in-place editor
app/templates/pages/submissions.html          # filter labels; list columns
app/templates/partials/submission_list.html · submission_row.html
app/templates/pages/submission_export_new.html # Contract select; client and data vintage pre-fill
app/static/js/app.js                          # contract row editor sliver; export contract select
app/static/css/submissions.css                # contract table
infra/scripts/seed_db.py                      # contract_status_kind MERGE
infra/scripts/bulk_update_contract_status.sql # CIC's January bulk update: Contract status per CRM ID from a CRM extract
tests/iteration1_mirror.py                    # contract DDL, kind rename, view
tests/sqlserver/test_submission_migration.py  # contract table, view, dropped columns and index
tests/sqlserver/test_bulk_update_contract_status.py # the script's dry run, apply, unknown status and duplicate cases
tests/unit/                                   # see Testing
docs/FUNCTIONAL_REQUIREMENTS.md · docs/DATA_MODEL.md · docs/PRD.md · .specify/memory/constitution.md
docs/ui_previews/submission_contracts.html    # new preview: create form editor + deal card contract table
```

## Complexity Tracking

> Only if the Constitution Check has a violation to justify.

None.

## Testing

<!-- Strategy by tier. Not a test-file inventory. -->

- **Unit** (2,010 passed on this branch before the amendment): create with
  zero, one and three contract rows in one transaction; a blank or duplicate
  CRM ID refuses the whole save with the row named; a CRM ID another
  submission holds refuses create, add and edit with the owner's id and name
  in `ContractInvalid.owner`, in any case or whitespace, on a Completed or
  Cancelled owner too, while a row keeps its own CRM ID on edit; a raw
  case-variant insert trips `uq_contract_crm_id` (T-15); blank expiration filled
  as inception + 1 year − 1 day; treaty year from the earliest contract
  inception and `None` with none; contract status set on a Completed
  submission with no event, other attributes refused when not Active; the
  concurrency 409 on a stale contract `updated_at`; the two clause groups —
  a Won Aggregate XOL and an In Process Per Risk XOL on one submission do
  not satisfy "Per Risk XOL + Won" together; in force per contract —
  inclusive bounds, one Won and one Lost contract qualify, no contract never
  qualifies; the default order with a contract-less submission placed by
  creation date; the library `EXISTS` cases from 9/18 unchanged;
  `v_contract` emits one row per contract and none for a contract-less
  submission; the export form pre-fills client, data vintage and the single
  contract; route tests for the four contract POSTs and every renamed param.
- **SQL Server integration**: the migration creates `contract` with its FKs,
  `contract_status_kind` holds three rows, `submission` has no
  `inception_date` / `treaty_type_code` / `deal_status_code`,
  `ix_submission_list_order` is gone, `v_contract` exists, `COALESCE(DATE,
  DATETIME2)` orders as expected. Unverified until someone runs
  `make test-sql`. The bulk update script: dry run writes nothing, apply
  sets each contract to its status and moves `updated_at`, a second run
  changes nothing, an unknown status or a duplicate CRM ID writes nothing
  (`test_bulk_update_contract_status.py`, run 2026-09-23 from WSL2 against
  `infra-sqlserver-1`). The default sort and every list filter checked
  against 2,000 seeded submissions on a scratch database, 18 checks, one
  page in 6-20 ms (tasks T078, research.md R11).
- **IRP sandbox**: N/A.
