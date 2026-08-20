# Data Model: Package Retirement

## 1. Removed schema

- Drop `package`.
- Drop `submission_package`.
- Drop `package_id` from `irp_edm`, `irp_rdm`, `irp_job`, and `irp_analysis`.
- Drop indexes and foreign keys whose only column is `package_id`.

## 2. `submission_edm`

One row relates an existing submission to an existing EDM.

| Column | Type | Null | Rule |
|---|---|---|---|
| `submission_id` | UUID | no | FK to `submission.id` |
| `edm_id` | UUID | no | FK to `irp_edm.id` |
| `inserted_at` | DATETIME2 | no | defaults to current UTC time |
| `inserted_by` | UUID | yes | FK to `app_user.id` |

- Primary key: (`submission_id`, `edm_id`).
- Index: (`edm_id`, `submission_id`) for entity-to-submission library/detail reads.
- Detach deletes the association row. It does not update or soft-delete `irp_edm`.

## 3. `submission_rdm`

One row relates an existing submission to an existing RDM.

| Column | Type | Null | Rule |
|---|---|---|---|
| `submission_id` | UUID | no | FK to `submission.id` |
| `rdm_id` | UUID | no | FK to `irp_rdm.id` |
| `inserted_at` | DATETIME2 | no | defaults to current UTC time |
| `inserted_by` | UUID | yes | FK to `app_user.id` |

- Primary key: (`submission_id`, `rdm_id`).
- Index: (`rdm_id`, `submission_id`) for entity-to-submission library/detail reads.
- Detach deletes the association row. It does not update or soft-delete `irp_rdm`.

## 4. `irp_edm` and `irp_rdm`

Both tables remain global physical-resource rows. Remove only `package_id` and its
foreign key/index. A row may have no submission association, including an adopted
Risk Modeler EDM and a standalone import.

Submission lists are derived through `submission_edm` or `submission_rdm`; no cached
submission ID or count is stored on the entity.

Each table has nullable `notes NVARCHAR(250)`. The note describes the EDM or RDM
row and is shared by every related submission. Blank input is stored as null.
Note updates stamp `updated_at` and `updated_by`.

## 5. `irp_job`

- Remove `package_id` and `ix_irp_job_package_id`.
- Keep `irp_edm_id` and `irp_rdm_id` as execution targets.
- Add nullable `requested_from_submission_id` as an FK to `submission.id`.
- `requested_from_submission_id` records request provenance only. Polling, retries,
  worker dispatch, and target lookup must not depend on it.
- Add an index on `requested_from_submission_id` for contextual job links.

## 6. `irp_analysis`

- Remove `package_id` and `ix_irp_analysis_package_id`.
- Standalone RDM imports write `edm_id = NULL`.
- Replace the broker-analysis identity constraint with UNIQUE (`rdm_id`, `irp_id`).
- Keep nullable `edm_id` for future analyst-run analyses whose owning EDM is known;
  broker-analysis display must not resolve or claim an EDM relationship.

## 7. Relationships

```text
submission 1--N submission_edm N--1 irp_edm
submission 1--N submission_rdm N--1 irp_rdm
irp_edm    1--N irp_portfolio
irp_edm    1--N irp_treaty
irp_rdm    1--N irp_analysis       (broker analyses; edm_id null)
irp_edm    1--N irp_job            (when the job targets an EDM)
irp_rdm    1--N irp_job            (when the job targets an RDM)
submission 1--N irp_job            (optional request provenance)
```

There is no direct EDM-to-RDM relationship. A contextual EDM page selects EDMs from
`submission_edm` and RDMs from `submission_rdm` using the same `submission_id`.

## 8. Rebuild order

Create in foreign-key order:

1. Existing kind and user tables.
2. `submission` and its existing children.
3. `irp_edm` and `irp_rdm` without `package_id`.
4. `submission_edm` and `submission_rdm`.
5. Job, analysis, portfolio, and treaty tables.

Downgrade drops `submission_rdm` and `submission_edm` before `irp_rdm`, `irp_edm`,
and `submission`.
