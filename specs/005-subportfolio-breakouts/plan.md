# Implementation Plan: One-Click Portfolio Breakouts by LOB & Geography (Iteration 4)

**Branch**: `005-subportfolio-breakouts` | **Date**: 2026-07-29 | **Revised**: 2026-08-03 | **Spec**: [spec.md](spec.md)

## Plan status

**Ready for tasks:** Yes
**Blocked by:** Nothing. The library prerequisite (T-02) is delivered and the sandbox spike (T-08) is closed — evidence in [probe-findings.md](probe-findings.md). The `irp-integration` changes sit in PR #21 pending review, so this repo builds against `make irp-local` and re-pins to TestPyPI when that publishes.

## Design summary

- Sub-portfolio composition is three calls (T-01): select the source portfolio's matching account ids → `create_portfolio` (HTTP 201) → `manage_portfolio_accounts(accounts_to_add=…)` (HTTP 200). Both writes are synchronous — no `irp_job` rows, no poller, `poll_*_to_completion` nowhere.
- Selection differs per dimension: **LOB** takes one `search_policies_paginated` pass over the source portfolio's accounts and groups client-side on `policy["lob"]["lobName"]`, so the whole fan-out shares one read; **state** takes one `search_locations_paginated` call per value filtered on `admin1Code`. Both scope by listing account ids, because Risk Modeler has no portfolio predicate — so both chunk by **composed filter length** against a named constant (HTTP 431 is a header-size limit, ~4,872 characters measured, and the bearer token shares that budget).
- `GET /edms/{edm_id}/portfolios/{pid}/breakout` (new `app/routers/portfolios.py`, existing nav node `irp.edm_library`) returns the modal fragment: dimension choice, values from the stored summary's `breakout_values[dimension]`, the generated name and account count per value, the count, the measured overlap statement (FR-007), the blank-value disclosure, and — above 25 sub-portfolios — the statement that the run takes several minutes and holds the job queue (FR-006c); ineligible dimensions render disabled-with-reason.
- The gate rule (FR-002/003) is one testable function in new `app/services/breakout_service.py`: EDM ready ∧ source portfolio exists ∧ no `backfill_edm_detail` in flight for the EDM ∧ summary carries `breakout_values` ∧ ≥ 2 distinct values for the dimension. A summary predating this iteration has no `breakout_values` and reads as missing — disabled, pointing at Sync (R11). A refresh in flight disables the action because it rewrites the summary the preview reads (P-16).
- The plan builder in `breakout_service` is a pure function of (source portfolio name, source RM id, values, existing portfolio names) producing a **name** (≤ 40, source truncated, value whole, collision-suffixed) and a **number** (≤ 20, `P{rm id}-{S|L}-{token}`, hash-tailed when the token is long) per sub-portfolio — P-11/T-05. The number is the identity; the name is not.
- `POST .../breakout` (CSRF) re-checks the gate, refuses with 409 when the stored summary's `as_of` no longer matches the one the preview carried (FR-002b — a Sync landing mid-preview changes the values the analyst judged from, and the stamp check cannot see it), then reads the source portfolio's current `stampDate` via `search_portfolios` — the flow's only web-layer RM call — and refuses with 409 when it differs from the stamp captured at backfill (FR-002a). No `rwb_job` row on either refusal.
- On pass, the POST composes and writes the **approved plan** into `input_data` (one entry per sub-portfolio: value, label, name, number) and idempotently enqueues one `run_breakout_lob`/`run_breakout_state` `rwb_job`, then returns the EDM body partial; the page's existing 3-second self-poll shows progress. The two checks above hold the summary steady, so the persisted values, labels, counts, and numbers match the preview; only a collision suffix can move, and the number is the identity (P-14).
- The Dramatiq worker (new `app/workers/portfolio_jobs.py`, shared body for both job types) **executes the persisted plan** — it never re-enumerates or recomputes names (constitution Art. 8 / R10). It resolves the account ids for every planned value **once, before the loop** — the one grouped policy pass for LOB, one location read per value for state — then loops per entry: `create_portfolio` → `manage_portfolio_accounts` → upsert the `irp_portfolio` row with lineage → read the portfolio back and compare against the ids sent.
- Per-item try/except: one failure never stops the loop; a zero-account selection fails that sub-portfolio and creates nothing (FR-008); an `IRPAPIError` from the paginated selection read fails that sub-portfolio rather than proceeding on a short id list (W-14); `completed < total` from the add is **not** a failure — it is what a healthy re-run reports (W-9). Per-sub-portfolio outcomes land in `rwb_job.output_data`.
- A duplicate-name failure resolves the existing Risk Modeler portfolio by **`portfolioNumber`**, adopts it (lineage row with that id), and re-runs the add step so an adopted-but-empty portfolio is healed (T-07); more than one hit on the number fails that sub-portfolio instead of guessing. Re-runs skip entries whose lineage row already exists — no rollback anywhere (P-07).
- On completion (including partial success) the worker idempotently enqueues the existing `backfill_edm_detail` job so generated portfolios acquire figures (FR-013).
- `backfill_edm_detail` gains three edits: it captures the portfolio's RM `stampDate` alongside the summary (the FR-002a anchor), the two DataBridge summary scripts return a per-value account count with `Admin1Code` as the state value plus `Admin1Name` as a nullable label, and a new script returns each portfolio's account total. The summary JSON gains `breakout_values` and `account_total`; no new column. Measured cost +1.44s worker-side (R11).
- Schema, all in `0001_initial.py`: `irp_portfolio` += `source_portfolio_id` (self-FK), `breakout_dimension_code` (FK), `breakout_value`; new `breakout_dimension_kind` (seeds `lob`, `state`); `rwb_job_type_kind` seeds `run_breakout_lob`/`run_breakout_state`; filtered unique index (source, dimension, value) — the idempotency key.
- UI per docs/UI_WORKFLOW.md rule 1: a rendered HTML preview of the modal (disabled/empty/partial-failure states included) is approved before the template and route are built; the portfolio-row lineage badge and in-flight/outcome display are derivative edits, no preview.
- The PRD documentation pass ships in this branch (O6-1/O6-2 register, §10A.5, §21 — P-09/R9).

