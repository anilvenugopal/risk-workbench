# Tasks: Submission Data and Cross-Entity Search (Iteration 12)

**Input**: Design documents from `specs/017-submission-data-and-search/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/routes.md, research.md, quickstart.md

**Tests**: plan.md §Testing names the unit assertions each area must carry; they are
listed here as tasks in the story that owns them. `uv run pytest tests/unit` after
every task (baseline 1725 passed). The SQL Server tier is unverified until the
developer runs `make test-sql`.

**Organization**: one phase per user story, in spec priority order. Per
`docs/UI_WORKFLOW.md` rule 2, **stop at the end of each story phase** for the
approver to click the running feature before the next story starts.

## Format: `[ID] [P?] [Story] [Ref] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1, US2, US3 — spec.md user stories 1–3
- **[Ref]**: `FR-nnn` from spec.md, `T-nn` / `P-nn` from the decision tables
- `- Proof:` names the test or observation that closes the task when it is not obvious

## Path Conventions

Single project at the repo root: `app/`, `alembic/`, `infra/`, `tests/`, `docs/`.

---

## Phase 1: Setup

- [x] T001 Run `uv run pytest tests/unit` and record the baseline count (plan.md says 1725 passed) so every later report names the delta
- [x] T002 Correct `specs/017-submission-data-and-search/contracts/routes.md` §3 so the `crm_id` row matches spec P-10 / FR-014 and plan T-05: each value matches a whole CRM ID exactly, case-insensitive and trimmed, one `IN` list over `LOWER(TRIM(crm_id))` (done 2026-09-18 during /speckit-analyze)

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: schema, mirrors, kind reads, the shared filter parser and the client
read every story depends on. No user-visible change yet.

- [x] T003 [FR-013] [T-01] [T-03] [T-04] [T-06] [T-07] Edit `alembic/versions/0001_initial.py`: add `deal_status_kind` (code, label, sort_order, inserted_at) with seed `IN_PROCESS`/`WON`/`LOST` (data-model.md §1); add `submission.deal_status_code NVARCHAR(50) NOT NULL DEFAULT 'IN_PROCESS'` FK `deal_status_kind.code`, `submission.expiration_date DATE NULL`, `submission.client_id INT NULL` (no FK); add `submission_crm_id.inception_date DATE NULL`, `submission_crm_id.expiration_date DATE NULL`; add `op.execute("CREATE VIEW v_submission_crm_id …")` with the exact text of research.md R3 (own `op.execute`, `DROP VIEW` first in `downgrade`); replace the six provisional `treaty_type_kind` rows with the eleven codes of research.md R6; extend `ix_submission_list_order` `mssql_include` with `deal_status_code`, `expiration_date`, `client_id`. DB lifecycle: Rebuild (developer runs `make db-rebuild`)
- [x] T004 [P] [T-07] [FR-012] Replace the `treaty_type_kind` `MERGE` rows in `infra/scripts/seed_db.py` with the eleven codes and labels of research.md R6 (`aggregate_xol` … `top_and_aggregate`, sort 10–110)
- [x] T005 [P] [T-01] [T-03] [T-04] [T-07] Mirror the schema in `tests/iteration1_mirror.py`: `deal_status_kind` DDL + seed, the three `submission` columns, the two `submission_crm_id` columns, `CREATE VIEW v_submission_crm_id` (same SQL text as the migration), `TREATY_SEED` = eleven rows, and the view/kind table added to the drop order list at line ~358
- [x] T006 [P] [T-06] Create `tests/loss_mirror.py` with the `dbo.Client` DDL (`ClientID INT PK`, `ClientName`, `ActiveFlag`; data-model.md §6) and extend `tests/conftest.py` so the app-DB fixture also registers a second in-memory SQLite engine as `LOSS` with an attached schema named `dbo` (the spec 014 conftest pattern, research.md R5); add a fixture that seeds `27 - Travelers Corporate Cat` and `41 - <name>`, and a fixture variant that registers no `LOSS` engine
- [x] T007 [P] [T-01] [T-04] [T-07] Extend `tests/sqlserver/test_submission_migration.py`: `treaty_type_kind` holds exactly the eleven codes; `deal_status_kind` holds three; `v_submission_crm_id` exists and `COALESCE` on `DATE` returns `DATE`; `ix_submission_list_order` INCLUDE carries `deal_status_code`, `expiration_date`, `client_id`; a `dbo.Client` read over `LOSS` skipped when the table is absent
  - Proof: unverified until the developer runs `make test-sql`; say so in the handoff
