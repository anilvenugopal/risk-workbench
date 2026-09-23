# Data Model: Submission Data and Cross-Entity Search (spec 017)

Deltas to the current schema, all in `rwb_workbench`
(`alembic/versions/0001_initial.py`, single-revision strategy — edit in place,
then **Rebuild**). `rwb_loss` is read, never changed. Amended 2026-09-21 for
the contract grain (note 32 D17–D23): the 9/18 shape (deal status and dates on
the submission, overrides per CRM ID) is replaced, not extended. Decision
references: [plan.md](plan.md) T-01 … T-14.

## Name mapping

| Analyst sees | Column / table | Values |
|---|---|---|
| Modeling status | `submission.status_code` → `submission_status_kind`; history in `submission_status_event` | `ACTIVE`, `COMPLETED`, `CANCELLED` (unchanged) |
| Contract status | `contract.contract_status_code` → `contract_status_kind` | `WON`, `LOST`, `IN_PROCESS` |
| Contract, CRM ID | `contract` (one row per CRM ID) | |
| Cedant | `submission.cedant_name` | free text (unchanged) |
| Client ID | `submission.client_id` | `dbo.Client.ClientID` in `rwb_loss`; no FK |
| Data vintage | `submission.data_vintage` | date, optional |
| Cedant (treaty grid column) | `irp_treaty.attributes` → `cedant` | Risk Modeler's treaty attribute (unchanged) |

## 1. `contract_status_kind` — kind table (T-01)

`deal_status_kind` renamed. Same shape as every kind table (`code` PK,
`label`, `sort_order`, `inserted_at`). Seed: `('IN_PROCESS', 'In Process',
10)`, `('WON', 'Won', 20)`, `('LOST', 'Lost', 30)`.

## 2. `submission` — four columns removed, one added (T-03)

| Column | Change | Why |
|---|---|---|
| `treaty_type_code` | **removed**, with its FK and `ix_submission_treaty_type_code` | a contract attribute (P-15) |
| `inception_date` | **removed** | a contract attribute (P-03) |
| `expiration_date` | **removed** | a contract attribute (P-03) |
| `deal_status_code` | **removed**, with its FK | a contract attribute (P-02) |
| `data_vintage` | **added** `DATE NULL` | one per submission (P-19) |
| `client_id` | unchanged `INT NULL`, no FK | (P-04) |
| `treaty_year` | unchanged `INT NULL` | stays on the submission (P-20) |

`ix_submission_list_order` is **dropped**: its leading key was
`inception_date DESC`, and the list's order is now a contract aggregate that
no index on `submission` can serve (T-11). `ix_submission_cedant_name` and
`ix_submission_assigned_analyst_id` stay.

## 3. `contract` — replaces `submission_crm_id` (T-03)

| Column | Type | Notes |
|---|---|---|
| `id` | UNIQUEIDENTIFIER PK DEFAULT NEWID() | |
| `submission_id` | UNIQUEIDENTIFIER NOT NULL FK `submission.id` | |
| `crm_id` | NVARCHAR(255) NOT NULL | free text, no format validation; unique across the Workbench case-insensitively, enforced by `uq_contract_crm_id` and the service lookup that names the owner (FR-003, T-15) |
| `treaty_type_code` | NVARCHAR(50) NOT NULL FK `treaty_type_kind.code` | |
| `inception_date` | DATE NOT NULL | |
| `expiration_date` | DATE NOT NULL | the service fills inception + 1 year − 1 day when the form sends blank (P-03) |
| `contract_status_code` | NVARCHAR(50) NOT NULL DEFAULT `'IN_PROCESS'` FK `contract_status_kind.code` | updated in place (Article 4 "other status") |
| `inserted_at` | DATETIME2 NOT NULL DEFAULT GETUTCDATE() | |
| `updated_at` | DATETIME2 NOT NULL DEFAULT GETUTCDATE() | the R1 concurrency marker for in-place edits (T-02) |
| `inserted_by`, `updated_by` | UNIQUEIDENTIFIER NULL FK `app_user.id` | |