## Material changes

| Area | Change |
|---|---|
| Database | `rwb_workbench` only: 3 lineage columns on `irp_portfolio`, `breakout_dimension_kind` + 2 seeds, 2 `rwb_job_type_kind` seeds, 1 filtered unique index — folded into `0001_initial.py`. EXPOSURE, LOSS, DATABRIDGE untouched. |
| Worker | New `portfolio_jobs.py`: `run_breakout_lob`/`run_breakout_state` actors (shared body) — executes the persisted plan, per-sub-portfolio loop, adopt-by-number, outcomes in `output_data`, completion enqueue of `backfill_edm_detail`. `backfill_edm_detail` additionally captures `stampDate` and the counted summary. Poller untouched (both writes are synchronous — no `irp_job` rows). |
| DataBridge SQL | `portfolio_states.sql` returns `Admin1Code` + nullable `Admin1Name` + account count, grouped and filtered on the code (the `COALESCE` goes); `portfolio_lines_of_business.sql` gains an account count; new `portfolio_account_total.sql`. Read-only, worker-side, via `irp-integration` as before. |
| UI | New `breakout_modal.html` + modal GET / confirm POST in new `routers/portfolios.py`; breakout action + lineage badge on `portfolio_row.html`; in-flight indicator + outcome banner on `edm_detail_body.html`; token-based styles in `details.css`. The `states` column now renders state codes (P-12). No new nav node. |
| Services | New `breakout_service.py` (gate, enumeration, name/number plan builder, plan persistence, enqueue, outcome read model); `irp_gateway.py` gains `select_breakout_accounts` (account ids per value, resolved once per run) and `create_sub_portfolio` (create → add → verify), plus the extended summary builder, all mirrored in the CI fake; `portfolio_service.py` gains the lineage-aware list and insert/adopt helpers. |
| Library | `irp-integration` PR #21: paginated selection reads, `manage_portfolio_accounts`, name/number validation, raising pagination. Delivered — this repo consumes it, no library work in scope here ([contracts/irp-library.md](contracts/irp-library.md) records the consumed contract). |

