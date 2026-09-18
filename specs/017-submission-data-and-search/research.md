# Research: Submission Data and Cross-Entity Search (Iteration 12)

## Clarifications

### Session 2026-09-17

- Q: Where do inception and expiration live: deal-level defaults with optional per-CRM-ID overrides (A), CRM-ID grain only with the submission inception dropped and at least one CRM ID required (B), or CRM-ID grain only with a placeholder contract row for submissions without a CRM ID (C)? → A: Deal-level inception (required) and expiration (optional) stay on the submission as defaults; each CRM ID carries an optional inception/expiration override pair; "make them all the same" clears the overrides. Rejected B and C: both would derive the list's default sort, the inception filter and the treaty-year default, and force a CRM ID or a placeholder row on deals that have none yet (P-03).
- Q: Is Hold seeded as the fourth Modeling status value with the Completed / Cancelled edit lock (A), seeded as a label only that still accepts edits (B), or dropped (C)? → A: C, dropped. Modeling status stays Active / Completed / Cancelled; the 8/5 Hold request closes because Submission status = In Process now carries the paused-deal meaning (P-01).
- Q: Is Client optional at creation and afterwards (A), required at creation with creation blocked while the repository is unreachable (B), or optional at creation but required before Submission status = Won (C)? → A: A, optional throughout. Deals open before the repository has a client row and the Workbench cannot create one; a blank client matches no client filter (P-04).
- Q: Where does a Ctrl-J analysis result land: the analyses list with the row expanded (A), the owning submission's Results section (B), a new per-analysis page (C), or Risk Modeler directly (D)? → A: None. Analysis search is removed from this spec: the cross-submission analyses list, the Analyses group in Ctrl-J and the per-analysis page all leave scope; the need stays in note 30 O30-11 for a later spec (P-08). Removed with it: user story 4, FR-018 (analyses list), FR-019 (analysis-to-submission linkage), FR-020 (Ctrl-J Analyses group), the Analysis entity and SC-002; FR-021..023 renumbered FR-018..020 and SC-003..005 renumbered SC-002..004.
- Q: When a Won submission's CRM ID has no expiration, is it never in force (A), in force until inception plus one year with the expiration marked as assumed (B), or in force open-ended from inception (C)? → A: A, never in force. A blank expiration is missing data; the in-force query is used for event response and retention, where a false positive costs more than an omission, and the empty result shows which deals still need an expiration (P-09).

### Session 2026-09-18

- Q: Does the existing "only Active accepts changes" rule apply to Submission status: editable in every Modeling status (A), only while Active (B), or while Active or Completed but locked when Cancelled (C)? → A: A, editable in every Modeling status. The cedant's Won / Lost answer usually arrives after modeling is Completed; gating on Active would force a reopen-and-complete cycle with two reasons recorded to set a value that carries none (P-12; closes the T-02 assumption in R1).
- Q: Do multi-value filters cap at twenty with CRM ID values matched as substrings (A), keep the current 400 cap (B), cap CRM ID alone at twenty (C), or cap at twenty with CRM ID values matched exactly (D)? → A: D. Twenty is the documented cap (FR doc line 114, SC-001) and keeps eight library filters at 160 parameters under SQL Server's 2,100 limit. Exact match because the filter is a pasted list of complete CRM IDs, CRM ID is the one guaranteed-unique attribute (FR doc line 54), and substring matching would let "1234" hit "12345"; it also replaces twenty ORed `LIKE` predicates with one `IN` list. Substring matching had only been carried over from the single CRM ID box, which the chip list replaces (P-10).
- Q: Do the EDM and RDM libraries get a Modeling status filter, which FR-015 omits but the R4 predicate builder included: no (A) or yes, as a ninth filter (B)? → A: A, no. The library stories are about deals and contracts (Won, in force, whose deal), not where modeling stands; the submissions list already filters on Modeling status (P-13).
- Q: Does a CRM ID override the deal's dates per column, inception alone or expiration alone (A), or as a pair where entering one date requires the other (B)? → A: A, per column. Note 30 O30-8 says expiration is the date that most commonly differs while inception is shared; each blank date inherits the deal's on its own and the page marks "inherited" per date (P-03 wording corrected from "pair").
- Q: Labels as proposed, Cedant / EDM cedent / Client (A), Cedant / RM cedent / Client (B), or Cedant / EDM cedent / Repository client (C)? → A: Neither. The user corrected the premise: an EDM has no cedant; the exposure value is Risk Modeler's treaty attribute `cedant`, shown once as a column of the treaty grid on the EDM detail page, where the context already says whose it is. Two labels only, Cedant (submission) and Client (repository); the treaty grid column is respelled "Cedant"; "cedent" is removed from templates and the functional requirements document, whose line 50 wrongly calls it an EDM field. Client menu "ID - name", matching ID or name, stands (P-05).

