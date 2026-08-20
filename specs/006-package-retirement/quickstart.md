# Verification: Package Retirement

## Preconditions

- The developer has started `linux-box` and SQL Server if connected tiers will run.
- The developer has rebuilt WORKBENCH from the edited single revision after accepting
  the destructive pre-go-live reset.
- The active source is `irp-integration` 0.4.0 from TestPyPI.

## Automated verification

Run the unit tier from the host:

```bash
uv run pytest tests/unit
```

Run the SQL Server tier only through the configured environment:

```bash
make test-sql
```

Run the opt-in IRP tier inside `linux-box` when sandbox credentials are available:

```bash
uv run pytest tests/irp --run-irp
```

Report each tier and pass count separately. SQL Server changes remain unverified until
`make test-sql` runs. Standalone RDM behavior remains unverified against Risk Modeler
until the IRP tier runs with the intended production-version integration package.

## User story 1 - Submission tables

1. Navigate to a submission with no data; confirm specific EDM and RDM empty states.
2. Navigate to a submission with several EDMs/RDMs; confirm both tables remain visible.
3. Sort each table by Name, Status, and count. Confirm sorting one table does not
   change the other table and that polling retains both selected orders.
4. Confirm only ready EDMs/RDMs show Risk Modeler links. Navigate to a ready EDM
   and RDM, and confirm an importing row says the link will be available when ready.
5. Confirm no Package label, card, modal, action, or URL remains.

Stop for approver click-through before user story 2.

## User story 2 - Add and detach

1. Import a new EDM directly from a submission and watch its import/backfill finish.
2. Import a new RDM directly and confirm one standalone Risk Modeler import exists.
3. Relate the EDM and RDM to a second submission without another import.
4. Detach each from the first submission and confirm both remain under the second and
   in their libraries.
5. Submit a stale or duplicate candidate and confirm the write predicate refuses it.
6. Attempt each action on completed and cancelled submissions and confirm rejection.

Stop for approver click-through before user story 3.

## User story 3 - Contextual EDM

1. Give two submissions different RDM sets and relate one EDM to both.
2. Navigate to the EDM from submission A; confirm the context link names A and only A's RDMs appear.
3. Navigate to the same EDM from submission B; confirm the link and RDM set change to B.
4. Switch EDMs with the name selector and confirm the submission URL segment remains fixed.
5. Expand one RDM and confirm only its stored analysis rows load.
6. Navigate to `/edms/{edm_id}` from the library and confirm no submission is selected silently.

Stop for final approver click-through.

## Repository subtraction check

Search live implementation and current execution docs for `package`, `packages`,
`package_id`, and `submission_package`. Remaining matches must be historical evidence,
explicit supersession notes, or the Python packaging term rather than the retired domain.
