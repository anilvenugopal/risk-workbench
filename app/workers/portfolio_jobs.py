"""Dramatiq actors for the portfolio breakout fan-out (spec 005 — Article 10,
contracts/worker-poller.md).

``run_breakout_lob`` / ``run_breakout_state`` share ``_run_breakout_body``
(actor name == ``rwb_job_type``, the loader convention). The worker **executes
the plan persisted at confirm** (AGENTS.md rule 8 / R10 / T-10): it never
re-enumerates values, re-reads the summary, or recomputes names — collision
suffixing reads portfolio names this run itself changes. Account ids ARE
resolved at execution time (they are not what the analyst approved), once,
before the loop.

Per-entry isolation throughout: one failure never stops the loop; a
zero-account selection fails that sub-portfolio with NO create call (FR-008);
a duplicate-name create adopts by ``portfolioNumber`` — exactly one hit — and
re-runs the add to heal an empty adoption (R7). RM call first, row second: a
crash between them is healed by the idempotent re-run, never the reverse order
(a row without an RM portfolio would lie). No rollback anywhere (P-07).
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Callable

import dramatiq

from app.services import (
    breakout_service,
    edm_service,
    irp_gateway,
    portfolio_service,
    rwb_job_service,
)
from app.workers import broker, dispatch, runtime
from db import execute_one

logger = logging.getLogger(__name__)

# Importing this module registers the actors against the broker configured in
# app.workers.broker (module import side effect — no Redis connection yet).
_ = broker.redis_broker


def _worker_id() -> str:
    return f"{socket.gethostname()}:{__name__}"


def _load_input(rwb_job_id: Any) -> dict:
    row = execute_one("SELECT input_data FROM rwb_job WHERE id = :id",
                      {"id": str(rwb_job_id)}, connection="WORKBENCH")
    if row is None or not row["input_data"]:
        return {}
    return json.loads(row["input_data"])


def _dimension_label(dimension: str) -> str:
    row = execute_one(
        "SELECT label FROM breakout_dimension_kind WHERE code = :c",
        {"c": dimension}, connection="WORKBENCH")
    return str(row["label"]) if row is not None else dimension


def _execute_entry(entry: breakout_service.SubPortfolioPlan, *,
                   edm, edm_id: str, source: dict, dimension: str,
                   dimension_label: str, actor_id: str | None,
                   selection: irp_gateway.BreakoutSelection,
                   ) -> breakout_service.SubPortfolioOutcome:
    """One plan entry, per-item isolated (the ``_backfill_edm_detail_body`` /
    ``_upload_rdm_body`` precedents)."""
    Outcome = breakout_service.SubPortfolioOutcome

    def result(outcome: str, **extra) -> breakout_service.SubPortfolioOutcome:
        return Outcome(value=entry.value, name=entry.name,
                       number=entry.number, outcome=outcome, **extra)

    # a. a live lineage row already owns this (source, dimension, value) —
    #    the idempotent re-run skip (FR-011)
    existing = portfolio_service.find_generated(source["id"], dimension,
                                                entry.value)
    if existing is not None:
        logger.info("breakout sub-portfolio %s skipped for portfolio %s — "
                    "already created (analyst %s)", entry.name, source["id"],
                    actor_id)
        return result("skipped_existing", irp_id=existing["irp_id"])

    # b. a failed or empty selection fails the entry with NO create call —
    #    never proceed on a short id list (W-14); no empty portfolio reaches
    #    Risk Modeler (FR-008)
    if entry.value in selection.errors_by_value:
        error = f"selection read failed: {selection.errors_by_value[entry.value]}"
        logger.warning("breakout sub-portfolio %s failed for portfolio %s: %s "
                       "(analyst %s)", entry.name, source["id"], error, actor_id)
        return result("failed", error=error)
    account_ids = selection.accounts_by_value.get(entry.value) or []
    if not account_ids:
        error = ("selection returned zero accounts — the stored summary has "
                 "drifted from Risk Modeler; Sync the EDM and retry")
        logger.warning("breakout sub-portfolio %s failed for portfolio %s: %s "
                       "(analyst %s)", entry.name, source["id"], error, actor_id)
        return result("failed", error=error)

    # c. create → add → read back. The description carries the source
    #    portfolio name, dimension, and value IN FULL AND UNTRUNCATED — it is
    #    what carries the lineage the 40-character name loses (FR-010). The
    #    display label rides along when the plan carries one, since the name
    #    is composed from it (P-12 as revised 2026-08-05) and the raw value
    #    must stay searchable in Risk Modeler.
    description = (f"Breakout of portfolio {source['name']} by "
                   f"{dimension_label}: {entry.value}"
                   + (f" ({entry.label})" if entry.label else ""))
    try:
        created = irp_gateway.create_sub_portfolio(
            edm_name=edm.name, exposure_irp_id=str(edm.irp_id),
            name=entry.name, number=entry.number, description=description,
            account_ids=account_ids)
    except irp_gateway.DuplicatePortfolioNameError:
        return _adopt_entry(entry, edm=edm, edm_id=edm_id, source=source,
                            dimension=dimension, actor_id=actor_id,
                            account_ids=account_ids)
    except Exception as exc:  # noqa: BLE001 — per-entry isolation; the loop continues
        logger.warning("breakout sub-portfolio %s failed for portfolio %s: %s "
                       "(analyst %s)", entry.name, source["id"], exc, actor_id)
        return result("failed", error=str(exc))

    # d. RM call first, row second — upserted IMMEDIATELY per entry so the
    #    page's self-poll shows generated portfolios as they land
    write = portfolio_service.insert_generated(
        edm_id, name=entry.name, irp_id=created.portfolio_irp_id,
        source_portfolio_id=source["id"], dimension_code=dimension,
        value=entry.value, actor_id=actor_id)
    outcome = "created" if write.created else "skipped_existing"
    logger.info("breakout sub-portfolio %s %s for portfolio %s "
                "(irp_id=%s, accounts=%d, analyst %s)", entry.name, outcome,
                source["id"], created.portfolio_irp_id, created.account_count,
                actor_id)
    return result(outcome, irp_id=created.portfolio_irp_id,
                  accounts=created.account_count)


def _adopt_entry(entry: breakout_service.SubPortfolioPlan, *, edm, edm_id: str,
                 source: dict, dimension: str, actor_id: str | None,
                 account_ids: list[int],
                 ) -> breakout_service.SubPortfolioOutcome:
    """Duplicate-name branch: resolve the existing RM portfolio by its
    generated ``portfolioNumber`` — the identity stable across runs (P-11) —
    adopt it, and re-run the add unconditionally so an adopted-but-empty
    portfolio is healed (R7; re-adding members is safe, W-9). Zero or more
    than one hit fails the entry rather than adopting an arbitrary one
    (FR-011)."""
    Outcome = breakout_service.SubPortfolioOutcome

    def result(outcome: str, **extra) -> breakout_service.SubPortfolioOutcome:
        return Outcome(value=entry.value, name=entry.name,
                       number=entry.number, outcome=outcome, **extra)

    try:
        hits = irp_gateway.find_portfolio_by_number(
            exposure_irp_id=str(edm.irp_id), number=entry.number)
    except Exception as exc:  # noqa: BLE001 — per-entry isolation
        logger.warning("breakout adoption lookup failed for %s (number %s): %s",
                       entry.name, entry.number, exc)
        return result("failed", error=f"adoption lookup failed: {exc}")
    if len(hits) != 1:
        error = (f"{len(hits)} portfolios carry number {entry.number} — "
                 "cannot adopt" if hits else
                 f"name is taken but no portfolio carries number "
                 f"{entry.number} — cannot adopt")
        logger.warning("breakout sub-portfolio %s failed for portfolio %s: %s "
                       "(analyst %s)", entry.name, source["id"], error, actor_id)
        return result("failed", error=error)
    hit = hits[0]
    try:
        # RM call first, row second (the create-path ordering note): a crash
        # after the populate but before the row is healed by re-adopting.
        populated = irp_gateway.populate_sub_portfolio(
            edm_name=edm.name, exposure_irp_id=str(edm.irp_id),
            portfolio_irp_id=hit.irp_id, account_ids=account_ids)
    except Exception as exc:  # noqa: BLE001 — per-entry isolation
        logger.warning("breakout populate-on-adopt failed for %s (irp_id=%s): %s",
                       entry.name, hit.irp_id, exc)
        return result("failed", error=f"populate on adoption failed: {exc}")
    portfolio_service.adopt_generated(
        edm_id, name=(hit.name or entry.name), irp_id=hit.irp_id,
        source_portfolio_id=source["id"], dimension_code=dimension,
        value=entry.value, actor_id=actor_id)
    logger.info("breakout sub-portfolio %s adopted for portfolio %s "
                "(irp_id=%s, accounts=%d, analyst %s)", entry.name,
                source["id"], hit.irp_id, populated.account_count, actor_id)
    return result("adopted", irp_id=hit.irp_id,
                  accounts=populated.account_count)


def _run_breakout_body(rwb_job_id: Any) -> runtime.JobResult:
    """Execute the approved plan persisted at confirm (data-model §4). The job
    succeeds when ≥ 1 entry is created/adopted/skipped-existing (partial
    success = success with outcomes — the ``_upload_rdm_body`` semantics) and
    fails only when zero succeeded. On completion including partial success,
    idempotently enqueue ``backfill_edm_detail`` so generated portfolios
    acquire figures without analyst action (FR-013)."""
    ctx = _load_input(rwb_job_id)
    edm_id = ctx.get("edm_id")
    portfolio_id = ctx.get("portfolio_id")
    dimension = ctx.get("dimension")
    actor_id = ctx.get("actor_id")

    # 1. minimal invariants — rows live, EDM has its exposureId
    edm = edm_service.get_edm(edm_id) if edm_id else None
    if edm is None or edm.irp_id is None:
        return runtime.JobResult.fail("EDM missing or has no exposureId — "
                                      "nothing created")
    source = execute_one(
        "SELECT id, name, irp_id, deleted_at FROM irp_portfolio "
        "WHERE id = :p AND edm_id = :e",
        {"p": str(portfolio_id), "e": str(edm_id)}, connection="WORKBENCH")
    if source is None or source["deleted_at"] is not None:
        return runtime.JobResult.fail("source portfolio missing or deleted — "
                                      "nothing created")
    if source["irp_id"] is None or not dimension:
        return runtime.JobResult.fail("source portfolio has no Risk Modeler id "
                                      "or the dimension is missing — nothing "
                                      "created")
    source = dict(source, id=str(source["id"]).lower())

    # 2. the approved plan, read verbatim from input_data (T-10)
    try:
        plan = breakout_service.load_approved_plan(ctx)
    except ValueError as exc:
        return runtime.JobResult.fail(f"approved plan unusable: {exc}")
    logger.info("breakout %s executing approved plan for portfolio %s: "
                "%d sub-portfolios (analyst %s)", dimension, portfolio_id,
                len(plan), actor_id)

    # 3. account ids for every planned value, ONCE, before the loop (R1): one
    #    set-based DataBridge query. Its failure is the input to every value →
    #    fail; nothing has been written to Risk Modeler at this point.
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
                "for portfolio %s",
                sum(len(v) for v in selection.accounts_by_value.values()),
                len(selection.accounts_by_value), portfolio_id)

    # 4. the per-entry loop
    dimension_label = _dimension_label(dimension)
    outcomes = [
        _execute_entry(entry, edm=edm, edm_id=str(edm_id), source=source,
                       dimension=dimension, dimension_label=dimension_label,
                       actor_id=actor_id, selection=selection)
        for entry in plan
    ]

    # 5–7. outcomes + completion enqueue + terminal status
    output = breakout_service.summarize_outcomes(outcomes)
    succeeded = output["created"] + output["adopted"] + output["skipped_existing"]
    output["backfill_enqueued"] = False
    if succeeded:
        # Keyed on THIS breakout job row — distinct from the poller's
        # import-keyed enqueue and the analyst Sync's EDM-keyed one; revives a
        # terminal head so a re-run refreshes figures again (FR-013).
        backfill_id = rwb_job_service.ensure_pending_rwb_job(
            requestor_type="rwb_job", requestor_id=str(rwb_job_id),
            rwb_job_type="backfill_edm_detail",
            input_data={"edm_id": str(edm_id)})
        if backfill_id is not None:
            dispatch.dispatch(rwb_job_id=backfill_id,
                              rwb_job_type="backfill_edm_detail")
        output["backfill_enqueued"] = True
    logger.info("breakout %s completed for portfolio %s by analyst %s: "
                "%d created, %d adopted, %d skipped, %d failed of %d planned",
                dimension, portfolio_id, actor_id, output["created"],
                output["adopted"], output["skipped_existing"],
                output["failed"], output["planned"])
    if succeeded == 0:
        return runtime.JobResult.fail("no sub-portfolio succeeded", **output)
    return runtime.JobResult(status="succeeded", output=output)


# Dramatiq's default actor time limit is 10 minutes — a large fan-out (the add
# step alone is one PATCH per 1,000 accounts) runs longer. When even this limit
# is exceeded, runtime.run_job marks the row failed so the reconciler cannot
# reset it to pending for a re-run that would die the same way.
_BREAKOUT_TIME_LIMIT_MS = 60 * 60 * 1000


@dramatiq.actor(max_retries=0, time_limit=_BREAKOUT_TIME_LIMIT_MS)
def run_breakout_lob(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _run_breakout_body(rwb_job_id))


@dramatiq.actor(max_retries=0, time_limit=_BREAKOUT_TIME_LIMIT_MS)
def run_breakout_state(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _run_breakout_body(rwb_job_id))


# ── synchronous drain (unit tier + simple worker) ────────────────────────────────

_BODIES: dict[str, Callable[[Any], runtime.JobResult]] = {
    "run_breakout_lob": _run_breakout_body,
    "run_breakout_state": _run_breakout_body,
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


__all__ = ["run_breakout_lob", "run_breakout_state", "run_one"]