## High-risk technical decisions

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | Sub-portfolio creation is **select → create → add-by-ids**, the add via `manage_portfolio_accounts` (PATCH, reports `completed`/`total`); no one-shot create-by-filter exists in RM | Approved | [research.md#R1](research.md), W-1 |
| T-02 | The library work is **delivered** (PR #21): paginated selection reads, `manage_portfolio_accounts`, 40/20-character name validation, raising pagination. `add_filtered_accounts` exists but is not used — it returns `{}` and cannot report what it did | Approved | [contracts/irp-library.md](contracts/irp-library.md), W-1 |
| T-03 | The fan-out runs as one `run_breakout_*` `rwb_job`; the confirm POST gates, freshness-checks, persists the plan, enqueues, returns | Approved | [research.md#R2](research.md) |
| T-04 | Lineage is 3 nullable columns on `irp_portfolio` + `breakout_dimension_kind` + a filtered unique idempotency index; no breakout table | Approved | [research.md#R3](research.md), [data-model.md](data-model.md) |
| T-05 | Name **and** number are pure functions of (source name, source RM id, values, existing names); the number is the stable identity because the name depends on collision suffixing — implements P-11 | Approved | [research.md#R4](research.md), W-2/W-13/W-17 |
| T-06 | Gate + enumeration read the stored summary only; confirm verifies `stampDate` equality via `search_portfolios`, the flow's one web-layer RM call | Approved | [research.md#R5](research.md) |
| T-07 | Recovery is idempotent re-run: skip by lineage, adopt by **`portfolioNumber`**, re-run the add unconditionally to heal an empty adoption (re-adding members is safe) | Approved | [research.md#R7](research.md), W-9/W-17 |
| T-08 | Sandbox spike — **closed**. U1 selection endpoints and tokens, U2 add semantics, U4 state vocabulary, U5 account bucketing, U6 pagination all answered, plus the 40-character name limit | Approved | [probe-findings.md](probe-findings.md) |
| T-09 | `queryFilter` one-call populate and an EDM-level deep-filter read — **closed permanently, not deferred**: no filter names a source portfolio, so the one-call form cannot express "the TX accounts of portfolio 1", and `allowDeepFilters=true` returns zero rows with HTTP 200 | Rejected | [research.md#R1](research.md), W-6/W-7 |
| T-10 | The worker executes the plan persisted at confirm; it does not recompute names, because collision suffixing reads portfolio names the run itself changes | Approved | [research.md#R10](research.md) |

---

## Technical Context

**New dependencies**: None. The `irp-integration` changes this feature consumes are delivered in that repo's PR #21 (branch `feature/filtered-subportfolio-creation`), not yet on TestPyPI. Implementation runs against the editable checkout (`make irp-local`); before implement completes the dependency is pinned to the published build (`make irp-testpypi`, confirmed with `make irp-status`). Method signatures were verified against `portfolio.py`/`utils.py` at `a04e3d7` — re-confirm against the active wheel before writing `create_sub_portfolio`, since the wheel is pre-release and moves.

**Databases touched**: WORKBENCH only — 3 columns, 1 kind table, 4 seed rows, 1 filtered index. Schema-affecting: choose **Rebuild / Refresh / Skip** at implement time; recommended **Rebuild** (`make db-rebuild`), consistent with the single-revision strategy. EXPOSURE and LOSS untouched. DATABRIDGE is read only by the existing `backfill_edm_detail` worker through `irp-integration`, whose three summary scripts this iteration edits — no DDL, no migration, no request-path read.

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design, re-checked 2026-08-03 after the probe run.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: **no violations**. One was found and fixed during the 2026-08-03 re-check — the pre-probe plan had the worker recompute the confirmed plan from the stored summary, which Article 8 forbids because collision suffixing reads portfolio names that are not part of the stored summary and that the run itself changes. The confirmed plan is now persisted in `input_data` and executed (T-10/R10).

Material interactions — where an article actively shapes this design:

- **Article 2 (Sequencing Is Derived, Not Stored)**: the prerequisite gate is computed in code in one function; the confirm-time `stampDate` read via `search_portfolios` is this article's submit-time name-resolution pattern; the lineage columns record provenance, not a sequence.
- **Article 3 (Kind Tables)**: `breakout_dimension_kind` is a kind table (app-defined closed set the code dispatches on); `breakout_value` stays plain NVARCHAR because it stores external exposure vocabulary verbatim — the spec-004 snapshot rationale.
- **Article 5 (Judgment Waits for a Click)**: nothing is created until the analyst confirms the full previewed list; the post-completion `backfill_edm_detail` enqueue is mechanical follow-up and auto-fires.
- **Article 8 (Approved Plans Are Immutable)**: the confirm POST persists the previewed names and numbers and the worker runs that list. Account ids are resolved at execution time deliberately — they are not what the analyst approved, and the freshness check already refuses a confirm made against drifted data.
- **Article 10 (SQL Table Is the Queue)**: two new job types on the existing `rwb_job` queue with its atomic claim, heartbeat, and reconciler; idempotent enqueue on the `UNIQUE(requestor_type, requestor_id, rwb_job_type)` key — one type per dimension so the key distinguishes them per portfolio. A long fan-out briefly occupying the single worker is accepted at this scale.
- **Article 11 (IRP Behind the Gateway)**: the creation loop runs worker-side through `irp_gateway`; the web layer's single RM call is the confirm-time freshness read (permitted request-path submission pattern, never a `get_*` poll); both writes are synchronous, so there are no `irp_job` rows and the poller is untouched; the DataBridge summary reads stay worker-side; `poll_*_to_completion` appears nowhere.
- **Article 12 (Test-First, Three Tiers)**: the prerequisite gate is the article's named must-test; the tiers are laid out under Testing below.

## Project Structure

```text
app/
├── services/irp_gateway.py       # select_breakout_accounts (ids per value, once per run);
│                                 # create_sub_portfolio (create → add → verify);
│                                 # extended summary builder (breakout_values, account_total);
│                                 # fake mirrors both
├── services/breakout_service.py  # NEW: gate rule, enumeration, name/number plan builder,
│                                 # overlap arithmetic, plan persistence, enqueue, outcome read model
├── services/portfolio_service.py # lineage-aware portfolio list; insert/adopt row helpers
├── workers/portfolio_jobs.py     # NEW: run_breakout_lob / run_breakout_state actors (shared body)
├── workers/…backfill_edm_detail  # captures stampDate; consumes the counted summary scripts
├── routers/portfolios.py         # NEW: breakout modal GET + confirm POST (nav key irp.edm_library)
├── templates/partials/
│   ├── breakout_modal.html       # NEW: dimension choice, preview list, disclosures, confirm
│   ├── portfolio_row.html        # breakout action; lineage badge; states column renders codes
│   └── edm_detail_body.html      # in-flight indicator riding the existing self-poll; outcome banner
└── static/css/details.css        # modal + badge styles via tokens

sql/databridge/
├── portfolio_states.sql              # Admin1Code + nullable Admin1Name + account count
├── portfolio_lines_of_business.sql   # + account count
└── portfolio_account_total.sql       # NEW: per-portfolio account total (the overlap denominator)

alembic/versions/0001_initial.py  # lineage columns, breakout_dimension_kind + seeds, job-type seeds, filtered index
infra/scripts/seed_db.py          # idempotent MERGE for the two new seed sets

tests/
├── unit/                         # gate truth table; name/number plan builder; overlap arithmetic;
│                                 # worker loop vs fake; response-shape parsing; routes; list read model
├── sqlserver/                    # migration builds columns/kind table/index; uniqueness under the real driver
└── irp/test_breakout.py          # NEW opt-in: real select → create → add round-trip through the gateway
```

## Testing

- **Unit** (SQLite via `register_engine`, fake IRP extended with `select_breakout_accounts` and `create_sub_portfolio` incl. duplicate-name behavior): the prerequisite gate truth table (Article 12 must-test), including a pre-`breakout_values` summary reading as missing; the name/number plan builder (40/20-character budgets, source truncation, value truncation at the floor, collision suffixing, hash-tailed tokens, determinism); the overlap arithmetic (clean partition, heavy overlap, missing `account_total`); the worker body (executes the persisted plan rather than recomputing, per-item isolation, partial failure → outcomes in `output_data`, `completed 0` on re-run is success, `IRPAPIError` on the selection read fails one entry, idempotent re-run creates only missing rows, adopt-by-number incl. the multi-hit refusal, completion enqueue of `backfill_edm_detail`); the selection-read response parsing against recorded bodies (every wrong-key mistake in the probe run was silent — W-15); the routes (disabled-with-reason states, CSRF, 409 gate/freshness refusal, plan persisted at enqueue, enqueue idempotency); the lineage-aware list read model.
- **SQL Server integration**: the migration builds the lineage columns, kind table, and filtered unique index; row upsert + lineage uniqueness under the real driver.
- **IRP sandbox** (opt-in): the real select → create → add round-trip through the gateway, re-verifying in this app's tier what the probe run established (selection tokens, chunking, idempotent re-add, name/number limits, account bucketing); the architecture guard extended over the new worker/gateway code (`poll_*_to_completion` absent).