## Evidence for the plan's technical decisions

Each section closes one `T-nn` row in [plan.md](plan.md). Codebase facts were
read on branch `017-submission-data-and-search` at `b8c4cbe` (main) on
2026-09-17; branch `016-ty-perspective-export` (which carries the merged spec
014 build) was read for the client-table pattern.

### R1 — Two statuses, two columns, one of them new (T-01, T-02)

**Decision.** Modeling status keeps `submission.status_code`,
`submission_status_kind` and `submission_status_event` unchanged; only labels
move. Submission status is a new column `submission.deal_status_code NVARCHAR(50)
NOT NULL DEFAULT 'IN_PROCESS'` with FK to a new kind table `deal_status_kind`
(`WON`, `LOST`, `IN_PROCESS`). It is updated in place through the
`reassign_owner` pattern (`UPDATE … WHERE id = :id AND updated_at = :expected`),
records no reason and no event, and is accepted in every Modeling status.

**Rationale.**
- Constitution Article 4 names `submission.status_code` and
  `submission_status_event` as *the* event-sourced status. Renaming them to
  `modeling_*` would be a constitution amendment plus edits in
  `submission_service.set_status`, `get_status_history`, `_require_active`,
  `tests/iteration1_mirror.py`, `tests/sqlserver/test_submission_migration.py`
  and `docs/DATA_MODEL.md`, for a label change. The label is a template edit.
- The kind-table name `submission_status_kind` is therefore taken by Modeling
  status, so the new status is `deal_status` in code and schema; the UI label
  is "Submission status" (P-02). `data-model.md` records the mapping.
- Article 4 says "other status is updated in place" and P-02 says no reason and
  no history, so a second event table would contradict both.
- T-02: the cedant's Won / Lost answer arrives after modeling finishes;
  Completed is the normal Modeling status of a Won deal. Gating the deal
  status on Active would force a reopen-set-complete cycle with two reasons to
  record a value that carries none. `_require_active` is not applied.
  Confirmed by P-12 (2026-09-18 clarification; spec acceptance 1.8).

**Alternatives rejected.**
- Rename the three Modeling-status tables to `modeling_status_*` and reuse
  `submission_status_kind` for Won / Lost / In Process: reuses a name for a new
  meaning in a codebase where the old meaning is still on `main` in six
  documents and three test files.
- Add `WON` / `LOST` / `IN_PROCESS` to `submission_status_kind`: violates
  non-negotiable 1 (one filter, one history, one menu).
- Event-source the deal status: no product rule asks for its history (P-02).

### R2 — Dates: deal defaults with per-CRM-ID overrides (T-03)

