# Quickstart: One-Click Portfolio Breakouts (Iteration 4)

Validation guide — proves the feature end-to-end. Details live in [data-model.md](data-model.md) and [contracts/](contracts/); implementation steps live in tasks.md.

## Prerequisites

- Dev stack up: `make dev-up` (Docker) **or** `make sqlserver-up` + `make native-dev` (WSL2 native). Poller + Dramatiq worker must be running (the breakout loop is worker-side).
- **DB rebuilt** for the new schema (lineage columns, kind table, seeds): `make db-rebuild` *(destructive — the recommended choice for this schema-affecting iteration)*.
- `irp-integration` **0.3.0** from TestPyPI (`make irp-testpypi`, PR #21: `manage_portfolio_accounts`, paginated selection reads, name/number validation); verify with `make irp-status`. The breakout selection itself runs as DataBridge SQL (`sql/databridge/breakout_{lob,state}_accounts.sql`) — R1 as revised 2026-08-05.
- Sandbox IRP env vars set (for the opt-in tier and manual walkthrough); an imported, `ready` EDM whose source portfolio has a **backfilled exposure summary** with ≥ 2 LOBs and ≥ 2 states (import one and let `backfill_edm_detail` finish, or hit **Sync** on the EDM page).

## Automated validation

```bash
make test                                   # unit tier — gate truth table, plan/naming,
                                            # worker loop (fake IRP), routes, lineage read model
pytest tests/sqlserver --run-sqlserver      # migration: lineage columns + kind table + filtered
                                            # unique index; duplicate generated-portfolio rejection
pytest tests/irp --run-irp                  # opt-in sandbox: select + create + add round-trip
                                            # (codifies the R1 spike: selection vocabulary, already-member
                                            # semantics, state vocab, bucketing, the 40/20 limits)
```

Expected: all green; the unit tier runs with no external deps (SQLite + fake IRP).

## Manual end-to-end walkthrough

1. **Open the EDM** (EDM Library → the prepared EDM). The portfolio table shows the source portfolio with populated figures; each row has a **Break out** action (EDM `ready`).
2. **LOB breakout (US1)**: Break out → *By line of business*. The modal lists every distinct LOB (no free text), the generated name per sub-portfolio (`{source} - {LOB}`), the count, and the overlap + blank-value disclosures. Confirm → toast "Breakout started"; within a few poll cycles the new sub-portfolio rows appear (figures pending), then fill in once the auto-fired backfill completes — no Sync click. Verify in Risk Modeler: one portfolio per LOB, names matching.
   - **Check the two disclosure numbers against the EDM** (FR-007, revised 2026-08-05). The repeat count must equal the accounts carrying more than one value, and the shortfall must equal the accounts carrying none — neither is `Σ per-value counts − account_total`, which reports a clean partition when the two errors cancel. Against the EDM database:
     ```sql
     -- accounts carrying more than one LOB, and accounts carrying at least one
     SELECT SUM(CASE WHEN n > 1 THEN 1 ELSE 0 END) AS multi_value, COUNT(*) AS covered
     FROM (SELECT pa.ACCGRPID, COUNT(DISTINCT l.LOBNAME) AS n
           FROM dbo.portacct pa
           JOIN dbo.policy p ON p.ACCGRPID = pa.ACCGRPID
           JOIN dbo.lobdet l ON l.LOBDETID = p.LOBDETID
           WHERE pa.PORTINFOID = <source> AND NULLIF(LTRIM(RTRIM(l.LOBNAME)),'') IS NOT NULL
           GROUP BY pa.ACCGRPID) x;
     -- the shortfall the modal states = this portfolio's account total − covered
     SELECT COUNT(DISTINCT ACCGRPID) FROM dbo.portacct WHERE PORTINFOID = <source>;
     ```
     A portfolio whose summary predates the revision shows the qualitative wording instead and no numbers — Sync it and re-open the modal.
3. **Gate states**: on a portfolio with no summary → the action is disabled with a Sync pointer; single-LOB portfolio → the LOB option is disabled ("only one value present"); while a breakout runs → "already running"; portfolio changed in RM since the last backfill (edit it in the RM UI, then confirm without Syncing) → confirm refused with "Sync the EDM, then retry" (`stampDate` mismatch) and no job row.
4. **Idempotent re-run (SC-004)**: re-open Break out → the same dimension shows all sub-portfolios "already created"; confirm → job completes with all `skipped_existing`, no duplicates (check the portfolio list and RM). For the stronger variant: kill the worker mid-run, restart, re-request — only missing sub-portfolios are created.
5. **Geography breakout (US2)**: *By geography (state)* on the source portfolio — same flow; the disclosure explicitly warns that multi-state accounts land in full in every matching sub-portfolio. Verify a known multi-state account appears in both its state sub-portfolios in RM.
6. **Lineage (US3)**: generated rows show `↳ from {source} · {dimension label}: {value}`; broker-arrived rows unchanged. Break out a *generated portfolio* (after its summary backfills) → the chained row badges its **immediate source only** (FR-014), never a rendered chain. Check the worker log for the business-event trail (actor, per-sub-portfolio outcomes) and the `rwb_job.output_data` record.
7. **Partial failure**: with the fake/sandbox induced to fail one sub-portfolio (or a forced name conflict), the completion banner reads e.g. "10 created, 1 failed"; created sub-portfolios persist; the failed one's reason stays on the source portfolio's row across a refresh and a navigation away and back (FR-012); re-run completes the missing one.
8. **Two overlapping swaps keep their place** (T-11): with a breakout running so the Portfolios section polls every 3 seconds, expand a portfolio row, scroll down, and open the Break out modal on another row so both requests are in flight. The expanded row stays open and the scroll offset holds through both swaps.
9. **Demo-bug regression (T-16)**: run a breakout, delete its sub-portfolios in the Risk Modeler UI, **Sync** the EDM (rows disappear from the list), re-run the same breakout, and Sync again. Expected: the re-run creates fresh RM portfolios and the second Sync stays healthy — no `uq_irp_portfolio_breakout` violation, every later Sync of the EDM succeeds. In the DB: one `irp_portfolio` row per (source, dimension, value), live, carrying the NEW RM ids.
10. **Custom groups (follow-on FR-018–021)**: Break out → **Custom groups**. Every dimension with ≥ 2 distinct values renders as a pill — peril included, its values reading as mnemonics (`EQ`, `WS`) rather than the stored `loccvg.PERIL` codes (P-30). Build a cart of 3 groups, one selecting values across two dimensions **including peril** (e.g. `state: FL, GA` + `peril: WS`); tick values in one dimension, switch pills, and confirm ticked state survives. Each cart row shows the name — exactly what was typed, no source-name prefix (P-24) — its filters, and "up to N accounts"; two groups sharing a value carry a may-overlap note. Type the name of an existing portfolio in the EDM → the red "Name taken" line appears under the input, and Add is refused (P-25); in Risk Modeler each created portfolio's number is the name inside 20 characters — verbatim when it fits, else hash-tailed (P-26). Confirm → toast "Breakout started — 3 groups"; the source row shows "custom groups — k of 3 done"; **one `rwb_job` per group** (`run_breakout_custom`, requestor `breakout_group`); each generated row badges `↳ from {source} · group: {label}` with the filters in the tooltip; the completion banner aggregates the cart. In Risk Modeler each portfolio holds exactly the accounts matching **every** dimension of its group, and the description lists the full filter set.
11. **Peril breakout (P-19 rev., note 12 D3)**: on a mixed-peril portfolio, Break out → *By peril* — the chooser tile reads "N perils present" and each preview row shows the code with its mnemonic beside it, naming the sub-portfolio `{source} - WS`. Confirm → one `run_breakout_peril` job; in Risk Modeler each portfolio holds the accounts whose `loccvg` rows carry that peril, its number is `P{source rm id}-P-{code}`, and its description reads `… by Peril: 2 (WS)`. An account carrying two perils appears in full in both sub-portfolios — the overlap line states how many do.
12. **Group idempotency (P-22)**: re-open Custom groups and add a group with the SAME values under a DIFFERENT name → the cart row shows the existing group's name ("existing group"); confirm → the run skips (`skipped_existing`), no rename, no duplicate. A group whose filters no account satisfies fails with "no account matches every filter" and creates nothing, while the cart's other groups proceed.

## Exit criteria (PRD §21 Iteration 4, as narrowed by this spec)

- One-click LOB breakout produces one sub-portfolio per LOB in a single confirmed action, with every offered value from the stored summary and zero free-text entry (SC-001).
- One-click state breakout ships the same way, the sub-portfolios covering the source with the measured overlap and the measured blank-value shortfall disclosed before confirm — both counted per account and checkable against the EDM (SC-002, step 2).
- A ≤ 15-value breakout is reflected in the portfolio list within 30 s of confirm; a 40+ value fan-out completes with per-sub-portfolio outcomes and is never refused for size; generated portfolios acquire figures without analyst action (SC-003).
- Partial failure leaves app state consistent with Risk Modeler, and re-running completes the missing sub-portfolios without duplicating existing ones (SC-004).
- The gate enables/disables from entity state alone, and the confirm additionally refuses a rewritten summary (FR-002b) and a stale `stampDate` (FR-002a) before any job row exists (SC-005).
