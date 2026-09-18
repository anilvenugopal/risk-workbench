# Quickstart: Submission Data and Cross-Entity Search (spec 017)

How to verify the feature. Contracts: [contracts/routes.md](contracts/routes.md);
schema: [data-model.md](data-model.md).

## Prerequisites

- Docker stack up (`make dev-up`) or WSL2 native — **starting the stack is the
  developer's call**; agents report and stop if it is down.
- DB lifecycle: **Rebuild** — `make db-rebuild` (destructive) after the
  migration edit (new kind table, columns, view, treaty reseed). Dev
  submissions on the dropped treaty codes do not survive; that is P-07.
- `rwb_loss` holding `dbo.Client`: apply spec 014's `make bootstrap-loss` from
  the `014-results-export` / `016-ty-perspective-export` checkout against the
  same SQL Server. Without it the client field reads "Client list unavailable"
  and everything else works (FR-009).
- No worker or poller is involved; the web process is enough.

## 1. Unit tier (no containers)

```bash
uv run pytest tests/unit
```

Baseline before this feature: 1725 passed. Covers the filter clause builder,
the library `EXISTS`, in-force through the view, effective dates,
"make dates the same", Submission status writes,
`client_service` fail-open, kind reads, the twenty-value cap on all three
lists, the view's row shape, and the new routes.

## 2. SQL Server tier

```bash
make test-sql        # Docker
make wsl-test-sql    # WSL2
```

Covers the seeds (eleven treaty codes, three deal statuses), the view and its
`DATE` result type, the `ix_submission_list_order` INCLUDE columns, and the
`dbo.Client` read (skipped when the table is absent). **Unverified until
someone runs it.**

## 3. Click-through

### Story 1 — two statuses, dates per CRM ID

1. Open any submission. The deal card's **Status** group shows **Modeling:
   Active** and **Submission: In Process** as two labelled pills.
2. Press the Status pencil. The pills become two selects and a reason box, in
   place. Set Submission to **Lost** and Save. The Modeling pill and the
   Modeling history are unchanged — no event is recorded.
3. Press the pencil again, set Modeling to **Completed** with a reason and
   Submission to **Won**, and Save. Both change in one write; **Modeling
   history** gains one entry, carrying the reason (T-02, P-12).
4. Press **+ Add CRM ID** on an Active deal. The field appears with the deal's
   dates already in it; add three, accepting the dates. Each row reads the
   deal's inception and expiration.
5. Press the Term pencil and change the deal's expiration. All three rows
   follow it.
6. Press one CRM ID's row pencil and enter an expiration three years later.
   Only that row changes, and it reads exactly like the others.
7. Click **Make dates the same**, confirm. All rows read the deal dates again.
8. Submissions list: the **Modeling status** picker offers Active, Completed,
   Cancelled; the **Submission status** picker offers Won, Lost, In Process.
   No Hold anywhere.

### Story 2 — client and treaty type

1. Open **New submission**. Type `27` in Client — the menu offers
   `27 - Travelers Corporate Cat`; type `Trav` — same row. Pick it and save.
   The submission page shows **Client: 27 - Travelers Corporate Cat**, the
   free-text field is labelled **Cedant**.
2. Create another submission leaving Client blank. It saves.
3. Stop `rwb_loss` (or point `MSSQL_LOSS_DATABASE` at a missing database) and
   reload the form: the Client field says the list is unavailable; saving
   without a client still works.
4. The Treaty type select and the list's Treaty type picker both offer the
   eleven FR-012 values. Insert a twelfth row into `treaty_type_kind` and
   reload: both offer twelve.
5. EDM detail, treaty grid: the column head reads **Cedant**, not "Cedent".
6. FR-011 (export form pre-fill) is verified only after spec 014 is on `main`.

### Story 3 — find EDMs and RDMs by deal

Set up: submission A (Won, owner Cheryl, CRM IDs `T-100`, `T-200`, expiration
next year), submission B (In Process, owner Ben, CRM ID `T-300`), both
attached to EDM X; EDM Y attached to nothing; submission C (Won, CRM ID `T-400`,
no expiration).

1. EDM library, CRM ID chips `T-100`, `T-300`: X listed once; Y not listed.
2. Submission status = Won and Owner = Cheryl: X listed (A satisfies both).
   Submission status = Won and Owner = Ben: X **not** listed (no single
   submission satisfies both).
3. Clear every submission filter: X and Y listed. Set any one: Y disappears.
4. RDM library: the same filters appear and behave the same beside name search
   and import status.
5. Submissions list, tick **In force as of** (today): A listed, B not (In
   Process), C not (no expiration). Change the date to after A's expiration:
   A disappears.
6. Enter twenty-one CRM IDs on any list: the banner reads
   `CRM ID accepts 20 values or fewer.` and no rows render.
7. `/edms/sync`: name search and paging only, unchanged.

### Extract

```sql
SELECT * FROM v_submission_crm_id WHERE submission_id = '<A>';
```

Two rows (`T-100`, `T-200`) with effective dates, both statuses and the
client; a submission with no CRM ID returns one row with `crm_id` NULL.

## 4. Docs check

`docs/FUNCTIONAL_REQUIREMENTS.md`: line 62 names two statuses and no Hold;
line 72's parked CRM item lists expiration date and Submission status with
their two consumers; line 114 lists the new filters and the twenty cap; line
117 marks global search Implemented with six groups; line 118 no longer says
only the submissions list has search or filter.
