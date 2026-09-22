# Research: Submission Data and Cross-Entity Search (Iteration 12)

## Clarifications

### Session 2026-09-17

- Q: Where do inception and expiration live: deal-level defaults with optional per-CRM-ID overrides (A), CRM-ID grain only with the submission inception dropped and at least one CRM ID required (B), or CRM-ID grain only with a placeholder contract row for submissions without a CRM ID (C)? → A at the time. **Reversed 2026-09-18 on the design call and 2026-09-21 in this spec**: the dates live on the contract only (see the 2026-09-21 session and R2). B and C were rejected on 9/17 for deriving the sort and forcing a CRM ID on deals that have none; the 9/21 answer is neither B nor C — zero contracts are allowed and the sort is an aggregate (R11).
- Q: Is Hold seeded as the fourth Modeling status value (A), a label only (B), or dropped (C)? → C, dropped. Submission status = In Process (now contract status) carries the paused-deal meaning (P-01).
- Q: Is Client optional at creation and afterwards (A), required at creation (B), or required before Won (C)? → A, optional throughout (P-04).
- Q: Where does a Ctrl-J analysis result land? → None. Analysis search is removed from this spec (P-08).
- Q: When a Won submission's CRM ID has no expiration, is it never in force (A), in force for a year (B), or open-ended (C)? → A. **Moot since 2026-09-21**: expiration is NOT NULL on the contract (P-03); the predicate is unchanged.

### Session 2026-09-18

- Q: Does "only Active accepts changes" apply to Submission status? → Editable in every Modeling status (P-12). **Carried to contract status on 2026-09-21.**
- Q: Multi-value filter cap and CRM ID matching? → Twenty, exact match (P-10).
- Q: Do the libraries get a Modeling status filter? → No (P-13).
- Q: Does a CRM ID override the deal's dates per column or as a pair? → Per column. **Moot since 2026-09-21**: there is no deal date to override.
- Q: Labels? → Cedant / Client; the treaty grid column respelled "Cedant" (P-05). **Amended 2026-09-21**: the repository field is labelled "Client ID" (note 32 D14).

### Session 2026-09-21 (note 32 reconciliation)

