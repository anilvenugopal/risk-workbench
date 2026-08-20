# Quickstart: Verifying Analysis Execution (spec 010)

Phased per P-09 — each phase verifies on its own. Prerequisites for the manual paths:
the Docker stack up (developer-started), an imported EDM with ≥2 portfolios and ≥1
treaty, IRP metadata synced, and the spec-009 suites/templates seeded.

## Automated tiers

| Tier | Command | Covers |
|---|---|---|
| Unit | `uv run pytest tests/unit` | Naming/truncation/suffix, plan composition + immutability, gate, worker bodies against FakeIRP, poller handler, retry batch, read models |
| SQL Server | `make test-sql` (or `make wsl-test-sql`) | Reshaped `irp_analysis` constraints (origin CHECK, filtered uniques), new `irp_job` columns, `analysis_result_meta`, schema-drift guard |
| IRP sandbox | `make shell` → `uv run pytest tests/irp --run-irp` | One real submit → poll → backfill → results round-trip |

Never run the SQL Server tier from a host shell; report tiers by name and count, and say
plainly when a tier did not run.

## Phase 1 — Suite execution + tracking (US1, US2)

1. On the EDM detail page, check 2 portfolios → **Execute Suite** enables. Click it.
2. In the modal: search finds a suite; pick one with ~10 templates; expand it and
   deselect 2; pick a treaty; Submit (disabled until a suite was chosen).
3. Modal closes immediately (P-11). The user-executed section fills as submissions land:
   expect `2 × 8 = 16` analyses, each named `portfolio name + template name` (≤64 chars
   sent to RM; hover/expanded shows the full name), each with its own job.
4. Watch statuses move (QUEUED → RUNNING → FINISHED/FAILED) without refreshing — the 3s
   body poll. Confirm the same list appears for a second logged-in analyst.
5. Pick two suites sharing a template → the shared template submits once per portfolio.
6. Peril mismatch: include a template whose peril the EDM lacks → that analysis ends
   FAILED with RM's reason ("no locations match the criteria") shown on its row; siblings
   unaffected.
7. Submission failure: break `RISK_MODELER_BASE_URL` mid-run (or use the sandbox outage
   window) → affected rows show "Failed to submit" immediately, the poller's retry batch
   resubmits with backoff up to `IRP_SUBMISSION_MAX_RETRIES`, then the row stays visible
   as failed-to-submit with its reason.
8. On completion, expand a finished analysis → settings/metadata are shown (backfill).
9. Recovery: kill the Dramatiq worker mid-run; after
   `RWB_HEARTBEAT_STALE_SECS` the reconciler re-pends the job and the run finishes with
   no duplicate analyses for already-submitted items.
10. Rerun the same suite against the same portfolio → new analyses named with a numeric
    suffix inside the 64-char cap; nothing blocked (P-10).

## Phase 2 — Single-template execution (US3)

1. Check 1 portfolio → **Execute Template**; the modal lists templates only (no suites),
   same search; pick 2; Submit disabled until one is chosen.
2. Expect 2 analyses with identical naming/tracking/failure behavior as Phase 1.

## Treaty pass-through (any phase, FR-018)

Click "Add / edit in Risk Modeler ↗" on the treaty section → RM opens in a new window;
edit and save there; return to the workbench window → the treaty section refreshes
(focus-triggered sync) and shows the change. No entry appears in the job monitor.

## Phase 3 — Loss numbers (US4)

1. Run one DLM and one HD analysis to FINISHED. No analyst action: retrieval fires
   automatically after backfill.
2. Expand the finished analysis → loss tabs per perspective (Gross / Ground-Up /
   Reinsurance-Layer where present): AAL, max event loss, ELT record count, standard
   deviation, return-period losses, OEP and AEP. PLT appears for the HD analysis only.
3. A gross-only run (no treaties) shows no Reinsurance-Layer tab and no error.
4. A FAILED analysis shows its reason and no loss data; confirm no
   `analysis_result_meta` row and no Parquet directory was created for it.
5. On disk: `{OUTPUTS_BASE_DIR}/analyses/{analysis_id}/{GR|GU|RL}/*.parquet` matches the
   meta rows' `*_file_path` columns.

## Phase 4 — Job monitor listing (T-12)

Open `/workflows/irp-jobs` → a read-only table lists the run's analysis jobs alongside
other tracked jobs (type, entity/analysis name, status, submitted-by, when, attempts),
newest first, refreshing on the 3s poll. No actions offered.

## Cross-references

- Schema assertions: [data-model.md](data-model.md)
- Worker/poller behavior under test: [contracts/worker-poller.md](contracts/worker-poller.md)
- Route behavior: [contracts/routes.md](contracts/routes.md)
