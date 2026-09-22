# Quickstart: Submission Data and Cross-Entity Search (spec 017)

How to verify the feature after the 2026-09-21 amendment (contract grain).
Contracts: [contracts/routes.md](contracts/routes.md); schema:
[data-model.md](data-model.md).

## Prerequisites

- Docker stack up (`make dev-up`) or WSL2 native — **starting the stack is the
  developer's call**; agents report and stop if it is down.
- DB lifecycle: **Rebuild** — `make db-rebuild` (destructive) after the
  migration edit (`contract` table, dropped submission columns, view). Dev
  submissions do not survive.
- `rwb_loss` holding `dbo.Client` (spec 014's `make bootstrap-loss`). Without
  it the client field reads "Client list unavailable" and everything else
  works (FR-009).
- No worker or poller is involved.

## 1. Unit tier (no containers)

```bash
uv run pytest tests/unit
```

2,010 passed on this branch before the amendment. Covers creation with zero,
one and three contracts, the CRM ID rules, the expiration default, treaty
year, contract status in every Modeling status, the two clause groups, in
force per contract, the default order, `v_contract`, the export pre-fill and
the contract routes.

## 2. SQL Server tier

```bash
make test-sql        # Docker
make wsl-test-sql    # WSL2
```

Covers `contract` and its FKs, the three contract statuses, the eleven treaty
codes, the dropped submission columns and index, `v_contract`, and the
`dbo.Client` read (skipped when absent). **Unverified until someone runs it.**

## 3. Click-through

### Story 1 — contracts on a submission

1. **New submission**: enter name and cedant only, save. The page shows the
   deal card with Modeling status Active and an empty contract table with
   **Add contract**. Treaty year is blank.
2. **New submission** again: add three contract rows — `A-1` Per Occurrence
   Cat XOL 2027-01-01, then `A-2` Aggregate XOL, then `A-3` Top & Drop. Rows
   two and three open with 2027-01-01 already in inception and 2027-12-31 in
   expiration; change row three's expiration to 2029-12-31. Set data vintage
   2027-01-15: treaty year reads 2027 before you save. Save: the contract table shows three rows under the
   headers CRM ID · Treaty type · Inception · Expiration · Contract status,
   all In Process.
3. Edit row two's CRM ID to `a-1` and save: refused, the row named.
4. Press the Status pencil, set Modeling status to Completed with a reason.
   The row pencils and Add disappear; each row's Contract status select is
   still live. Set `A-1` to Won and `A-2` to Lost: both save, the Modeling
   history has one entry.
5. Reopen the submission (Active). Press row three's pencil, change its
   expiration, save: only row three changes. Remove row two: it is gone with
   its status.
6. **Submissions list**: the row for this deal reads CRM ID "A-1 +1 more",
   treaty types "Per Occurrence Cat XOL +1 more", inception 2027-01-01, and
   no contract status column. The list is ordered by inception descending; the
   contract-less submission from step 1 sits among today's deals. The
   **Modeling status** picker offers Active, Completed, Cancelled; the
   **Contract status** picker offers Won, Lost, In Process; no Hold anywhere.

### Story 2 — client, data vintage, treaty type, export pre-fill

1. **New submission**: type `27` under **Client ID** — the menu offers
   `27 - Travelers Corporate Cat`; type `Trav` — same row. Enter data vintage
   2026-06-30 and one contract `T-100` incepting 2027-01-01. Save. The card
   shows Client ID 27 - Travelers Corporate Cat and Data vintage 2026-06-30;
   the free-text field is labelled Cedant.
2. Create another submission leaving Client ID and data vintage blank. It
   saves.
3. Stop `rwb_loss` (or point `MSSQL_LOSS_DATABASE` at a missing database) and
   reload the form: the Client ID field says the list is unavailable; saving
   without one still works.
4. A contract row's treaty type menu and the lists' Treaty type picker both
   offer the eleven FR-012 values. Insert a twelfth row into
   `treaty_type_kind` and reload: both offer twelve.
5. From the step-1 submission open **Export** on a finished analysis: Client
   reads 27, Data vintage 2026-06-30, the Contract select reads `T-100`, CRM
   ID `T-100`, treaty inception 2027-01-01. Change the inception and export:
   the export row records the changed date; the submission is unchanged. On
   a two-contract submission the Contract select opens blank; choosing one
   fills the two fields.
6. EDM detail, treaty grid: the column head reads **Cedant**.

### Story 3 — find EDMs and RDMs by deal

Set up: submission A (owner Cheryl) with contracts `T-100` Won and `T-200`
Lost, both Per Risk XOL 2026-01-01 to 2026-12-31, plus `T-300` Aggregate XOL
Won 2026-01-01 to 2026-12-31; submission B (owner Ben) with `T-400` In
Process; both attached to EDM X; EDM Y attached to nothing; submission C with
no contract.

1. EDM library, CRM ID chips `T-200`, `T-400`: X listed once; Y not listed.
2. Contract status = Won and Owner = Cheryl: X listed. Contract status = Won
   and Owner = Ben: X **not** listed.
3. Treaty type = Per Risk XOL and Contract status = Won: A listed (`T-100`).
   Set `T-100` to Lost: A **not** listed, although A still has a Won contract
   and a Per Risk XOL contract (P-18).
4. Clear every submission filter: X and Y listed. Set any one: Y disappears.
5. RDM library: the same filters behave the same beside name search and
   import status.
6. Submissions list, tick **In force as of** 2026-06-01: A listed, B not, C
   not. Set every Won contract of A to Lost: A disappears. Date 2027-06-01:
   A disappears.
7. Enter twenty-one CRM IDs on any list: `CRM ID accepts 20 values or fewer.`
   and no rows.
8. `/edms/sync`: name search and paging only, unchanged.

### Extract

```sql
SELECT * FROM v_contract WHERE submission_id = '<A>';
```

Three rows, one per contract, each carrying its own status and dates with
the submission's name, cedant, client, treaty year, data vintage and
Modeling status. Submission C returns no row.

## 4. Docs check

`docs/FUNCTIONAL_REQUIREMENTS.md` lines 44–62 describe a submission holding
zero or more contracts, name and cedant required at creation, Modeling status
and Contract status, labels Cedant and Client ID; line 115 lists the filters.
`docs/DATA_MODEL.md` §4 shows `contract` and `v_contract`.
`.specify/memory/constitution.md` Article 4 names `contract.contract_status_code`.
