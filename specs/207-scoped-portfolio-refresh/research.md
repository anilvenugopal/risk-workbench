# Research: Scoped Portfolio Refresh

Evidence for the T-nn decisions in [plan.md](plan.md). All code references are on
`main` at `60bba6c`.

## R1 — A separate job type (T-01) {#r1}

**Decision**: add `rwb_job_type` `refresh_portfolios`.

**Rationale**: these reads key on `rwb_job_type = 'backfill_edm_detail'`:
- the EDM's "syncing" state (`edm_service.latest_backfill_status`);
- the Sync button's skip (`edm_service.sync_detail`);
- the breakout gate's EDM-wide refusal (`breakout_service._backfill_in_flight`, spec 005 P-16);
- the banner (`breakout_service._follow_up_pending`).

With a separate type, a scoped refresh falls out of the first three by type,
which P-04 and P-05 require. The banner then switches to the new type.

**Alternative rejected**: a `portfolio_ids` key in `backfill_edm_detail`'s
`input_data`. Every reader above would have to parse `input_data` JSON in SQL to
tell a full sync from a scoped one.

## R2 — Input carries Risk Modeler ids (T-02) {#r2}

**Decision**: `portfolio_irp_ids` holds `irp_portfolio.irp_id` values.

**Rationale**: every successful breakout outcome carries the Risk Modeler id. The
`_outcome` calls in `app/workers/portfolio_jobs.py` pass `irp_id` for
`skipped_existing` (line 96), `created` (line 137) and `adopted` (line 184).
`summarize_outcomes` writes that id into `output_data.sub_portfolios[].irp_id`.

The outcomes carry no Workbench `irp_portfolio.id`, because generated rows are
inserted by `_INSERT_GENERATED` inside the breakout service. Both downstream
reads match on the Risk Modeler id: `list_portfolios` hits and DataBridge
`PORTINFOID`.

The hazard handler needs one `SELECT irp_id FROM irp_portfolio`.
`_resolve_geohaz_metadata` (`app/poller/run.py:269`) already makes the same read
for the `/metrics` call.

**Alternative rejected**: Workbench ids. The breakout completion would need a
lookup by name for every outcome, and the worker would need a second lookup to
reach Risk Modeler ids.

## R3 — Stamps from one enumeration (T-04) {#r3}

**Decision**: the worker calls `list_portfolios` once and filters the hits to
`portfolio_irp_ids`.

**Rationale**: Risk Modeler's portfolio search filters on `portfolioName` and
`portfolioNumber` only (spec 005 W-17). `fetch_portfolio_stamp`
(`app/services/irp_gateway.py:795`) therefore pages through the whole EDM's
portfolios and matches client-side. A per-portfolio stamp read would repeat that
enumeration once per covered portfolio.

The enumeration returns names and stamps, not figures, so FR-004 holds: the
figures read happens once per covered portfolio. The stamp is the same
`PortfolioHit.stamp` the full sync stores. Reading it before the DataBridge
summary keeps the stored stamp conservative, so FR-002a is unchanged.

**Alternative rejected**: carrying the stamp in `input_data`. The stamp moves
after the action that enqueued the refresh, and a stale stamp would refuse the
next breakout.

## R4 — DataBridge filter (T-05) {#r4}

**Decision**: each of the 11 scripts `get_edm_exposure_summary` runs adds
`({{ portfolio_ids }} IS NULL OR <alias>.PORTINFOID IN (SELECT CAST(value AS
INT) FROM STRING_SPLIT({{ portfolio_ids }}, CHAR(31))))` at its `portacct` read
(`portinfo` in `portfolio_list.sql`, the `acct` CTE in
`portfolio_currencies.sql`). `get_edm_exposure_summary` passes the ids
CHAR(31)-joined, or None.

**Evidence**:
- The wheel substitutes parameters as SQL literals, not bound parameters.
  `DataBridgeManager._substitute_named_parameters`
  (`irp_integration/databridge.py:420`) calls `_escape_sql_value`, which renders
  None as `NULL` (line 347).
  - The full sync's scripts therefore read `NULL IS NULL OR ...`, which SQL
    Server folds at compile time. The full sync's plan and output are unchanged.
  - There is no cached plan shared between the NULL and non-NULL forms, so
    parameter sniffing does not apply.
- `breakout_match_count.sql` already uses the `{{ x }} IS NULL OR ... STRING_SPLIT(..., CHAR(31))` form in production.
- A placeholder with no matching key raises `IRPDataBridgeQueryError`
  ("Missing required parameter"). Both callers must therefore pass
  `portfolio_ids`, including the full sync with None.