**Decision.** `submission.inception_date` stays `NOT NULL`;
`submission.expiration_date DATE NULL` is added. `submission_crm_id` gains
`inception_date DATE NULL` and `expiration_date DATE NULL`. A CRM ID's
effective inception is `COALESCE(c.inception_date, s.inception_date)` and its
effective expiration `COALESCE(c.expiration_date, s.expiration_date)`, per
column, so a CRM ID may override expiration alone (note 30 O30-8: "it's the
expiration date that's probably more commonly different"). "Make them all the
same" runs `UPDATE submission_crm_id SET inception_date = NULL, expiration_date
= NULL WHERE submission_id = :id`. The date routes call `_require_active`
exactly as `add_crm_id` / `remove_crm_id` do.

**Rationale.** P-03 chose shape A in the 2026-09-17 clarification: the
deal-level inception keeps the list's default sort, its inception filter and
the treaty-year default (`_default_treaty_year`) untouched, and
`ix_submission_list_order` keeps its key. Overrides as nullable columns on the
existing join table need no new table and make "inherited" a per-column
`IS NULL` test the page can render.

**Alternatives rejected.**
- A separate `submission_crm_id_dates` table: one more join for two nullable
  columns.
- Storing the effective date on every CRM row and copying the deal date down
  on insert: "make them all the same" would then have to write, and a later
  change to the deal date would not reach the CRM IDs (non-negotiable 2 wants
  in-force computed, not stored).
- A CHECK that expiration ≥ inception: no product rule asks for it and with
  per-column overrides the effective pair is not on one row; left out.

### R3 — One view for the extract and for in-force (T-04)

**Decision.** A view in `rwb_workbench`:

```sql
CREATE VIEW v_submission_crm_id AS
SELECT s.id            AS submission_id,
       s.name          AS submission_name,
       s.cedant_name, s.treaty_type_code, s.treaty_year,
       c.id            AS crm_tag_id,
       c.crm_id,
       COALESCE(c.inception_date,  s.inception_date)  AS effective_inception_date,
       COALESCE(c.expiration_date, s.expiration_date) AS effective_expiration_date,
       s.status_code      AS modeling_status_code,
       s.deal_status_code,
       s.client_id
FROM submission s
LEFT JOIN submission_crm_id c ON c.submission_id = s.id
```

The in-force predicate on every list is

```sql
s.deal_status_code = :won
AND EXISTS (SELECT 1 FROM v_submission_crm_id v
            WHERE v.submission_id = s.id
              AND v.effective_inception_date <= :asof
              AND v.effective_expiration_date >= :asof)
```

with `:won` bound from `submission_service.WON = "WON"`. The Won test is on
the outer row, not inside the view: the optimizer does not reliably push a
predicate on a view column out through the correlation, and on the outer row
the covering `ix_submission_list_order` (which gains `deal_status_code` in its
INCLUDE) removes Lost and In Process deals before any join. The `COALESCE`
dates cannot be indexed either way; that follows from P-09's "computed, never
stored" (2026-09-17 review).

**Rationale.**
- FR-013 (one row per CRM ID, a blank row for a deal with none, effective dates,
  both statuses, client) is the view's row shape verbatim; note 30 O30-9 asked
  for "the extract shape written down (columns, grain, and the date-fallback
  rule)" — the view is that document, executable.
- P-09's three rules fall out of the SQL: a deal with no CRM ID has one row
  whose effective dates are the deal's (LEFT JOIN); a NULL expiration makes
  `>= :asof` unknown, so the row never qualifies; Won is the only status that
  qualifies.
- One definition of the fallback rule, read by the filter and by CIC's linking
  query (note 30 O30-10, "link up SQL tables").
- SQLite supports `CREATE VIEW`, `COALESCE` and `LEFT JOIN` with this exact
  text, so the unit tier runs it unchanged; ISO-8601 `TEXT` dates compare
  correctly. SQL Server returns `DATE` from `COALESCE(DATE, DATE)`.
- Alembic: `op.execute("CREATE VIEW …")` in `upgrade`, `DROP VIEW` first in
  `downgrade`. `CREATE VIEW` must be alone in its batch on SQL Server, which a
  single `op.execute` satisfies.

**Alternatives rejected.**
- Inline the `COALESCE` in `list_submissions` and again in the library
  `EXISTS`: the fallback rule would live in two query strings and nowhere CIC
  can read.
- A Python-side extract endpoint (CSV): no consumer asked for a download; the
  extract's consumers are SQL queries against the Workbench (O30-10).

`WON` as a constant: Article 3 forbids status literals "baked into internal
code paths"; the codebase's precedent is `submission_service.ACTIVE` used by
`_require_active`. The in-force rule *is* the business rule that names Won
(P-09), so one named constant in the same module follows the precedent.

### R4 — One predicate builder and one `EXISTS` per library (T-05)

**Decision.** `submission_service.submission_filter_clauses(filters, alias="s")
-> tuple[list[str], dict]` builds the ANDed clauses for owner ids, cedant words,
client ids, treaty type codes, treaty years, CRM IDs (an `IN` list over
`LOWER(TRIM(c.crm_id))` inside one `EXISTS` over `submission_crm_id`, P-10),
deal status codes, modeling status codes (submissions list only, P-13),
inception date and in-force date, using the existing `_in_clause`,
`_word_and_clauses` and `_escape_like` helpers from `app/services/_common.py`.
`list_submissions` appends the clauses to its own `WHERE`. `edm_service.list_edms`
and `rdm_service.list_rdms` gain `submission_filters: dict | None`; when any
value is set they append

```sql
AND EXISTS (SELECT 1 FROM submission_edm a
            JOIN submission s ON s.id = a.submission_id
            WHERE a.edm_id = irp_edm.id AND <clauses>)
```

(`submission_rdm` / `rdm_id` / `irp_rdm` for RDMs).

**Rationale.** FR-016 requires an EDM to match when *one* linked submission
satisfies *every* submission-attribute filter together. Filtering the joined
rows and collapsing would let a Won deal of Ben's and an In Process deal of
Cheryl's qualify one EDM for "Won + Cheryl" (acceptance 3.2). A single
`EXISTS` whose body ANDs all clauses evaluates them against one submission row
at a time, which is the required semantics with no post-processing. The
existing `list_edms` returns every row unpaged and then `_attach_submissions`;
the `EXISTS` narrows before that. Parameter prefixes (`owner`, `c`, `crm`,
`tt`, `ty`, `ds`, `ms`, `cl`, `inc`, `asof`) keep the libraries' own `:q` /
`:status` params from colliding.

Name search and import status on the libraries stay on the entity row itself,
outside the `EXISTS` (FR-016).

**Alternatives rejected.**
- Reuse `list_submissions` to get matching submission ids, then `IN (...)` on
  the association table: two queries and a parameter list that grows with the
  book.
- A JOIN + `DISTINCT`: the acceptance-3.2 bug described above.

### R5 — Client: a stored id read over `LOSS` (T-06)

**Decision.** `submission.client_id INT NULL`, no foreign key — `dbo.Client`
lives in `rwb_loss`, another database. New `app/services/client_service.py`:

- `list_clients() -> list[Client] | None`: `SELECT ClientID, ClientName FROM
  dbo.Client ORDER BY ClientName, ClientID` on `connection="LOSS"`; `None` when
  the connection raises (`sqlalchemy.exc.SQLAlchemyError`).
- `client_names(ids) -> dict[int, str]`: one `IN` query for a page's distinct
  ids; `{}` on failure.
- `display(client_id, name)`: `"27 - Travelers Corporate Cat"`, or `"27 (name
  unavailable)"` when the list is unreachable.

