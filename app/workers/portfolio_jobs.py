"""Dramatiq actors for the portfolio breakout fan-out (spec 005 — Article 10,
contracts/worker-poller.md).

``run_breakout_lob`` / ``run_breakout_state`` / ``run_breakout_country`` /
``run_breakout_peril`` share ``_run_breakout_body``
(actor name == ``rwb_job_type``, the loader convention);
``run_breakout_custom`` runs ``_run_breakout_group_body`` — one job per
custom group (T-13), same entry machinery. The worker **executes the plan
persisted at confirm** (AGENTS.md rule 8 / R10 / T-10): it never re-enumerates
values, re-reads the summary, or recomposes names — collision suffixing reads
portfolio names this run itself changes. Account ids ARE resolved at execution
time (they are not what the analyst approved), once, before the loop.

Per-entry isolation throughout: one failure never stops the loop — the guard
sits around the whole entry in ``_run_breakout_body``, so the lineage write is
inside it too, not only the Risk Modeler calls. A zero-account selection fails
that sub-portfolio with NO create call (FR-008); a duplicate-name create adopts
by ``portfolioNumber`` — exactly one hit — and re-runs the add to heal an empty
adoption (R7). RM call first, row second: a crash between them is healed by the
idempotent re-run, never the reverse order (a row without an RM portfolio would
lie). No rollback anywhere (P-07).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.services import (
    breakout_service,
    edm_service,
    irp_gateway,
    portfolio_service,
    rwb_job_service,
)
from app.services._common import _uid
from app.workers import broker, dispatch, runtime
from app.workers.queues import rwb_actor
from db import execute_one

logger = logging.getLogger(__name__)

# Importing this module registers the actors against the broker configured in
# app.workers.broker (module import side effect — no Redis connection yet).
_ = broker.redis_broker


def _dimension_label(dimension: str) -> str:
    row = execute_one(
        "SELECT label FROM breakout_dimension_kind WHERE code = :c",
        {"c": dimension}, connection="WORKBENCH")
    return str(row["label"]) if row is not None else dimension


def _outcome(entry: breakout_service.SubPortfolioPlan, outcome: str,
             **extra) -> breakout_service.SubPortfolioOutcome:
    return breakout_service.SubPortfolioOutcome(
        value=entry.value, name=entry.name, number=entry.number,
        outcome=outcome, **extra)


def _failed(entry: breakout_service.SubPortfolioPlan, *, source_id: Any,
            actor_id: str | None, error: str,
            ) -> breakout_service.SubPortfolioOutcome:
    """One failed sub-portfolio: the business-event log line and the outcome the
    loop records, together — so every failure path logs identically (FR-015)."""
    logger.warning("breakout sub-portfolio %s failed for portfolio %s: %s "
                   "(analyst %s)", entry.name, source_id, error, actor_id)
    return _outcome(entry, "failed", error=error)


def _execute_entry(entry: breakout_service.SubPortfolioPlan, *,
                   edm, source: dict, dimension: str,
                   description: str, actor_id: str | None,
                   selection: dict[str, list[int]],
                   group_id: str | None = None,
                   ) -> breakout_service.SubPortfolioOutcome:
    """One plan entry, per-item isolated (the ``_backfill_edm_detail_body`` /
    ``_upload_rdm_body`` precedents). Anything this raises is caught by the
    loop's own guard, which is what keeps a failed lineage write from taking the
    whole fan-out down. The custom-group body reuses this with a pre-resolved
    one-key selection, its own description, and the ``breakout_group`` row id
    (T-14)."""
    def fail(error: str) -> breakout_service.SubPortfolioOutcome:
        return _failed(entry, source_id=source["id"], actor_id=actor_id,
                       error=error)

    # a. a live lineage row already owns this (source, dimension, value) —
    #    the idempotent re-run skip (FR-011)
    existing = portfolio_service.find_generated(source["id"], dimension,
                                                entry.value)
    if existing is not None:
        logger.info("breakout sub-portfolio %s skipped for portfolio %s — "
                    "already created (analyst %s)", entry.name, source["id"],
                    actor_id)
        return _outcome(entry, "skipped_existing", irp_id=existing["irp_id"])

    # b. an empty selection fails the entry with NO create call — never proceed
    #    on a short id list (W-14); no empty portfolio reaches Risk Modeler
    #    (FR-008)
    account_ids = selection.get(entry.value) or []
    if not account_ids:
        return fail("selection returned zero accounts — the stored summary has "
                    "drifted from Risk Modeler; Sync the EDM and retry")

    # c. create → add → read back
    try:
        created = irp_gateway.create_sub_portfolio(
            edm_name=edm.name, exposure_irp_id=str(edm.irp_id),
            name=entry.name, number=entry.number, description=description,
            account_ids=account_ids)
    except irp_gateway.DuplicatePortfolioNameError:
        duplicate_name = True
    except Exception as exc:  # noqa: BLE001 — per-entry isolation; the loop continues
        return fail(str(exc))
    else:
        duplicate_name = False
    # The adoption branch runs OUTSIDE the except above: an exception raised
    # inside a handler is not caught by that handler's siblings, so calling
    # _adopt_entry from there would leave its own writes unguarded.
    if duplicate_name:
        return _adopt_entry(entry, edm=edm, source=source,
                            dimension=dimension, actor_id=actor_id,
                            account_ids=account_ids, group_id=group_id)

    # d. RM call first, row second — upserted IMMEDIATELY per entry so the
    #    page's self-poll shows generated portfolios as they land
    write = portfolio_service.save_generated_portfolio(
        edm.id, name=entry.name, irp_id=created.portfolio_irp_id,
        source_portfolio_id=source["id"], dimension_code=dimension,
        value=entry.value, actor_id=actor_id, group_id=group_id)
    outcome = "created" if write.created else "skipped_existing"
    logger.info("breakout sub-portfolio %s %s for portfolio %s "
                "(irp_id=%s, accounts=%d, analyst %s)", entry.name, outcome,
                source["id"], created.portfolio_irp_id, created.account_count,
                actor_id)
    return _outcome(entry, outcome, irp_id=created.portfolio_irp_id,
                    accounts=created.account_count)


def _adopt_entry(entry: breakout_service.SubPortfolioPlan, *, edm,
                 source: dict, dimension: str, actor_id: str | None,
                 account_ids: list[int], group_id: str | None = None,
                 ) -> breakout_service.SubPortfolioOutcome:
    """Duplicate-name branch: resolve the existing RM portfolio by its
    generated ``portfolioNumber`` — the identity stable across runs (P-11) —
    adopt it, and re-run the add unconditionally so an adopted-but-empty
    portfolio is healed (R7; re-adding members is safe, W-9). Zero or more
    than one hit fails the entry rather than adopting an arbitrary one
    (FR-011)."""
    def fail(error: str) -> breakout_service.SubPortfolioOutcome:
        return _failed(entry, source_id=source["id"], actor_id=actor_id,
                       error=error)

    try:
        hits = irp_gateway.find_portfolio_by_number(
            exposure_irp_id=str(edm.irp_id), number=entry.number)
    except Exception as exc:  # noqa: BLE001 — per-entry isolation
        return fail(f"adoption lookup failed: {exc}")
    if len(hits) != 1:
        return fail(f"{len(hits)} portfolios carry number {entry.number} — "
                    "cannot adopt" if hits else
                    f"name is taken but no portfolio carries number "
                    f"{entry.number} — cannot adopt")
    hit = hits[0]
    try:
        # RM call first, row second (the create-path ordering note): a crash
        # after the populate but before the row is healed by re-adopting.
        populated = irp_gateway.populate_sub_portfolio(
            edm_name=edm.name, exposure_irp_id=str(edm.irp_id),
            portfolio_irp_id=hit.irp_id, account_ids=account_ids)
    except Exception as exc:  # noqa: BLE001 — per-entry isolation
        return fail(f"populate on adoption failed: {exc}")
    write = portfolio_service.save_generated_portfolio(
        edm.id, name=(hit.name or entry.name), irp_id=hit.irp_id,
        source_portfolio_id=source["id"], dimension_code=dimension,
        value=entry.value, actor_id=actor_id, group_id=group_id)
    # A lost UNIQUE race means a concurrent run already recorded this
    # sub-portfolio — the skip outcome, as on the create path.
    outcome = "adopted" if write.created else "skipped_existing"
    logger.info("breakout sub-portfolio %s %s for portfolio %s "
                "(irp_id=%s, accounts=%d, analyst %s)", entry.name, outcome,
                source["id"], hit.irp_id, populated.account_count, actor_id)
    return _outcome(entry, outcome, irp_id=hit.irp_id,
                    accounts=populated.account_count)


def _load_edm_and_source(ctx: dict) -> tuple[Any, dict | None, str | None]:
    """The step-1 invariants both breakout bodies share: EDM live with its
    exposureId, source portfolio live with its RM id. Returns
    ``(edm, source, error)`` — a set ``error`` fails the job with nothing
    created."""
    edm_id = ctx.get("edm_id")
    edm = edm_service.get_edm(edm_id) if edm_id else None
    if edm is None or edm.irp_id is None:
        return None, None, "EDM missing or has no exposureId — nothing created"
    source = execute_one(
        "SELECT id, name, irp_id, deleted_at FROM irp_portfolio "
        "WHERE id = :p AND edm_id = :e",
        {"p": str(ctx.get("portfolio_id")), "e": str(edm_id)},
        connection="WORKBENCH")
    if source is None or source["deleted_at"] is not None:
        return edm, None, "source portfolio missing or deleted — nothing created"
    if source["irp_id"] is None:
        return edm, None, ("source portfolio has no Risk Modeler id — "
                           "nothing created")
    return edm, dict(source, id=_uid(source["id"])), None


def _complete_breakout(rwb_job_id: Any, *, edm_id: Any, outcomes: list,
                       zero_success_error: str) -> runtime.JobResult:
    """The completion both breakout bodies share: summarize the outcomes and,
    when ≥ 1 entry succeeded, idempotently enqueue+dispatch the FR-013
    follow-up ``backfill_edm_detail``. The head is keyed on THIS breakout job
    row — distinct from the poller's import-keyed enqueue and the analyst
    Sync's EDM-keyed one (``rwb_job_service.backfill_edm_detail_rows`` resolves
    all three keys) — and revives a terminal head so a re-run refreshes figures
    again. Zero successes fail the job with ``zero_success_error``."""
    output = breakout_service.summarize_outcomes(outcomes)
    succeeded = output["created"] + output["adopted"] + output["skipped_existing"]
    output["backfill_enqueued"] = False
    if succeeded:
        backfill_id = rwb_job_service.ensure_pending_rwb_job(
            requestor_type="rwb_job", requestor_id=str(rwb_job_id),
            rwb_job_type="backfill_edm_detail",
            input_data={"edm_id": str(edm_id)})
        if backfill_id is not None:
            dispatch.dispatch(rwb_job_id=backfill_id,
                              rwb_job_type="backfill_edm_detail")
        output["backfill_enqueued"] = True
    if succeeded == 0:
        return runtime.JobResult.fail(zero_success_error, **output)
    return runtime.JobResult(status="succeeded", output=output)


def _run_breakout_body(rwb_job_id: Any) -> runtime.JobResult:
    """Execute the approved plan persisted at confirm (data-model §4). The job
    succeeds when ≥ 1 entry is created/adopted/skipped-existing (partial
    success = success with outcomes — the ``_upload_rdm_body`` semantics) and
    fails only when zero succeeded. On completion including partial success,
    idempotently enqueue ``backfill_edm_detail`` so generated portfolios
    acquire figures without analyst action (FR-013)."""
    ctx = rwb_job_service.load_input_data(rwb_job_id)
    edm_id = ctx.get("edm_id")
    portfolio_id = ctx.get("portfolio_id")
    dimension = ctx.get("dimension")
    actor_id = ctx.get("actor_id")

    edm, source, error = _load_edm_and_source(ctx)
    if error is not None:
        return runtime.JobResult.fail(error)
    if not dimension:
        return runtime.JobResult.fail("the dimension is missing — nothing "
                                      "created")

    try:
        plan = breakout_service.load_approved_plan(ctx)
    except ValueError as exc:
        return runtime.JobResult.fail(f"approved plan unusable: {exc}")
    logger.info("breakout %s executing approved plan for portfolio %s: "
                "%d sub-portfolios (analyst %s)", dimension, portfolio_id,
                len(plan), actor_id)

    logger.info("breakout selection read started for portfolio %s (%d values)",
                portfolio_id, len(plan))
    try:
        selection = irp_gateway.select_breakout_accounts(
            edm_name=edm.name, exposure_irp_id=str(edm.irp_id),
            source_portfolio_irp_id=str(source["irp_id"]),
            dimension=dimension, values=[entry.value for entry in plan])
    except Exception as exc:  # noqa: BLE001 — recoverable job failure, nothing created
        logger.warning("breakout selection failed for portfolio %s: %s",
                       portfolio_id, exc)
        return runtime.JobResult.fail(f"account selection failed: {exc}")
    logger.info("breakout selection resolved %d account ids across %d values "
                "for portfolio %s", sum(len(v) for v in selection.values()),
                len(selection), portfolio_id)

    dimension_label = _dimension_label(dimension)
    outcomes: list[breakout_service.SubPortfolioOutcome] = []
    for entry in plan:
        description = (f"Breakout of portfolio {source['name']} by "
                       f"{dimension_label}: {entry.value}"
                       + (f" ({entry.label})" if entry.label else ""))
        try:
            outcomes.append(_execute_entry(
                entry, edm=edm, source=source, dimension=dimension,
                description=description, actor_id=actor_id,
                selection=selection))
        except Exception as exc:  # noqa: BLE001 — per-entry isolation, last guard
            outcomes.append(_failed(entry, source_id=source["id"],
                                    actor_id=actor_id, error=str(exc)))

    result = _complete_breakout(rwb_job_id, edm_id=edm_id, outcomes=outcomes,
                                zero_success_error="no sub-portfolio succeeded")
    output = result.output
    logger.info("breakout %s completed for portfolio %s by analyst %s: "
                "%d created, %d adopted, %d skipped, %d failed of %d planned",
                dimension, portfolio_id, actor_id, output["created"],
                output["adopted"], output["skipped_existing"],
                output["failed"], output["planned"])
    return result


def _run_breakout_group_body(rwb_job_id: Any) -> runtime.JobResult:
    """Execute one approved custom group (follow-on FR-018–021 / T-14): one
    per-dimension selection read per filter dimension — the same
    probe-verified scripts the quick breakouts use — account ids UNIONed
    within a dimension and INTERSECTed across dimensions (OR within, AND
    across — P-20; whole-account semantics fall out of intersecting
    whole-account selections), then one create-else-adopt through the shared
    entry machinery, with the lineage row carrying dimension ``custom``, the
    group_key as its value, and the ``breakout_group`` row id. An empty
    intersection fails the group with a recorded reason and creates nothing
    (FR-008 semantics); completion enqueues ``backfill_edm_detail`` exactly
    like the quick body (FR-013)."""
    ctx = rwb_job_service.load_input_data(rwb_job_id)
    portfolio_id = ctx.get("portfolio_id")
    actor_id = ctx.get("actor_id")
    edm, source, error = _load_edm_and_source(ctx)
    if error is not None:
        return runtime.JobResult.fail(error)
    try:
        group = breakout_service.load_approved_group(ctx)
    except ValueError as exc:
        return runtime.JobResult.fail(f"approved breakout unusable: {exc}")
    entry = breakout_service.SubPortfolioPlan(
        value=group.key, label=group.label, name=group.name,
        number=group.number, accounts=0, exists=False)
    logger.info("custom-group breakout executing group %s (%s) for portfolio "
                "%s (analyst %s)", group.label, group.key, portfolio_id,
                actor_id)

    ids: set[int] | None = None
    for dim in sorted(group.filters):
        values = group.filters[dim]
        try:
            selection = irp_gateway.select_breakout_accounts(
                edm_name=edm.name, exposure_irp_id=str(edm.irp_id),
                source_portfolio_irp_id=str(source["irp_id"]),
                dimension=dim, values=values)
        except Exception as exc:  # noqa: BLE001 — recoverable job failure, nothing created
            logger.warning("group selection failed for portfolio %s (%s): %s",
                           portfolio_id, dim, exc)
            return runtime.JobResult.fail(
                f"account selection failed for {dim}: {exc}")
        union = set().union(*(selection.get(v) or () for v in values))
        ids = union if ids is None else ids & union

    # The RM description lists the full filter set untruncated (FR-010
    # pattern) — it carries the membership rule the 40-character name cannot.
    shown = {dim: ", ".join(breakout_service.display_value(v, dim)
                            for v in values)
             for dim, values in group.filters.items()}
    description = (f"Custom breakout {group.label} of portfolio "
                   f"{source['name']}: "
                   + " AND ".join(f"{dim} IN ({shown[dim]})"
                                  for dim in sorted(group.filters)))
    if not ids:
        # A legitimate data outcome, not summary drift: the selected values
        # exist, but no single account carries one from EVERY dimension.
        outcomes = [_failed(entry, source_id=source["id"], actor_id=actor_id,
                            error="no account matches every filter of this "
                                  "breakout — nothing was created")]
    else:
        try:
            outcomes = [_execute_entry(
                entry, edm=edm, source=source, dimension="custom",
                description=description, actor_id=actor_id,
                selection={group.key: sorted(ids)}, group_id=group.id)]
        except Exception as exc:  # noqa: BLE001 — the entry guard, as in the quick loop
            outcomes = [_failed(entry, source_id=source["id"],
                                actor_id=actor_id, error=str(exc))]

    result = _complete_breakout(
        rwb_job_id, edm_id=ctx.get("edm_id"), outcomes=outcomes,
        zero_success_error=outcomes[0].error or "the breakout failed")
    logger.info("custom-group breakout completed for portfolio %s by analyst "
                "%s: group %s → %s", portfolio_id, actor_id, group.label,
                outcomes[0].outcome)
    return result


# Dramatiq's default actor time limit is 10 minutes — a large fan-out (the add
# step alone is one PATCH per 1,000 accounts) runs longer. When even this limit
# is exceeded, runtime.run_job marks the row failed so the reconciler cannot
# reset it to pending for a re-run that would die the same way.
_BREAKOUT_TIME_LIMIT_MS = 60 * 60 * 1000


@rwb_actor(max_retries=0, time_limit=_BREAKOUT_TIME_LIMIT_MS)
def run_breakout_lob(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _run_breakout_body(rwb_job_id))


@rwb_actor(max_retries=0, time_limit=_BREAKOUT_TIME_LIMIT_MS)
def run_breakout_state(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _run_breakout_body(rwb_job_id))


@rwb_actor(max_retries=0, time_limit=_BREAKOUT_TIME_LIMIT_MS)
def run_breakout_country(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _run_breakout_body(rwb_job_id))


@rwb_actor(max_retries=0, time_limit=_BREAKOUT_TIME_LIMIT_MS)
def run_breakout_peril(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _run_breakout_body(rwb_job_id))


@rwb_actor(max_retries=0, time_limit=_BREAKOUT_TIME_LIMIT_MS)
def run_breakout_custom(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=runtime.worker_id(),
                    body=lambda: _run_breakout_group_body(rwb_job_id))


# ── synchronous drain (unit tier + simple worker) ────────────────────────────────

_BODIES: dict[str, Callable[[Any], runtime.JobResult]] = {
    actor.actor_name: body for actor, body in (
        (run_breakout_lob, _run_breakout_body),
        (run_breakout_state, _run_breakout_body),
        (run_breakout_country, _run_breakout_body),
        (run_breakout_peril, _run_breakout_body),
        (run_breakout_custom, _run_breakout_group_body),
    )
}


def run_one(*, rwb_job_id: Any, rwb_job_type: str, worker_id: str = "worker") -> bool:
    """Claim + run a single breakout ``rwb_job`` through the shared body.
    Returns ``run_job``'s result (``False`` if already claimed / unknown type)."""
    body = _BODIES.get(rwb_job_type)
    if body is None:
        logger.debug("no body for rwb_job_type %s — skipping", rwb_job_type)
        return False
    return runtime.run_job(rwb_job_id=rwb_job_id, worker_id=worker_id,
                           body=lambda: body(rwb_job_id))


__all__ = ["run_breakout_lob", "run_breakout_state", "run_breakout_country",
           "run_breakout_peril", "run_breakout_custom", "run_one"]
