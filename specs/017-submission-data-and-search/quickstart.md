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

Covers `contract` and its FKs, `uq_contract_crm_id` (unique, unfiltered, and
case-insensitive under the server's collation), the three contract statuses,
the eleven treaty codes, the dropped submission columns and index,
`v_contract`, the `dbo.Client` read (skipped when absent), and the bulk
update script's four cases (`test_bulk_update_contract_status.py`).
**Unverified until someone runs it.**

## 3. Click-through

### Story 1 — contracts on a submission

1. **New submission**: enter name and cedant only, save. The page shows the
   deal card with Modeling status Active and an empty contract table with
   **Add contract**. Treaty year is blank.
2. **New submission** again: add three contract rows — `A-1` Per Occurrence
   Cat XOL, inception 2027-01-01: expiration fills as 2027-12-31. Then `A-2`
   Aggregate XOL and `A-3` Top & Drop: rows two and three open with both dates
   already filled; change row three's expiration to 2029-12-31. Set data vintage
   2027-01-15: treaty year reads 2027 before you save. Save: the contract table shows three rows under the
   headers CRM ID · Treaty type · Inception · Expiration · Contract status,
   all Open.
3. Edit row two's CRM ID to `a-1` and save: refused, the row named. Open
   the create form again and save a second deal with CRM ID ` a-1 `: refused,
   the row names the first deal and its name opens that deal in a new tab. On
   the second deal's page (create it with `B-1`) add `A-1`: the banner links
   the first deal. Edit the first deal's `A-1` row keeping `A-1`: saves.
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
   contract-less submission from step 1 sits among today's deals. Filter on
   CRM ID `A-3`: the row reads CRM ID "A-3", treaty type "Top & Drop", no
   "+1 more"; filter on Contract status Won instead: "A-1" alone. Clear the
   filters and the row reads "A-1 +1 more" again. The
   **Modeling status** picker offers Active, Completed, Cancelled; the
   **Contract status** picker offers Won, Lost, Open; no Hold anywhere.

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

### Bulk update — Contract status from CIC's CRM table (FR-023)

Set up: Rebuild (it creates `dbo.CRMContractStatus` in `rwb_loss`), then
`make seed-demo` (or `make wsl-seed-demo`). The seeder fills the table with one
row per seeded contract, about a third carrying a status the contract does not,
plus ten CRM IDs the Workbench lacks; its last print line reports the counts.

1. Open `infra/scripts/bulk_update_contract_status.sql` against
   `rwb_workbench` in SSMS or Azure Data Studio and run it as is. The summary
   reads `to_update` about a third of the contracts, `not_in_workbench 10`,
   `dry_run 1`; the change list shows each row with its old and new status;
   nothing is written.
2. Set `@dry_run = 0`, run: `N contract(s) updated.` Each deal's contract
   table shows the new status, and **In force as of** today on the
   submissions list returns the Won deals whose term covers today.
3. Run again: `to_update 0, already_at_status` the full count.
4. Insert `('CRM-BAD-1', 'Bound')` into `rwb_loss.dbo.CRMContractStatus` and
   run: the problem list names the row, `Nothing written`, no contract
   changed. Delete the row afterwards.

In production the one SOURCE line names CIC's loss repository instead of
`rwb_loss`; the login running the script needs SELECT on
`dbo.CRMContractStatus` there.

From the host without SSMS (a dry run unless `@dry_run` is edited):

```bash
docker exec -i infra-sqlserver-1 bash -c '/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -d rwb_workbench -W' < infra/scripts/bulk_update_contract_status.sql
```

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
and Contract status, labels Cedant and Client ID; line 54 reads Implemented
(CRM ID is the only guaranteed-unique attribute); line 115 lists the filters.
`docs/DATA_MODEL.md` §4 shows `contract`, `uq_contract_crm_id` and `v_contract`.
`.specify/memory/constitution.md` Article 4 names `contract.contract_status_code`.