On create and edit, a posted `client_id` is parsed as an int; when
`list_clients()` returns a list and the id is not in it, the form re-renders
with a field error; when it returns `None`, the id is stored as posted (the
form only offers ids from the list, and the failure mode P-04 cares about is a
blank client on an unreachable repository, which always saves).

**Evidence.** Branch `016-ty-perspective-export` (spec 014 merged in) reads
`dbo.Client` the same way: `export_service.list_clients` at `:385-387`, the
existence check `SELECT 1 AS x FROM dbo.Client WHERE ClientID = :c` at
`:476-478`, the display join at `:571-573`, all on `connection="LOSS"`
(note 31 §3). Spec 014 P-18: every row, active and retired alike, `ActiveFlag`
is not a filter. Its unit tier registers a second SQLite engine as `LOSS` with
an attached in-memory schema named `dbo` (`tests/conftest.py:234-248`,
`tests/loss_mirror.py`) so `dbo.Client` resolves unchanged; this branch copies
that arrangement with the `Client` DDL only. The dev `rwb_loss` gets
`dbo.Client` from 014's `infra/scripts/bootstrap_loss.py` /
`db/bootstrap/loss_dev_mirror.sql`; this branch adds no loss bootstrap, so
in a dev stack without 014 applied the client field reads "list unavailable"
and everything else works — which is FR-009's own scenario.

