# Data Model: Submission Data and Cross-Entity Search (spec 017)

Deltas to the current schema, all in `rwb_workbench`
(`alembic/versions/0001_initial.py`, single-revision strategy — edit in place,
then **Rebuild**). `rwb_loss` is read, never changed. Decision references:
[plan.md](plan.md) T-01, T-03, T-04, T-06, T-07.

## Name mapping

| Analyst sees | Column / table | Values |
|---|---|---|
| Modeling status | `submission.status_code` → `submission_status_kind`; history in `submission_status_event` | `ACTIVE`, `COMPLETED`, `CANCELLED` (unchanged) |
| Submission status | `submission.deal_status_code` → `deal_status_kind` | `WON`, `LOST`, `IN_PROCESS` |
| Cedant | `submission.cedant_name` | free text (unchanged) |
| Client | `submission.client_id` | `dbo.Client.ClientID` in `rwb_loss`; no FK |
| Cedant (treaty grid column) | `irp_treaty.attributes` → `cedant` (unchanged) | Risk Modeler's treaty attribute; column head respelled from "Cedent" |

## 1. `deal_status_kind` — new kind table (T-01)

| Column | Type | Notes |
|---|---|---|
| `code` | NVARCHAR(50) PK | |
| `label` | NVARCHAR(255) NOT NULL | |
| `sort_order` | INT NOT NULL | |
| `inserted_at` | DATETIME2 NOT NULL DEFAULT GETUTCDATE() | |

Seed: `('IN_PROCESS', 'In Process', 10)`, `('WON', 'Won', 20)`,
`('LOST', 'Lost', 30)`.

## 2. `submission` — three new columns (T-01, T-03, T-06)

| Column | Type | Notes |
|---|---|---|
| `deal_status_code` | NVARCHAR(50) NOT NULL DEFAULT `'IN_PROCESS'`, FK `deal_status_kind.code` | Updated in place; no event table (Article 4 "other status"). |
| `expiration_date` | DATE NULL | Deal-level default for every CRM ID (P-03). |
| `client_id` | INT NULL | `dbo.Client.ClientID` over the `LOSS` connection; validated against the list at write time when the list is reachable; never a FK (other database). |

Unchanged: `inception_date DATE NOT NULL` stays the list's default sort, the
inception filter and the treaty-year default (P-03, FR-006).

Index change: `ix_submission_list_order` INCLUDE gains `deal_status_code`,
`expiration_date`, `client_id` so the master list page still reads from one
index.

## 3. `submission_crm_id` — override pair (T-03)

| Column | Type | Notes |
|---|---|---|
| `inception_date` | DATE NULL | Overrides `submission.inception_date` for this CRM ID when set. |
| `expiration_date` | DATE NULL | Overrides `submission.expiration_date` for this CRM ID when set. |

Effective dates are `COALESCE(c.inception_date, s.inception_date)` and
`COALESCE(c.expiration_date, s.expiration_date)`, per column. "Inherited"
on the page means the override column is NULL. "Make them all the same" sets
both columns to NULL for every row of the submission.

Existing behavior kept: `crm_id` free text, no format validation, unique
within a submission case-insensitively in `add_crm_id` (FR-007).

## 4. `v_submission_crm_id` — view (T-04)

One row per CRM ID; a submission with no CRM ID emits one row with
`crm_tag_id` and `crm_id` NULL. Both the FR-013 extract and the in-force
predicate read it.

| Column | Source |
|---|---|
| `submission_id` | `s.id` |
| `submission_name` | `s.name` |
| `cedant_name`, `treaty_type_code`, `treaty_year` | `s.*` |
| `crm_tag_id` | `c.id` (NULL when none) |
| `crm_id` | `c.crm_id` (NULL when none) |
| `effective_inception_date` | `COALESCE(c.inception_date, s.inception_date)` |
| `effective_expiration_date` | `COALESCE(c.expiration_date, s.expiration_date)` |
| `modeling_status_code` | `s.status_code` |
| `deal_status_code` | `s.deal_status_code` |
| `client_id` | `s.client_id` |

Definition: `FROM submission s LEFT JOIN submission_crm_id c ON c.submission_id
= s.id`. Full text in [research.md R3](research.md#r3--one-view-for-the-extract-and-for-in-force-t-04).

**In force as of D** (never stored, FR-018): a row of the view with
`deal_status_code = 'WON' AND effective_inception_date <= D AND
effective_expiration_date >= D`. A NULL effective expiration never qualifies.

## 5. `treaty_type_kind` — reseeded (T-07)

The six provisional rows (`cat_xol`, `quota_share`, `surplus`, `per_risk_xol`,
`aggregate_xol`, `stop_loss`) are replaced by the eleven FR-012 rows listed in
[research.md R6](research.md#r6--treaty-types-from-the-kind-table-t-07).
Column shape unchanged. Dev rows referencing a dropped code break the FK, so
the lifecycle choice is Rebuild.

## 6. `rwb_loss.dbo.Client` — read only (T-06)

| Column | Type | Use |
|---|---|---|
| `ClientID` | INT PK | stored in `submission.client_id` |
| `ClientName` | NVARCHAR(150) NULL | display `"ID - name"` |
| `ActiveFlag` | VARCHAR(1) NULL | not read (spec 014 P-18) |

Created in dev by spec 014's `infra/scripts/bootstrap_loss.py`; this branch
adds no DDL to `rwb_loss`. Unit tests attach an in-memory SQLite schema named
`dbo` to a second engine registered as `LOSS` so the same SQL text runs.

## 7. Read models (service dataclasses)

- `SubmissionRow` / `Submission` gain `deal_status_code`, `deal_status_label`,
  `expiration_date`, `client_id`, `client_name` (`None` when the list is
  unreachable).
- `CrmTag` gains `inception_date`, `expiration_date` (the overrides),
  `effective_inception_date`, `effective_expiration_date`,
  `inception_inherited`, `expiration_inherited`.
- `EdmRow` / `RdmRow` unchanged; `list_edms` / `list_rdms` take
  `submission_filters`.

## 8. Docs to update (owners)

- `docs/DATA_MODEL.md` §4: the Submission bullet list (two statuses, client,
  dates, the view) and the seed table row for `treaty_type_kind`.
- `docs/PRD.md` §7.2a: Modeling status vs Submission status.
- `docs/FUNCTIONAL_REQUIREMENTS.md`: FR-020's three corrections plus the
  status row (line 62), the filters row (line 114) and the label rows (lines
  50–51).
