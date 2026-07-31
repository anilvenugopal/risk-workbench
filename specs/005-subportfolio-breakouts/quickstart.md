# Quickstart: One-Click Portfolio Breakouts (Iteration 4)

Validation guide — proves the feature end-to-end. Details live in [data-model.md](data-model.md) and [contracts/](contracts/); implementation steps live in tasks.md.

## Prerequisites

- Dev stack up: `make dev-up` (Docker) **or** `make sqlserver-up` + `make native-dev` (WSL2 native). Poller + Dramatiq worker must be running (the breakout loop is worker-side).
- **DB rebuilt** for the new schema (lineage columns, kind table, seeds): `make db-rebuild` *(destructive — the recommended choice for this schema-affecting iteration)*.
- `irp-integration` with the R1 enhancements (filtered/paginated `search_accounts_by_portfolio` + `add_filtered_accounts`): `make irp-local` while developing, or the pinned TestPyPI `0.2.2.devN` (`make irp-testpypi`); verify with `make irp-status`.
- Sandbox IRP env vars set (for the opt-in tier and manual walkthrough); an imported, `ready` EDM whose source portfolio has a **backfilled exposure summary** with ≥ 2 LOBs and ≥ 2 states (import one and let `backfill_edm_detail` finish, or hit **Sync** on the EDM page).

## Automated validation

```bash
make test                                   # unit tier — gate truth table, slice plan/naming,
                                            # worker loop (fake IRP), routes, lineage read model
pytest tests/sqlserver --run-sqlserver      # migration: lineage columns + kind table + filtered
                                            # unique index; duplicate-slice rejection
pytest tests/irp --run-irp                  # opt-in sandbox: select + create + add round-trip
                                            # (codifies the R1 spike: selection tokens, already-member
                                            # semantics, state vocab, bucketing, pagination)
```

Expected: all green; the unit tier runs with no external deps (SQLite + fake IRP).

## Manual end-to-end walkthrough

1. **Open the EDM** (EDM Library → the prepared EDM). The portfolio table shows the source portfolio with populated figures; each row has a **Break out** action (EDM `ready`).
2. **LOB breakout (US1)**: Break out → *By line of business*. The modal lists every distinct LOB (no free text), the generated name per slice (`{source} - {LOB}`), the count, and the overlap + blank-value disclosures. Confirm → toast "Breakout started"; within a few poll cycles the new slice rows appear (figures pending), then fill in once the auto-fired backfill completes — no Sync click. Verify in Risk Modeler: one portfolio per LOB, names matching.
3. **Gate states**: on a portfolio with no summary → the action is disabled with a Sync pointer; single-LOB portfolio → the LOB option is disabled ("only one value present"); while a breakout runs → "already running"; portfolio changed in RM since the last backfill (edit it in the RM UI, then confirm without Syncing) → confirm refused with "Sync the EDM, then retry" (`stampDate` mismatch) and no job row.
4. **Idempotent re-run (SC-006)**: re-open Break out → the same dimension shows all slices "already created"; confirm → job completes with all `skipped_existing`, no duplicates (check the portfolio list and RM). For the stronger variant: kill the worker mid-run, restart, re-request — only missing slices are created.
5. **Geography breakout (US2)**: *By geography (state)* on the source portfolio — same flow; the disclosure explicitly warns that multi-state accounts land in full in every matching slice. Verify a known multi-state account appears in both its state slices in RM.
6. **Lineage (US3)**: slice rows show `↳ from {source} · {dimension}: {value}`; broker-arrived rows unchanged. Break out a *slice* (after its summary backfills) → chained lineage renders sanely. Check the worker log for the business-event trail (actor, per-slice outcomes) and the `rwb_job.output_data` record.
7. **Partial failure**: with the fake/sandbox induced to fail one slice (or a forced name conflict), the completion banner reads e.g. "10 created, 1 failed"; created slices persist; re-run completes the missing one.

## Exit criteria (PRD §21 Iteration 4, as narrowed by this spec)

- One-click LOB breakout produces one sub-portfolio per LOB, covering the source (SC-001/SC-004).
- One-click state breakout ships the same way with the overlap disclosure (SC-002).
- Values always come from the stored summary — zero free-text (SC-003).
- Gate enables/disables correctly from entity state alone (SC-007).
- ≤ 15-slice breakout reflected in the list within 30 s (SC-005); re-run idempotent (SC-006); slice figures auto-backfill (SC-008).