- [x] T008 [T-01] [T-07] In `app/services/submission_service.py`: add `WON = "WON"` beside `ACTIVE`; add `treaty_type_kinds()` and `deal_status_kinds()` mirroring `status_kinds()` (line ~793); add `deal_status_code`, `deal_status_label`, `expiration_date`, `client_id`, `client_name` to `SubmissionRow` and `Submission` and populate them in `_to_row` / `get_submission` (client_name filled by T-06's `client_names`, `None` when unreachable); add `inception_date`, `expiration_date`, `effective_inception_date`, `effective_expiration_date`, `inception_inherited`, `expiration_inherited` to `CrmTag` and fill them in `list_crm_ids` and `_attach_crm_ids` from `COALESCE` per column (data-model.md §7)
- [x] T009 [P] [T-06] [FR-008] [FR-009] Create `app/services/client_service.py` per contracts/routes.md §5: `Client` dataclass; `list_clients()` runs `SELECT ClientID, ClientName FROM dbo.Client ORDER BY ClientName, ClientID` through `db.execute(..., connection="LOSS")` and returns `None` on `SQLAlchemyError` or when no `LOSS` engine is registered; `client_names(ids)` one `IN` query, `{}` on failure; `display(client_id, name)` → `"27 - Travelers Corporate Cat"` or `"27 (name unavailable)"`. Read only, never `db.scripts`
- [x] T010 [T-08] [FR-019] Create `app/routers/_list_filters.py`: move `_SEARCH_MAX_CHARACTERS`, `_SEARCH_MAX_WORDS`, `_MAX_FILTER_VALUES` (400 → **20**), the label dicts and `_filter_validation_error` out of `app/routers/submissions.py` (lines ~880–915) into one `parse_list_filters(query_params, *, multi_keys, text_keys) -> (filters, error)` that returns the `filters` dict keyed as contracts §5 (`owner_ids`, `name`, `cedant_name`, `treaty_type_codes`, `treaty_years`, `status_codes`, `inception_date`) plus the one-line message `"{Label} accepts 20 values or fewer."`; `submissions.py` calls it and keeps its `owner` default / `owner=any` behavior
- [x] T011 [T-05] Add `submission_filter_clauses(filters, alias="s") -> tuple[list[str], dict]` to `app/services/submission_service.py` covering today's filters (owner ids, name words, cedant words, treaty type codes, treaty years, Modeling `status_codes`, inception date) with the parameter prefixes of research.md R4, using `_in_clause` / `_word_and_clauses` / `_escape_like` from `app/services/_common.py`; `list_submissions` (line ~714) appends the returned clauses instead of building its own. Behavior is unchanged in this task; US1–US3 add keys
- [x] T012 [P] [FR-013] Unit tests for Phase 2 in `tests/unit/test_submission_service.py`, `tests/unit/test_client_service.py` (new) and `tests/unit/test_submission_routes.py`: `treaty_type_kinds()` returns eleven rows in sort order and `deal_status_kinds()` three; a new submission reads `deal_status_code == "IN_PROCESS"`; `client_service` returns `None` / `{}` with no `LOSS` engine and the list with one; `SELECT * FROM v_submission_crm_id` emits one row per CRM ID and one blank-CRM row for a submission with none; twenty-one owners on the submissions list returns `Owner accepts 20 values or fewer.` and no rows (replace the existing 400-cap assertion)

**Checkpoint**: unit tier green, `make db-rebuild` is the developer's call before any click-through.

---

## Phase 3: User Story 1 — Two statuses and dates per CRM ID (P1)

**Goal**: the submission page shows Modeling status and Submission status apart, Submission status is editable in every Modeling status with no reason, each CRM ID shows effective inception and expiration with inherited marks, one control resets them all, and the submissions list filters the two statuses in separate controls.

**Independent test**: quickstart.md §3 Story 1, steps 1–8.

### Preview (docs/UI_WORKFLOW.md rule 1 — approval before templates or routes)

- [ ] T013 [US1] [FR-021] [T-10] [P-14] Rewrite `docs/ui_previews/submission_detail.html` from `docs/ui_previews/_scaffold.html`: one metadata section (name, Modeling status, Submission status, owner, Cedant, Client as "ID - name", treaty type, treaty year, deal inception, deal expiration, CRM IDs with effective dates and an inherited mark per date) and one set of controls (Modeling status transition with reason + history; Submission status plain select; add/remove CRM ID; edit a CRM ID's two dates in place; "Make them all the same"; edit deal dates). Four states on the board: Active with CRM IDs mixing inherited and entered dates; Active with no CRM ID; Completed (everything locked except Submission status, read-only banner reworded); client with the repository unreachable. Tables below the history are not in the preview
  - Proof: approver's informal 👍 on the rendered file. **Stop here until approved.** On approval, write the approved fragment ids into contracts/routes.md §1 (the deal-status POST target) and quickstart.md §3 Story 1 before T018 starts
  - Waived by the developer on 2026-09-18 ("Do not worry about creating UI previews"): T021–T023 were built without a preview; the fragment id `#deal-head` is recorded in contracts/routes.md §1

### Service

- [x] T014 [US1] [FR-002] [T-02] [P-12] Add `set_deal_status(*, submission_id, to_status, expected_updated_at, actor_id)` to `app/services/submission_service.py`: validate `to_status` against `deal_status_kinds()`, in-place `UPDATE submission SET deal_status_code = :to, updated_at = … WHERE id = :id AND updated_at = :expected` on the `reassign_owner` pattern (line ~968), raise the existing concurrency error on zero rows; **no** `_require_active`, no event row, no reason
- [x] T015 [US1] [FR-003] [FR-004] [FR-005] [T-03] Add `set_crm_dates(*, crm_tag_id, inception_date, expiration_date, actor_id)` (blank → NULL per column, `_require_active` on the owning submission) and `reset_crm_dates(*, submission_id, actor_id)` (`UPDATE submission_crm_id SET inception_date = NULL, expiration_date = NULL WHERE submission_id = :id`, `_require_active`) to `app/services/submission_service.py`; confirm `remove_crm_id` (line ~1091) deletes the row and so its overrides (FR-004)
- [x] T016 [US1] [FR-003] [P-03] Accept `expiration_date` (optional) in `create_submission` (line ~349) and `update_submission` (line ~893) in `app/services/submission_service.py`; leave `inception_date` required and `_default_treaty_year` reading deal-level inception (FR-006)
- [x] T017 [US1] [FR-002] [FR-014] [T-05] Add `deal_status_codes` to `submission_filter_clauses` (`s.deal_status_code IN`, prefix `ds`) and to `parse_list_filters` as multi param `deal_status` labelled "Submission status"; relabel the existing `status` param "Modeling status" in `_MULTI_FILTER_LABELS` (`app/routers/_list_filters.py`, `app/services/submission_service.py`)

### Routes

- [x] T018 [US1] [FR-002] [T-02] Add `POST /submissions/{sid}/deal-status` to `app/routers/submissions.py` per contracts §1: fields `to_status`, `expected_updated_at`, CSRF; success re-renders the metadata section fragment for HTMX and redirects otherwise; 409 banner on `updated_at` mismatch; 422 on an unknown code
- [x] T019 [US1] [FR-004] [FR-005] [T-03] Add `POST /submissions/{sid}/crm-ids/{tag_id}/dates` (fields `inception_date`, `expiration_date`, ISO or blank; 422 on an unparseable date; 409 `SubmissionClosed`) and `POST /submissions/{sid}/crm-ids/same-dates` (no fields; 409 `SubmissionClosed`) to `app/routers/submissions.py`, both re-rendering `#crm-tags`, beside the existing `crm-ids` routes at lines ~1539–1559
- [x] T020 [US1] [FR-001] Relabel the status chip, `POST /submissions/{sid}/status` responses and the history heading "Modeling status" in `app/routers/submissions.py` context and templates; the values stay Active / Completed / Cancelled and no Hold is offered anywhere

### Templates and styles (approved preview only)

- [x] T021 [US1] [FR-021] [T-10] Rebuild `app/templates/pages/submission_detail.html` from the title to the status history exactly as the approved preview: metadata section, Modeling status controls (transition, reason, history), Submission status select posting to `/deal-status` live in every Modeling status, deal inception/expiration edit, read-only banner reworded so it no longer says "Reopen it to make changes" for Submission status. Lines ~132 onward (EDM, RDM, analyses, exports tables) are untouched
- [x] T022 [US1] [FR-004] [FR-005] Replace `app/templates/partials/crm_tags.html`: one row per CRM ID with CRM ID · effective inception · effective expiration · actions (edit dates in place, remove); an inherited date carries class `crm-date--inherited` and title "Inherited from the deal"; a "Make them all the same" button with `hx-confirm` posting to `/crm-ids/same-dates`; add and edit controls rendered read-only when Modeling status is not Active
- [x] T023 [P] [US1] [FR-021] Add the metadata section, controls and `.crm-date--inherited` styles to `app/static/css/submissions.css` per the approved preview, reusing existing tokens and classes
- [x] T024 [P] [US1] [FR-003] Add the optional `expiration_date` field to `app/templates/pages/submission_form.html` and its create/edit handling in `app/routers/submissions.py`
- [x] T025 [US1] [FR-001] [FR-002] Submissions list: in `app/templates/pages/submissions.html` relabel the existing status `multi_picker` "Modeling status" and add a separate `multi_picker("deal_status", "Submission status", …)` from `deal_status_kinds()`; pass both kind lists from `list_submissions_page` in `app/routers/submissions.py`; add the Submission status column header/cell to `app/templates/partials/submission_list.html` and `submission_row.html`

### Tests

- [x] T026 [US1] [FR-002] [FR-003] [FR-004] [FR-005] [FR-006] [T-02] [T-03] Unit tests in `tests/unit/test_submission_service.py`: `set_deal_status` saves Lost with no reason and Modeling status unchanged; saves Won on a Completed and on a Cancelled submission; raises the concurrency error on a stale `updated_at`; `list_crm_ids` reports inherited flags per column and a per-CRM expiration override wins while inception stays inherited; `reset_crm_dates` nulls every override and is refused when not Active; `set_crm_dates` refused when not Active; a removed CRM ID takes its dates; the list's default order is still `inception_date DESC` and `_default_treaty_year` still reads the deal-level inception after the schema change
- [x] T027 [P] [US1] [FR-001] [FR-002] Route tests in `tests/unit/test_submission_routes.py`: the three new POSTs (success fragment, 409, 422, CSRF-less reject), the page shows "Modeling status" and "Submission status" as separate labels and no "Hold", the list offers `status` and `deal_status` as two pickers, `deal_status=WON` narrows the list

**Checkpoint / STOP**: unit tier green. Hand off for the quickstart §3 Story 1 click-through before starting US2.

---

## Phase 4: User Story 2 — Client and treaty type from CIC's lists (P1)

**Goal**: the create/edit form offers CIC's repository clients as "ID - name" matched on ID or name, saves without one when blank or when the list is unreachable, offers exactly the eleven treaty types read from the kind table, and every page spells the treaty attribute "Cedant".

**Independent test**: quickstart.md §3 Story 2, steps 1–5 (step 6 waits for spec 014).

- [ ] T028 [US2] [FR-012] [T-07] Delete `TREATY_TYPES` from `app/routers/submissions.py` (lines ~61–64) and replace its three uses (lines ~177, ~721, ~1027) with `submission_service.treaty_type_kinds()`; the form select and the list's treaty-type picker read the table
- [ ] T029 [US2] [FR-008] [FR-009] [T-06] [P-04] Client on create and edit in `app/routers/submissions.py` and `app/services/submission_service.py`: parse optional `client_id` as int; when `client_service.list_clients()` returns a list and the id is not in it, re-render with field error "Choose a client from the list."; when it returns `None`, store as posted; blank stores NULL; `create_submission` / `update_submission` accept `client_id`
- [ ] T030 [US2] [FR-008] [FR-009] [P-05] In `app/templates/pages/submission_form.html` add the Client field using the `typeahead_select` macro (`app/templates/partials/form_macros.html:30`) over `list_clients()` with option text "ID - name" (narrows on ID or name); when the list is `None` render it disabled with "Client list unavailable — the submission saves without one" and post nothing; keep the free-text field labelled "Cedant"
- [ ] T031 [US2] [FR-008] [FR-014] Client filter on the submissions list: add `client_ids` to `submission_filter_clauses` (`s.client_id IN`, integers, prefix `cl`; NULL never matches) and `client` to `parse_list_filters`; in `app/templates/pages/submissions.html` add `multi_picker("client", "Client", options, selected, search_placeholder="ID or name")` over `list_clients()`, rendered disabled with the unavailable message when `None`; show `client_name` via `client_service.display` in the metadata section built in T021 and in `submission_row.html` using one `client_names()` call per page
- [ ] T032 [P] [US2] [FR-010] [P-05] Respell "Cedent" → "Cedant" in `app/templates/partials/edm_detail_body.html:120` and the comment in `app/templates/partials/treaty_row.html:3,7`; confirm `grep -rni cedent app/templates` returns nothing
- [ ] T033 [P] [US2] [FR-008] [FR-009] [FR-010] [FR-012] Unit tests: `tests/unit/test_submission_routes.py` — form offers eleven treaty types and a twelfth row inserted into the SQLite `treaty_type_kind` appears on the form and in the list picker without a code change; client `27` saves and the page shows "27 - Travelers Corporate Cat"; blank client saves; with no `LOSS` engine the form shows the unavailable message and saves; a posted id not in a reachable list returns the field error; `client=27` narrows the list and a NULL-client submission never matches. `tests/unit/test_edm_detail_header.py` or `test_treaty_display.py` — treaty grid head reads "Cedant"
- [ ] T034 [US2] [FR-011] [T-09] [P-06] **Deferred until spec 014 is on `main`.** In `app/templates/pages/submission_export_new.html` pre-select `client_id` from the submission; in the export route (`app/routers/submissions.py`, the `treaty_incept` pre-fill) read the effective inception of the pre-filled CRM ID from `v_submission_crm_id` instead of `submission.inception_date`; replace `export_service.list_clients` with `client_service.list_clients`; an edited client or inception is recorded on the export only
  - Proof: quickstart §3 Story 2 step 5 — export with client 41 records 41, submission still reads 27

**Checkpoint / STOP**: unit tier green. Hand off for the quickstart §3 Story 2 click-through before starting US3.

---

## Phase 5: User Story 3 — Find EDMs and RDMs by the deals they belong to (P1)

**Goal**: the submissions list, EDM library and RDM library filter on multi-value CRM IDs (exact), Submission status, client and "in force as of"; the libraries add owner, cedant, treaty type and treaty year; an EDM or RDM matches when one linked submission satisfies every filter together.

**Independent test**: quickstart.md §3 Story 3, steps 1–7.

### Predicates

- [ ] T035 [US3] [FR-014] [FR-016] [P-10] [T-05] In `submission_filter_clauses` (`app/services/submission_service.py`) add `crm_ids`: one `EXISTS (SELECT 1 FROM submission_crm_id c WHERE c.submission_id = s.id AND LOWER(TRIM(c.crm_id)) IN (:crm0, …))` with values lowered and trimmed in Python, prefix `crm`; remove the single-CRM-ID `LIKE` clause
- [ ] T036 [US3] [FR-018] [T-04] [P-09] Add `in_force_as_of` to `submission_filter_clauses`: `s.deal_status_code = :won AND EXISTS (SELECT 1 FROM v_submission_crm_id v WHERE v.submission_id = s.id AND v.effective_inception_date <= :asof AND v.effective_expiration_date >= :asof)` with `:won` bound from `WON` (research.md R3); a NULL expiration never qualifies by construction
- [ ] T037 [US3] [FR-015] [FR-016] [T-05] Add `submission_filters: dict | None = None` to `edm_service.list_edms` (`app/services/edm_service.py:311`) and `rdm_service.list_rdms` (`app/services/rdm_service.py:98`): when any value is set, append `AND EXISTS (SELECT 1 FROM submission_edm a JOIN submission s ON s.id = a.submission_id WHERE a.edm_id = irp_edm.id AND <clauses>)` (`submission_rdm` / `rdm_id` / `irp_rdm` for RDMs) from `submission_filter_clauses(filters, alias="s")`; no filters → no `EXISTS`, so unlinked EDMs stay listed. Name search and import status stay on the entity row

### Parsing and the submissions list

- [ ] T038 [US3] [FR-014] [FR-018] [FR-019] In `app/routers/_list_filters.py` add: `crm_id` as a multi param (label "CRM ID", cap 20, exact-match values), `in_force` flag `1` and `as_of` date defaulting to today → `filters["in_force_as_of"]`; expose the library key set (`cedant`, `crm_id`, `owner`, `deal_status`, `client`, `treaty_type`, `treaty_year`, `in_force`) so `edms.py` / `rdms.py` know when any submission filter is set. The libraries carry no Modeling status param (P-13); their `status` param stays the import status
- [ ] T039 [US3] [FR-014] Generalise `Alpine.data('yearChips', …)` in `app/static/js/app.js:669` into a chip input that takes an optional validator, and in `app/templates/pages/submissions.html` replace the CRM ID text input with `chip-input` posting repeated `crm_id` values; add the "In force as of" checkbox + date input (default today) wired to `in_force` / `as_of`; the existing inception filter, default sort and `owner` default are unchanged (FR-006)

### Library filter bar (preview first)

- [ ] T040 [US3] [FR-015] Update `docs/ui_previews/edm_rdm_library.html`: the library header with name search and import status beside the eight submission-attribute filters (owner with no default, cedant, client, treaty type, treaty year, CRM ID chips, Submission status, in force as of); states: no filter set, filters set with rows, filters set with the over-cap banner, empty result
  - Proof: approver's informal 👍. **Stop here until approved.**
- [ ] T041 [US3] [FR-015] [FR-016] In `app/routers/edms.py` `_library_context` (line ~88) and `app/routers/rdms.py` `_library_context` (line ~55): parse the §3 params through `parse_list_filters`, pass `submission_filters` to `list_edms` / `list_rdms`, pass the kind lists, analyst list and client list for the pickers, and surface the cap message as the library banner; `GET /edms/table` and `GET /rdms/table` accept every param
- [ ] T042 [US3] [FR-015] Add the filter bar to `app/templates/pages/edm_library.html` and `app/templates/pages/rdm_library.html` exactly as the approved preview (reuse `multi_picker`, the chip input, and the in-force control from T039); build the `#lib-live` poll URL in `app/templates/partials/library_table.html:17` from the request's own query string so a filtered view keeps polling filtered
- [ ] T043 [US3] [FR-017] Confirm `/edms/sync` (`_sync_context`) and `app/templates/pages/edm_sync.html` are untouched: name search and paging only
  - Proof: existing `tests/unit/test_edm_sync_routes.py` unchanged and green

### Tests

- [ ] T044 [US3] [FR-014] [FR-016] [FR-018] [P-09] [P-10] Unit tests in `tests/unit/test_submission_service.py`: six CRM IDs OR within the filter; "12345" is not matched by "1234"; case and whitespace are ignored; filters AND across; in force: inclusive bounds, a NULL expiration never qualifies, a deal with no CRM ID uses its own dates, a per-CRM override wins per column, Lost and In Process never qualify; the `WON` constant is the only literal
- [ ] T045 [P] [US3] [FR-015] [FR-016] [FR-019] Unit tests in `tests/unit/test_edm_service.py`, `tests/unit/test_rdm_service.py`, `tests/unit/test_libraries.py`: an EDM shared by a Won deal of Cheryl's and an In Process deal of Ben's is listed for Won + Cheryl and not for Won + Ben; an EDM linked to two matching submissions is listed once; an EDM with no submission is listed only when no submission filter is set; RDM library behaves the same; twenty-one CRM IDs on each of the three lists returns `CRM ID accepts 20 values or fewer.` and no rows; the poll URL in the fragment carries every filter param

**Checkpoint / STOP**: unit tier green. Hand off for the quickstart §3 Story 3 click-through.

---

## Phase 6: Polish and cross-cutting

- [ ] T046 [P] [FR-020] [P-05] Correct `docs/FUNCTIONAL_REQUIREMENTS.md`: line 62 names Modeling status (Active, Completed, Cancelled) and Submission status (Won, Lost, In Process), no Hold; line 72's parked CRM item lists expiration date and Submission status with their two consumers, event response and retention; line 114 lists the new filters and the twenty cap; line 117 marks global search Implemented with six groups (pages, submissions, EDMs, RDMs, analysis templates, users); line 118 no longer says only the submissions list has search or filter; line 50's remark names the treaty attribute, not "a specific EDM field"; every "cedent" → "cedant"
- [ ] T047 [P] [T-01] [T-04] [T-07] Update `docs/DATA_MODEL.md` §4 (line ~84): the Submission bullets for the two statuses, `deal_status_kind`, `client_id`, `expiration_date`, the `submission_crm_id` override pair and `v_submission_crm_id`; the seed-table row for `treaty_type_kind` (line ~765) names eleven codes
- [ ] T048 [P] [P-01] [P-02] Update `docs/PRD.md` §7.2a (line ~480): Modeling status vs Submission status, and the line-1275 note that `status` on the submissions list is Modeling status
- [ ] T049 Subtraction review of the whole diff per AGENTS.md §Code Quality: remove comments that restate the code, inline one-caller helpers, drop the old CRM ID `LIKE` path and any 400-cap remnant, delete `docs/ui_previews` states not built; `grep -rni "cedent" app docs/FUNCTIONAL_REQUIREMENTS.md` returns nothing
- [ ] T050 Run `uv run pytest tests/unit` and report "unit tier, N passed (baseline 1725)"; state that the SQL Server tier (T007) is unverified until the developer runs `make test-sql`, and that T034 stays open until spec 014 merges

---

## Dependencies

- Phase 2 blocks every story. Within Phase 2: T003 → T005 (same DDL text); T006 → T009 tests; T008 → T011; T010 → T011 → T012.
- **US1** (Phase 3): T013 preview approval blocks T021–T023. T014–T017 can start during preview review. T018–T019 need T014–T015. T025 needs T017.
- **US2** (Phase 4): needs US1's T021 (metadata section shows Client) and Phase 2's T009. T028 and T032 are independent of US1. T034 is gated on spec 014 reaching `main`.
- **US3** (Phase 5): needs T011 and T017 (clause builder), T031 (client key). T040 preview approval blocks T042. T035–T039 can proceed during preview review.
- Phase 6 needs all three stories.

Story order is fixed by the UI workflow (one story at a time, click-through between): US1 → US2 → US3.

## Parallel examples

- Phase 2: T004, T005, T006, T007, T009 in parallel once T003's DDL text is fixed; T012 after T008–T011.
- US1: T023, T024 in parallel with T021–T022; T027 in parallel with T026.
- US2: T028, T032, T033 in parallel with T029–T031.
- US3: T035–T037 in parallel with T038–T039; T045 in parallel with T044.
- Phase 6: T046, T047, T048 in parallel.

## Implementation strategy

1. Phase 1 + Phase 2, then `make db-rebuild` (developer) — nothing user-visible yet.
2. **MVP = US1**: preview → approval → service, routes, templates → click-through. Delivers the two statuses and dates per CRM ID (SC-002, SC-004).
3. US2 adds the client and the eleven treaty types (SC-003); T034 lands separately when spec 014 merges.
4. US3 adds the search (SC-001), with the library preview approved before the bar is built.
5. Phase 6 docs and subtraction review; report tiers by name and count.