Indexes: `ix_contract_submission_id (submission_id)` and
`uq_contract_crm_id (crm_id) UNIQUE`, unfiltered, under the database's
case-insensitive default collation (note 33 D12–D13; research.md R14).

## 4. `v_contract` — view, the FR-013 extract (T-04)

One row per contract. A submission with no contract emits no row.

```sql
CREATE VIEW v_contract AS
SELECT s.id            AS submission_id,
       s.name          AS submission_name,
       s.cedant_name, s.client_id, s.treaty_year, s.data_vintage,
       s.status_code   AS modeling_status_code,
       c.id            AS contract_id,
       c.crm_id, c.treaty_type_code, c.inception_date, c.expiration_date,
       c.contract_status_code
FROM contract c
JOIN submission s ON s.id = c.submission_id
```

The Workbench's own queries read `contract` directly; the view exists for
CIC's linking SQL and the January bulk update, which key on `crm_id`
(note 32 D25).

## 5. Predicates the lists run (T-05, T-11)

**In force as of D** (never stored, FR-018):

```sql
EXISTS (SELECT 1 FROM contract c
        WHERE c.submission_id = s.id
          AND c.contract_status_code = :won
          AND c.inception_date <= :asof AND c.expiration_date >= :asof)
```

**Contract-level filters** (CRM ID, treaty type, inception, contract status,
in force) go into one `EXISTS` over `contract` so one row satisfies them all
(P-18). Submission-level filters (owner, cedant, client, treaty year,
Modeling status, name) stay on `s`.

**Default order** (P-17):

```sql
ORDER BY COALESCE((SELECT MAX(c.inception_date) FROM contract c
                   WHERE c.submission_id = s.id), s.inserted_at) DESC, s.name
```

SQL Server resolves `COALESCE(DATE, DATETIME2)` to `DATETIME2` and a date
sorts as its midnight; SQLite compares the ISO-8601 text, which orders the
same way. The list's sortable "Inception" column uses the same expression.

## 6. `treaty_type_kind` — reseeded (T-07)

Unchanged from 9/18: the six provisional rows are replaced by the eleven
FR-012 rows of [research.md R6](research.md#r6--treaty-types-from-the-kind-table-t-07).
Its FK now comes from `contract`, not `submission`.

## 7. `rwb_loss.dbo.Client` — read only (T-06)

Unchanged: `ClientID INT PK`, `ClientName NVARCHAR(150) NULL`, `ActiveFlag`
not read. Created in dev by spec 014's `bootstrap_loss.py`; unit tests attach
an in-memory SQLite schema named `dbo` to a second engine registered as `LOSS`.

## 8. Read models (service dataclasses)

- `Contract` replaces `CrmTag`: `id`, `submission_id`, `crm_id`,
  `treaty_type_code`, `treaty_type_label`, `inception_date`,
  `expiration_date`, `contract_status_code`, `contract_status_label`,
  `updated_at`.
- `SubmissionRow` (the list): loses `treaty_type_code` / `label`,
  `inception_date`, `expiration_date`, `deal_status_*`; gains
  `treaty_type_labels` (distinct, seed order), `latest_inception_date`,
  `data_vintage`.
- `Submission` (the page): the same losses, plus `contracts: list[Contract]`
  in insertion order and `data_vintage`.
- `EdmRow` / `RdmRow` unchanged; `list_edms` / `list_rdms` keep
  `submission_filters`.

## 9. Docs to update (owners)

- `docs/DATA_MODEL.md` §4: the Submission and Contract entities, the seed
  table row for `contract_status_kind`, the view, `uq_contract_crm_id`.
- `docs/PRD.md` §7.2a: Modeling status vs Contract status; §7.2 line 467
  and §7.2b: the CRM ID is unique across the Workbench and is the hard block
  in the otherwise soft identity pattern.
- `docs/FUNCTIONAL_REQUIREMENTS.md`: the lines FR-020 names.
- `.specify/memory/constitution.md` Article 4: the in-place list names
  `contract.contract_status_code` instead of `submission.deal_status_code`
  (patch version, no rule change).