- The EDM's indexes (`sys.indexes` on an EDM, 2026-10-07) cover every join the
  scripts make from the covered portfolios:
  - `portacct`: `UIX_PORTACCT (PORTINFOID, ACCGRPID)`, unique. The filter seeks
    it and reads `ACCGRPID` from the index.
  - by `ACCGRPID`: `Property.IX_Property_ACCGRPID`, `policy.IDX_FK_POLICY`.
  - by `LOCID`: `IDX_FK_*` on `loccvg`, `eqdet`, `hudet`, `fldet`, `frdet`,
    `todet`, `trdet`.
  - by `POLICYID`: `polcvg.IDX_FK_POLCVG`.
  - by primary key: `Address.PK_Address`, `lobdet.PK_LOBDET`.

**Risk**: `hdsteppolicy` has no index on `ACCGRPID`, only `PK_HDSTEPPOLICY` on
`STEPPOLICYID`. The step-policy leg of `portfolio_currencies.sql` reads the whole
table in both the full sync and a scoped refresh. The table holds step-function
policies only.

**Observed** ([quickstart.md](quickstart.md) §4, 2026-10-08, the largest EDM
available: 41 portfolios, 2,982,229 portfolio–account memberships): the full read
took 62.3 s and the read of 3 portfolios 20.3 s. The scoped result equalled the
full result's three entries. A scoped read keeps a floor of about 20 s that does
not shrink with the portfolio count. The `hdsteppolicy` scan and the 11 DataBridge
round-trips are the likely causes; neither was timed alone. A hazard lookup's
refresh therefore refuses that portfolio's breakout for at least that long.

**Alternative rejected**: per-portfolio scripts with `{{ portfolio_id }}`. With
`N` covered portfolios, they make 11 × `N` ODBC round-trips instead of 11.

## R5 — Gate keying and the narrowed predicate (T-06, T-07) {#r5}

**Finding**: `backfill_edm_detail_rows` (`app/services/rwb_job_service.py:553`)
joins `irp_job` on any `irp_job` requestor with `ij.irp_edm_id`. The hazard
lookup's `backfill_edm_detail` is keyed on the geohaz `irp_job`, which carries
`irp_edm_id`. A hazard follow-up therefore matched the EDM-wide P-16 refusal and
blocked every portfolio in the EDM, which is the behavior issue #207 reports.

**Decision**:
- After the change, `backfill_edm_detail` has two requestors left: the
  `import_edm` irp_job and `analyst_request`. The `rwb_job`, `breakout_group`
  and `irp_portfolio` joins go.
- The hazard refresh records the portfolio as its context
  (`context_type='portfolio'`, a seeded `rwb_job_context_type_kind` code). The
  gate reads that context with one `rwb_job` lookup, narrowed by
  `ix_rwb_job_status_code` to pending and running rows. No new index is needed.
- The breakout follow-up records the source portfolio as its context, so the
  same lookup refuses the source while the follow-up is pending or running
  (P-04).

**Rejected — breakout follow-up keyed `context_type='edm'`**: the PR #216
review found a gap. `ensure_pending_rwb_job` keys the follow-up on the breakout
job row and skips that row while it is pending or running. A re-run of the same
breakout confirmed during that window enqueues nothing, so the re-run's new
portfolios never refresh. Keying on the source portfolio makes the gate refuse
the re-run until the follow-up is terminal. The breakout confirm runs the gate
before it revives the breakout job, and the follow-up is enqueued before the
breakout job goes terminal, so no re-run fits between them.

**Rejected — a failed summary read stores `summary: null`**: the full sync's
rule. For a scoped refresh it replaced a portfolio's stored summary with null
and stamped a fresh `stamp_date`, so the breakout gate refused the portfolio as
having no summary and the confirm's freshness check passed on figures that
lacked one. P-06 keeps prior figures on a failed refresh, so the job fails
before any write.

**Modal**: `breakout_modal.html` shows "when the sync finishes the page updates
on its own" whenever `gate.refresh_in_flight` is true. That text is wrong for a
portfolio refresh. The gate therefore sets `reason` and leaves
`refresh_in_flight` false. The modal then shows its existing "Breakout is not
available right now: {reason}." note.

## Clarifications

### Session 2026-10-07

- Q: Can a scoped summary read only the covered portfolios' rows, or must it read the whole EDM, which would make FR-004's cost claim unreachable? → A: The EDM indexes every join column the 11 scripts use except `hdsteppolicy.ACCGRPID` ([R4](#r4)). FR-004 stands. The step-policy leg of `portfolio_currencies.sql` reads all of `hdsteppolicy`, as the full sync does today.