Filter menus: `multi_picker("client", "Client", options, selected,
search_placeholder="ID or name")` over `list_clients()`, option label
`"ID - name"`; the picker already narrows in place on typed text (the owner
picker uses the same macro), which satisfies P-05's "matches on ID or name".
When the list is `None` the picker renders disabled with the unavailable
message.

**Alternatives rejected.**
- A Workbench `client` mirror table synced from LOSS: a sync job and a
  second source of truth for a list the Workbench must never write (P-04).
- Caching `list_clients()` in process: the list is small (tens of rows) and
  read once per page render; no measurement says otherwise.

### R6 — Treaty types from the kind table (T-07)

**Decision.** `treaty_type_kind` is reseeded with eleven rows; the six
provisional codes are deleted, not migrated (P-07). Codes and labels:

| code | label | sort |
|---|---|---|
| `aggregate_xol` | Aggregate XOL | 10 |
| `aggregate_cat_xol` | Aggregate Cat XOL | 20 |
| `risk_aggregate_xol` | Risk Aggregate XOL | 30 |
| `per_occurrence_xol` | Per Occurrence XOL | 40 |
| `per_occurrence_cat_xol` | Per Occurrence Cat XOL | 50 |
| `per_risk_xol` | Per Risk XOL | 60 |
| `stop_loss` | Stop Loss | 70 |
| `reinstatement_premium_protection` | Reinstatement Premium Protection | 80 |
| `second_third_fourth_event_risk_exposed` | Second/Third/Fourth Event - Risk Exposed | 90 |
| `top_and_drop` | Top & Drop | 100 |
| `top_and_aggregate` | Top & Aggregate | 110 |

`app/routers/submissions.py:60-64` (`TREATY_TYPES`) is deleted;
`submission_service.treaty_type_kinds()` mirrors `status_kinds()` and feeds
the create/edit form and every treaty-type filter. The same eleven rows go
into `infra/scripts/seed_db.py`'s `MERGE`, `tests/iteration1_mirror.py`'s
`TREATY_SEED`, and `tests/sqlserver/test_submission_migration.py`.