- Q: Note 32 D17–D20 move status, treaty type, inception and expiration to the CRM ID. Is the CRM ID row now the entity, and what is it called? → Yes: `contract`, the noun FR doc line 57 has used since 8/4 and the word Cheryl and Wendy chose on 9/18. `deal_status` is renamed `contract_status` throughout (P-02, P-15, T-14).
- Q: With treaty type and inception off the submission, what is required at creation: at least one contract (A), treaty type and inception optional on the submission (B), or deal-level defaults kept beside the contract (C)? → None of the three as posed. The create form manages contracts in full — none, one or many, with every attribute — and name and cedant are the only submission fields required (P-16).
- Q: Is a "primary contract" (the first entered, per Wendy's cat-first convention) acceptable as the source of the list sort, the export pre-fill and the treaty-year default? → **No.** The user rejected the concept twice as a hole that would be hard to climb out of. Every place that needed one contract's value was re-derived: the list sorts on the latest contract inception (an aggregate); the export form asks the analyst to pick the contract and picks it only when there is exactly one; the carry-down default is form entry order; treaty year defaults from the earliest contract inception (P-17, P-06, P-03, P-20).
- Q: Is the CRM ID required on a contract, and unique where? → Required; unique within the submission, case-insensitive. Not globally unique: the industry-database workaround reuses one reserved CRM ID across several submissions (P-16).
- Q: Default sort? → Latest contract inception descending, then name; a submission with no contract placed by its creation date in the same key (P-17). "Last updated" was offered as the alternative meaning of "most relevant right now" and not taken, since the client accepted inception descending in FR doc line 116.
- Q: Data vintage required or optional on the submission? → Optional; the EDM often does not exist at creation. The export keeps it required and pre-fills from it (P-19).
- Q: "Client" or "Client ID"? → Client ID (P-05).

## Evidence for the plan's technical decisions

Each section closes one `T-nn` row in [plan.md](plan.md). Codebase facts were
re-read on branch `017-submission-data-and-search` at `227b15c` on
2026-09-21.

### R1 — Two statuses, two tables (T-01, T-02)

**Decision.** Modeling status keeps `submission.status_code`,
`submission_status_kind` and `submission_status_event` unchanged. Contract
status is `contract.contract_status_code NVARCHAR(50) NOT NULL DEFAULT
'IN_PROCESS'` with FK to `contract_status_kind` (`WON`, `LOST`, `IN_PROCESS`),
the 9/18 `deal_status_kind` renamed. It is updated in place under the
contract row's own `updated_at` marker, records no reason and no event, and is
accepted in every Modeling status.

**Rationale.** Constitution Article 4 names the Modeling status tables;
renaming them for a label is an amendment with no behavior gain. Article 4's
"other status is updated in place" covers contract status. Note 32 D16
confirmed one history trail, the modeling one. Note 32 D17 put the status on
the CRM ID: *"your submission status really should go down to the CRM table.
The modeling status is fine to have one modeling status."* P-12 carries over
unchanged: the Won / Lost answer arrives after modeling is Completed.

**History.** 9/17–9/18 built the status as `submission.deal_status_code`
(commits `4eec865`, `dd40ae6`, `7f43069`). Wendy's test case on 9/18 —
several CRM IDs on one modeling package, one bound and one lost — is what a
submission-level column cannot express. The bulk update CIC plans (note 32
D25) carries one status per CRM ID and would have needed a collision rule.

**Alternatives rejected.** One submission per CRM ID (note 32 §8.2: Allstate,
eight CRM IDs, one modeling project; Cheryl: "cumbersome"). Keeping a
submission-level status as a rollup of the contracts: a stored derived value
that the in-force rule would then have to ignore.

### R2 — The contract grain (T-03, T-14)

**Decision.** `submission_crm_id` becomes `contract` with `crm_id`,
`treaty_type_code`, `inception_date`, `expiration_date`,
`contract_status_code`, all NOT NULL, plus `updated_at` / `updated_by`.
`submission` loses `treaty_type_code`, `inception_date`, `expiration_date`
and `deal_status_code` and gains `data_vintage DATE NULL`. `treaty_year`
stays. Zero contracts are allowed. The CRM ID is required on a contract and
unique within its submission case-insensitively (the existing service rule;
no index). Expiration is required and defaults to inception plus one year
minus one day when the form sends blank. The "make them all the same"
control, its route and `reset_crm_dates` are deleted; a new row pre-fills
its dates from the row before it.

**Evidence.** Note 32 D19 (Wendy: treaty type *"goes with the CRM ID, not
with the submission"*), D20 (Cheryl on submission dates: *"Nope, that goes
with the CRM ID as well"*), D15 (expiration required, *"one year minus a
day"*), D22 (Wendy: *"when I go to add the next one, I want it to by default
have the same dates as the one above"*), D23 (data vintage, one per
submission, Wendy's definition: *"the in-force data as-of date of the data
that's been provided to us in the EDM"*). FR doc line 57 has read "CRM ID = a
contract" since 8/4. User decisions 2026-09-21: full contract management on
the create form, CRM ID required, no primary contract.

**Alternatives rejected.**
- Deal-level defaults kept beside the contract (the 9/18 override design):
  D20 removed the deal dates outright, and two sources for one date is what
  the 9/18 build spent its `COALESCE`s reconciling.
- At least one contract required at creation: contradicts FR doc line 57
  and spec FR-004 as the user set it on 9/21.
- A contract with a NULL CRM ID as a placeholder: a contract is its CRM ID
  in CIC's process (Cheryl: *"that's how we store everything"*); a deal
  whose CRM ID is not assigned yet has no contract yet.
- Keeping the table name `submission_crm_id`: leaving a status, a type and a
  term on a table named for a tag is how the noun stayed out of the schema
  for six weeks.
- A CHECK `expiration_date >= inception_date`: no product rule asks for it;
  left out as on 9/17.

### R3 — The view is the extract; the predicates read the table (T-04)

**Decision.** `v_contract` is a plain join of `contract` to `submission`
(data-model.md §4) and exists for FR-013 and CIC's linking SQL. In force and
every list filter read `contract` directly.

**Rationale.** The 9/18 view existed to hold the `COALESCE` fallback rule in
one place. With no fallback there is nothing to centralise, and a predicate
on a base table is simpler and indexable by `ix_contract_submission_id`.
P-09's rules fall out of the SQL: only a Won contract with both dates around
`:asof` qualifies; a submission with no contract has no row to qualify.
`WON` stays one module constant (Article 3 precedent: `ACTIVE`).

**Alternatives rejected.** A view with a synthetic blank row per
contract-less submission (the 9/18 `LEFT JOIN`): the extract's consumers
join on CRM ID, and a row without one has nothing to join. Inlining the
in-force test through the view: one more indirection for no shared rule.

### R4 — Two clause groups, one `EXISTS` each (T-05)

**Decision.** `submission_filter_clauses` returns submission-level clauses
(owner, cedant words, client ids, treaty years, Modeling status codes, name)
applied to `s`, and one `EXISTS (SELECT 1 FROM contract c WHERE
c.submission_id = s.id AND …)` whose body ANDs every contract-level clause of
the request (CRM IDs as `LOWER(TRIM(c.crm_id)) IN (…)`, treaty type codes,
inception date, contract status codes, in force). The libraries wrap the
whole in their existing `EXISTS` over the association table.

**Rationale.** P-18: "Per Risk XOL + Won" must be one contract, not a Per
Risk XOL In Process beside an Aggregate XOL Won. One `EXISTS` body evaluates
the clauses against one contract row, the same construction R4 used on 9/18
for "one submission satisfies every filter". Parameter prefixes are
unchanged (`crm`, `tt`, `inc`, `cs` for contract status, `asof`).

### R5 — Client: a stored id read over `LOSS` (T-06)

Unchanged from 9/18: `submission.client_id INT NULL`, no FK;
`client_service.list_clients()` / `client_names()` / `display()` over the
`LOSS` connection, failing open. Label "Client ID" (P-05, note 32 D14). The
existence check on post runs only while the list is reachable.

### R6 — Treaty types from the kind table (T-07)

Unchanged from 9/18: eleven codes, `aggregate_xol` … `top_and_aggregate`,
sort 10–110; the six provisional codes dropped, Rebuild. The FK moves from
`submission` to `contract`. Confirmed on screen 2026-09-18 (note 32 D13).

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

### R7 — The filter cap is twenty (T-08)

Unchanged from 9/18: `_MAX_FILTER_VALUES = 20` in
`app/routers/_list_filters.py`; eight library filters at 160 parameters stay
under SQL Server's 2,100.

### R8 — Export pre-fill (T-09)

**Decision.** Spec 014 is on `main` (merged 2026-09-18 01:47, PR #110), so
the gate is open. The export form pre-selects `client_id` from the
submission, pre-fills `data_vintage` from `submission.data_vintage`, and adds
a Contract select (one option per contract carrying `data-crm-id` and
`data-inception`) that copies its values into `crm_id` and `treaty_incept`
client-side; the option is pre-selected only when the submission has exactly
one contract. The `POST` is spec 014's, unchanged: what is posted is what is
recorded (P-15).

**Rationale.** Note 32 D21: Cheryl asked where the export pulls its dates
from once the submission has none; Wendy answered "the first one listed",
which is the primary-contract concept the user rejected on 9/21. Asking the
analyst costs one click and is the same choice they make today by typing a
CRM ID into a free-text field (`submission_export_new.html:103`). Cheryl on
exporting per CRM ID: *"That's the same process we have today."*

**Alternatives rejected.** Pre-selecting the first contract by insertion
order: the rejected primary. A server round trip on selection: the two values
are already on the page; an Alpine sliver copies them (Article 8).

### R9 — Things checked that need no change

- **Ctrl-J**: the submission item's `meta` is the cedant; no relabel.
- **Daily digest / notifications**: nothing reads `submission.status_code`.
- **Sync screen (FR-017)**: `/edms/sync` uses `_sync_context`; untouched.
- **Export manifest**: `stage.rwb_loss_result_manifest` keeps `treaty_incept`,
  `crm_id`, `data_vintage` per export (spec 014 FR-006); nothing there reads
  the submission's columns.
- **`links_to_submission_id`, `directory_path`, owner reassignment**:
  untouched.

### R10 — The deal card and the create form (T-10)

**Decision.** The deal card built 9/18 (`partials/submission_head.html`, four
groups Treaty · Term · Status · Owner, and `crm_tags.html` as its CRM band)
is amended: the Treaty and Term groups disappear (their facts are now per
contract) and the CRM band becomes a contract table with headers — CRM ID ·
Treaty type · Inception · Expiration · Contract status · actions — with
in-place row editing and an Add control. The Status group keeps Modeling
status and its history. The card gains Data vintage beside Client ID. The
create form gains the same row editor for zero or more contract rows. A
rendered preview of both screens is approved before templates or routes are
written.

**Evidence.** Note 32 D13–D16: the 9/18 card was accepted on sight
(*"Very nice"*), so its structure stays and only the contract part moves.
Ben on the CRM rows: *"Those probably need headers, so it's clear what this
is."* Cheryl's own working practice is the table: *"here's the five or six
CRM IDs, here's the status on them."* The 9/18 waiver of the US1 preview
(tasks.md T013) cost one rebuild inside a day; the preview is not waived
this time.

### R11 — The list sorts on a contract aggregate (T-11)

**Decision.** `ORDER BY COALESCE((SELECT MAX(c.inception_date) FROM contract c
WHERE c.submission_id = s.id), s.inserted_at) DESC, s.name`. The sortable
Inception column uses the same expression. `ix_submission_list_order` is
dropped. No denormalised inception on `submission`.

**Rationale.** The user rejected a primary contract (2026-09-21). The 85%
case has every contract on one submission sharing its inception (spec
SC-004, note 30 O30-8), so the maximum equals the only value; where
inceptions differ, the newest contract is the one most recently placed. A
contract-less submission is a deal being opened, and its creation date puts
it among the current renewals. SQL Server resolves `COALESCE(DATE,
DATETIME2)` to `DATETIME2` (a date becomes midnight); SQLite compares
ISO-8601 text; both order the same. The dropped index's comment recorded a
plan-shape intent, not a measurement; the submissions table is in the
hundreds of rows.

**Alternatives rejected.** `updated_at DESC` (recency): a different meaning
from the one the client accepted (FR doc line 116). `MIN` inception: places
a deal with one old and one new contract under the old one. A stored
`latest_inception_date` maintained on every contract write: a derived value
with three writers for a sort on a small table.

### R12 — Posting contracts with the form (T-12)

**Decision.** Contract rows post as five repeated fields, positionally
aligned: `contract_crm_id`, `contract_treaty_type`, `contract_inception`,
`contract_expiration`, `contract_status`. The router zips them into
`ContractInput` rows, ignoring a row with every field blank, and
`create_submission` validates and inserts them after the submission row in
the same transaction. Duplicates are checked across the posted rows and, on
the page's add and edit, against the stored rows, case-insensitive and
trimmed. A blank expiration is set to inception plus one year minus one day
in the service so the rule holds for every writer (form, page, tests).

**Rationale.** Repeated names are what HTML forms and Starlette's
`FormData.getlist` do natively, need no JSON, and survive the TestClient's
dict-with-list-values form encoding. One transaction because a submission
with half its contracts is not a state the analyst asked for.

**Alternatives rejected.** A separate "add contracts" step after creation:
the user asked for full management on the create form. Indexed names
(`contract[0][crm_id]`): needs a parser for no gain.

### R13 — Constitution patch (T-13)

`.specify/memory/constitution.md` v4.1.1 added `submission.deal_status_code`
to Article 4's in-place list (its changelog lines 7–14). The column no longer
exists. A patch version (v4.1.2) substitutes `contract.contract_status_code`
with the same description; no rule changes, so no template sync beyond the
version line.