**Evidence.** Note 30 D28 / O30-12 (CIC's list read out by Cheryl on 9/15);
the user fixed the eleven labels on 2026-09-17 (spec FR-012). `per_risk_xol`,
`aggregate_xol` and `stop_loss` keep their codes because the labels are the
same structure; existing dev rows on `cat_xol`, `quota_share` or `surplus`
would break the FK, which is why the DB lifecycle for this iteration is
**Rebuild**.

### R7 — The filter cap is twenty (T-08)

**Decision.** `_MAX_FILTER_VALUES` becomes 20 and moves, with the parsing and
the two message templates, to `app/routers/_list_filters.py`, called by the
three list routers.

**Evidence.** Commit `9da1165` introduced the cap at 20 with the D16 filters;
commit `87021d6` ("Preserve unavailable submission filters", no body) raised it
to 400 with the comment "four filters at this limit plus the text-search
parameters stay below SQL Server's 2,100-parameter limit".
`docs/FUNCTIONAL_REQUIREMENTS.md:114` still says "twenty values per filter is
the cap", spec FR-019 says twenty, SC-001 says "up to twenty CRM IDs". With
eight multi-value filters on the libraries, 400 per filter could reach 3,200
parameters and hit the limit the comment cites; 20 per filter caps at 160.
Confirmed by P-10 (2026-09-18 clarification): the one risk is an analyst who
relied on pasting more than twenty owners or years, which nobody has reported.

### R8 — Export pre-fill waits for spec 014 (T-09)

**Decision.** FR-011 is one task, gated on `014-results-export` reaching
`main`. Its inputs are delivered here: `submission.client_id` and the view's
`effective_inception_date` for the CRM ID the form pre-fills. When 014 merges,
`submission_export_new.html`'s `client_id` typeahead_select pre-selects the
submission's client, `treaty_incept` pre-fills from the effective inception of
the pre-filled CRM ID instead of `submission.inception_date`
(`app/routers/submissions.py:1571` on branch 016), and
`export_service.list_clients` is replaced by `client_service.list_clients`.

**Evidence.** `git branch --merged main` lists neither `014-results-export`
nor `016-ty-perspective-export`; `main`'s log ends at `b8c4cbe`. Spec 014
P-15 (edited form values recorded on the export only) already covers CRM ID
and inception; note 31 O31-7 asked to extend it to the client, which spec P-06
does.

### R9 — Things checked that need no change

- **Ctrl-J** (`app/services/search_service.py:62-71`): the submission item's
  `meta` is the cedant, not the status, so FR-001's "Ctrl-J metadata" needs no
  relabel today.
- **Daily digest / notifications**: no module under `app/notifications` reads
  `submission.status_code`, so the 8/5 Hold rationale ("takes the submission
  out of the daily digest") has no code to remove.
- **Treaty cedant column**: the exposure value appears once, as the "Cedent"
  column head of the EDM detail treaty grid
  (`app/templates/partials/edm_detail_body.html:120`, with the same spelling in
  `treaty_row.html`'s comment); FR-010 respells it "Cedant" and adds no
  qualifier (P-05). The deal header of the EDM page carries no cedant by spec
  004 FR-011.
- **CRM ID uniqueness (FR-007)**: `add_crm_id` already treats a case-insensitive
  duplicate as a no-op; no index is added.
- **Sync screen (FR-017)**: `/edms/sync` uses `_sync_context`, separate from
  `_library_context`; untouched.

### R10 — The submission page above the tables is rebuilt (T-10)

**Decision.** The block of `app/templates/pages/submission_detail.html` from
the title to the status history is redesigned as one metadata section and one
set of editing controls, with a rendered preview approved before any template
or route is written. The EDM, RDM, analyses and exports tables below it are
not touched.

**Evidence.** User direction, 2026-09-18: the EDM / RDM / analysis / exports
tables are fine; the metadata section at the top needs cleaning up, and the UX
for updating Modeling and Submission status, creating CRM IDs, and updating
inception and expiration all needs rework.

What the page does today (`submission_detail.html:17-131`):
- `detail-head`: `h1` with the status chip; a created / links-to / directory
  line; a `deal-facts` row of Cedant, Treaty type, Treaty year, Inception.
- Two `detail-band`s: Owner (inline `reassign` form when Active) and CRM
  (`partials/crm_tags.html`: an "Add CRM ID" box plus chips with a remove
  form each, rendered read-only when not Active).
- A `form-banner--readonly` reading "read-only. Reopen it to make changes."
- A "Status" section: Mark complete, Cancel deal with an optional reason
  input, or Reopen, as three separate forms; then the history list.

What this iteration would add to that stack if patched: a Submission status
select (editable in every Modeling status, P-12, which the read-only banner
contradicts), a Client fact, a deal-level expiration, and a table of CRM IDs
with two dates each plus inherited marks and a same-dates control (FR-004,
FR-005). Six inline forms and two bands on one header is the reason to rebuild
rather than extend.

**Alternatives rejected.**
- Add the new fields and controls to the existing header and bands: the
  outcome above.
- Move status editing to a modal: no user asked for it; decide in the preview.

