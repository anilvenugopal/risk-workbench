"""Dramatiq actors for entity jobs (Article 10 / worker-poller.md §2).

Each ``rwb_job_type`` has a **body** (the real Risk Modeler work, via ``irp_gateway``)
wrapped by ``runtime.run_job`` — atomic claim → heartbeat → complete. The unit of
work is always the *submit* (or the synchronous RDM delete), never the remote finish;
the poller bridges the async boundary and drives chaining.

Two entrypoints share the bodies:
  • the ``@dramatiq.actor`` wrappers — the real async delivery path;
  • ``run_pending`` — a synchronous drain of currently-``pending`` rows, used by the
    unit tier (drive the queue without Redis) and usable as a simple polling worker.

Every body is **idempotent** for the common re-run: a reconciler re-dispatch or
Dramatiq redelivery must not double-submit. Guards key off entity status
(``pending_import`` gates a submit) and the idempotent ``enqueue_rwb_job`` /
existing-``irp_job`` checks.

**Known at-least-once window (accepted this iteration — review item 4).** The submit
and the ``record_submitted_irp_job`` that flips the guard are two separate writes: a
crash *after* the submit reached Risk Modeler but *before* the record leaves the entity
still ``pending_import`` with no ``irp_job``, so a later retry re-submits and creates a
duplicate resource in RM. Closing it needs a pre-submit guard (a ``SUBMITTING`` state,
or a ``search_*``-by-name pre-check) — a larger change deferred to the US6 reconcile
work. Flagged at each submit→record seam below rather than left silent.
"""

from __future__ import annotations

import json
import logging
import socket
import uuid
from typing import Any, Callable

import dramatiq
from sqlalchemy import text

from app.services import (
    edm_service,
    irp_gateway,
    irp_job_service,
    portfolio_service,
    rdm_service,
    rwb_job_service,
    treaty_service,
)
from app.services._common import _utcnow
from app.workers import broker, dispatch, runtime
from db import (
    execute,
    execute_command,
    execute_one,
    execute_scalar,
    get_connection,
    is_unique_violation,
)

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


# ── upload_edm (US1) ────────────────────────────────────────────────────────────

def _upload_edm_body(rwb_job_id: Any) -> runtime.JobResult:
    """Submit one EDM import and record the ``irp_job`` (the unit of work is the
    submit). Idempotent: only a ``pending_import`` EDM is submitted, so a redelivery
    or reconciler re-run is a no-op (``JobResult.ok``). A submit that never reaches Risk
    Modeler records a ``SUBMISSION FAILED`` ``irp_job`` (for the poller's retry batch),
    flips the EDM to the visible/recoverable ``error`` state, and fails the ``rwb_job``."""
    ctx = _load_input(rwb_job_id)
    edm_id = ctx.get("edm_id")
    submission_id = ctx.get("requested_from_submission_id")
    edm = edm_service.get_edm(edm_id) if edm_id else None
    if edm is None:
        return runtime.JobResult.ok(skipped="edm missing")
    if edm.status != edm_service.PENDING:
        # already submitted/imported/errored — nothing to do this run.
        return runtime.JobResult.ok(skipped=f"edm status {edm.status}")

    try:
        res = irp_gateway.submit_edm_import(
            name=edm.name, source_file_path=edm.source_file_path)
    except Exception as exc:  # noqa: BLE001 — submit never reached RM → SUBMISSION FAILED
        logger.warning("upload_edm submit failed for %s: %s", edm_id, exc)
        irp_job_service.record_submission_failure(
            requested_from_submission_id=submission_id,
            irp_job_type="import_edm", irp_edm_id=edm_id,
            payload={"name": edm.name, "source_file_path": edm.source_file_path})
        edm_service.mark_error(edm_id=edm_id)
        return runtime.JobResult.fail(f"upload_edm submit failed: {exc}",
                                      submit_failed=str(exc))

    # AT-LEAST-ONCE WINDOW (see module docstring): the submit above already reached RM.
    # A crash before this record + mark_importing complete leaves the EDM pending_import
    # with no irp_job → a retry re-submits (duplicate exposure). Accepted this iteration.
    irp_job_id = irp_job_service.record_submitted_irp_job(
        requested_from_submission_id=submission_id,
        irp_job_type="import_edm", irp_edm_id=edm_id,
        irp_id=res.irp_id, resource_uri=res.resource_uri,
        payload=res.payload, response=res.response)
    edm_service.mark_importing(edm_id=edm_id)
    logger.info("import_edm submitted for edm=%s (irp_id=%s)", edm_id, res.irp_id)
    return runtime.JobResult.ok(irp_job_id=irp_job_id, irp_id=res.irp_id)


@dramatiq.actor(max_retries=0)
def upload_edm(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _upload_edm_body(rwb_job_id))


# ── upload_rdm (US2) ─────────────────────────────────────────────────────────────

def _rdm_import_exists(rdm_id: Any) -> bool:
    n = execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm' "
        "AND irp_rdm_id=:r AND status<>'SUBMISSION FAILED'",
        {"r": str(rdm_id)}, connection="WORKBENCH")
    return bool(n)


def _upload_rdm_body(rwb_job_id: Any) -> runtime.JobResult:
    """Submit one standalone RDM import and record its ``irp_job``."""
    ctx = _load_input(rwb_job_id)
    rdm_id = ctx.get("rdm_id")
    submission_id = ctx.get("requested_from_submission_id")
    rdm = rdm_service.get_rdm(rdm_id) if rdm_id else None
    if rdm is None:
        return runtime.JobResult.ok(skipped="rdm missing")
    if rdm.status != rdm_service.PENDING or _rdm_import_exists(rdm_id):
        return runtime.JobResult.ok(skipped=f"rdm status {rdm.status}")
    try:
        res = irp_gateway.submit_rdm_import(
            name=rdm.name, source_file_path=rdm.source_file_path)
    except Exception as exc:  # noqa: BLE001 — SUBMISSION FAILED, not a crash
        logger.warning("upload_rdm submit failed for %s: %s", rdm_id, exc)
        irp_job_service.record_submission_failure(
            requested_from_submission_id=submission_id,
            irp_job_type="import_rdm", irp_rdm_id=rdm_id,
            payload={"name": rdm.name, "source_file_path": rdm.source_file_path})
        rdm_service.mark_error(rdm_id=rdm_id)
        return runtime.JobResult.fail(f"upload_rdm submit failed: {exc}")
    irp_job_id = irp_job_service.record_submitted_irp_job(
        requested_from_submission_id=submission_id,
        irp_job_type="import_rdm", irp_rdm_id=rdm_id, irp_id=res.irp_id,
        resource_uri=res.resource_uri, payload=res.payload, response=res.response)
    rdm_service.mark_importing(rdm_id=rdm_id)
    logger.info("import_rdm submitted for rdm=%s (irp_id=%s)", rdm_id, res.irp_id)
    return runtime.JobResult.ok(irp_job_id=irp_job_id, irp_id=res.irp_id)


@dramatiq.actor(max_retries=0)
def upload_rdm(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _upload_rdm_body(rwb_job_id))


# ── backfill_rdm_analyses (US2, D2) ──────────────────────────────────────────────

_INSERT_ANALYSIS_IF_ABSENT = """
    INSERT INTO irp_analysis (id, rdm_id, edm_id, irp_id, name,
        source_rdm_name, status_code, created_by_irp_job_irp_id, is_group,
        inserted_at, updated_at)
    SELECT :id, :rdm, NULL, :irp, :name, :srdm, 'ready', :cby, 0, :now, :now
    WHERE NOT EXISTS (
        SELECT 1 FROM irp_analysis
        WHERE rdm_id = :rdm AND irp_id = :irp
    )
"""

# The spec-004 detail overwrite (idempotent in place, R2): settings_metadata +
# is_group only when the metadata fetch succeeded (never null a prior good
# snapshot on a failed re-run); the same rule guards the promoted pointer —
# a failed re-read that learned nothing must not null a prior good pointer.
_UPDATE_ANALYSIS_DETAIL = """
    UPDATE irp_analysis
    SET settings_metadata = :sm, is_group = :grp, exposure_resource_id = :x,
        updated_at = :now
    WHERE rdm_id = :rdm AND irp_id = :irp
"""
_UPDATE_ANALYSIS_POINTER = """
    UPDATE irp_analysis
    SET exposure_resource_id = :x, updated_at = :now
    WHERE rdm_id = :rdm AND irp_id = :irp
"""


def _prune_rdm_analyses(conn, *, rdm_id: Any,
                        seen_ids: list[str], now: Any) -> int:
    """Reconcile one RDM's captured rows against a successful
    ``search_analyses`` enumeration: soft-delete rows RM no longer returns
    and clear
    ``deleted_at`` on ids it returns again. Returns the rows pruned."""
    params: dict[str, Any] = {"rdm": str(rdm_id), "now": now}
    params.update({f"a{i}": str(v) for i, v in enumerate(seen_ids)})
    marks = ", ".join(f":a{i}" for i in range(len(seen_ids)))
    if marks:
        conn.execute(text(
            "UPDATE irp_analysis SET deleted_at = NULL, updated_at = :now "
            "WHERE rdm_id = :rdm AND deleted_at IS NOT NULL "
            f"AND irp_id IN ({marks})"), params)
    stale = f" AND irp_id NOT IN ({marks})" if marks else ""
    return conn.execute(text(
        "UPDATE irp_analysis SET deleted_at = :now, updated_at = :now "
        f"WHERE rdm_id = :rdm AND deleted_at IS NULL{stale}"),
        params).rowcount


def _backfill_rdm_analyses_body(rwb_job_id: Any) -> dict:
    """Capture every broker analysis for one RDM.

    The poller enqueues the job after the standalone RDM import finishes. The
    insert is idempotent on ``(rdm_id, irp_id)`` and stores ``edm_id`` as null.

    Spec 004 US3 extension (R3/R9): per captured analysis, also fetch its
    settings/metadata (single-item ``get_analysis_metadata``, looped app-side)
    and store the ``settings_metadata`` snapshot + ``is_group``, promoting RM's
    ``exposureResourceId`` to the typed column ONLY when the resource type is
    PORTFOLIO (null otherwise). One analysis's failed metadata read leaves its
    fields blank and never aborts the capture (blank, not error). No portfolio
    lookup here — resolution is read-time in ``analysis_service``.

    Manual RDM sync uses the same RDM-wide capture."""
    ctx = _load_input(rwb_job_id)
    rdm_id = ctx.get("rdm_id")
    apply_irp_id = ctx.get("apply_irp_id")
    rdm = rdm_service.get_rdm(rdm_id) if rdm_id else None
    if rdm is None:
        return {"skipped": "rdm missing"}

    # Enumerate and fetch every analysis's metadata before opening the
    # transaction (no txn across a gateway round-trip, Article 11); per-analysis
    # isolation. The gateway builds the filter with safe json.dumps quoting —
    # never interpolate names into a filter here.
    try:
        hits = irp_gateway.search_analyses(source_rdm_name=rdm.name)
    except Exception as exc:  # noqa: BLE001 — enumeration failed → recoverable job failure
        logger.warning("backfill_rdm_analyses: analysis enumeration failed for %s: %s",
                       rdm_id, exc)
        return runtime.JobResult.fail(f"analysis enumeration failed: {exc}")
    meta_by_id: dict[str, Any] = {}
    metadata_failures = 0
    for hit in hits:
        try:
            meta_by_id[hit.analysis_id] = irp_gateway.get_analysis_metadata(
                analysis_id=int(hit.analysis_id))
        except Exception as exc:  # noqa: BLE001 — blank, never error (US3 acc. 3)
            logger.warning("backfill_rdm_analyses: metadata read failed "
                           "(analysis=%s): %s", hit.analysis_id, exc)
            metadata_failures += 1

    now = _utcnow()
    pruned = 0
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            pruned += _prune_rdm_analyses(
                conn, rdm_id=rdm_id,
                seen_ids=[hit.analysis_id for hit in hits], now=now)
            for hit in hits:
                # The NOT EXISTS pre-check is not atomic under READ COMMITTED;
                # a concurrent backfill of the same RDM can win the race and
                # leave this insert violating UNIQUE(rdm_id, irp_id).
                # Absorb that in a SAVEPOINT as a dedup hit so the outer txn
                # (and the rollup) survives.
                try:
                    with conn.begin_nested():
                        conn.execute(text(_INSERT_ANALYSIS_IF_ABSENT), {
                            "id": str(uuid.uuid4()),
                            "rdm": str(rdm_id),
                            "irp": str(hit.analysis_id), "name": hit.name,
                            "srdm": rdm.name,
                            "cby": (str(apply_irp_id)
                                    if apply_irp_id is not None else None),
                            "now": now})
                except Exception as exc:  # noqa: BLE001 — UNIQUE race → already captured
                    if not is_unique_violation(exc):
                        raise
                # Detail overwrite (US3): the pointer prefers the per-analysis
                # metadata, falling back to the search hit; promoted ONLY for
                # exposureResourceType == "PORTFOLIO" (R9).
                meta = meta_by_id.get(hit.analysis_id)
                rid, rtype = hit.exposure_resource_id, hit.exposure_resource_type
                if meta is not None and meta.exposure_resource_id is not None:
                    rid, rtype = (meta.exposure_resource_id,
                                  meta.exposure_resource_type)
                pointer = rid if (rid is not None and rtype == "PORTFOLIO") else None
                key = {"rdm": str(rdm_id),
                       "irp": str(hit.analysis_id), "x": pointer, "now": now}
                if meta is not None:
                    conn.execute(text(_UPDATE_ANALYSIS_DETAIL), {
                        **key,
                        "sm": (json.dumps(meta.payload) if meta.payload else None),
                        "grp": (1 if meta.is_group else 0)})
                elif pointer is not None:
                    # metadata read failed — refresh the pointer only when
                    # the search hit actually carried one
                    conn.execute(text(_UPDATE_ANALYSIS_POINTER), key)
            # Mark the RDM ready and stamp its last successful analysis refresh.
            rdm_service.rollup_on_terminal(
                conn, rdm_id=rdm_id, rm_status="FINISHED", irp_id=apply_irp_id)
            conn.execute(text(
                "UPDATE irp_rdm SET as_of = :now, updated_at = :now WHERE id = :id"
            ), {"now": now, "id": str(rdm_id)})
    captured = len(hits)
    logger.info("captured %d analysis row(s) for rdm=%s", captured, rdm_id)
    out: dict[str, Any] = {"captured": captured}
    if pruned:
        out["pruned"] = pruned
    if metadata_failures:
        out["metadata_failures"] = metadata_failures
    return out


@dramatiq.actor(max_retries=0)
def backfill_rdm_analyses(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _backfill_rdm_analyses_body(rwb_job_id))


# ── backfill_edm_detail (spec 004 US1) ───────────────────────────────────────────

def _backfill_edm_detail_body(rwb_job_id: Any) -> runtime.JobResult:
    """Fetch a finished EDM's per-portfolio exposure detail from Risk Modeler and
    idempotently upsert the ``irp_portfolio`` JSON snapshot rows (R2/R3), stamping
    ``as_of``. Enqueued by the poller on ``import_edm`` FINISHED.

    Discipline (contracts/worker-poller.md §1):
      • single-item gateway reads, looped app-side — one portfolio's failed
        exposure read is logged and skipped, the rest still backfill (a partial
        snapshot beats none; an idempotent re-run completes it);
      • each successful enumeration also PRUNES (soft-deletes) rows RM no
        longer returns — a portfolio/treaty deleted in RM stops rendering with
        a fresh-looking ``as_of``; a failed enumeration never prunes;
      • no transaction held across a gateway round-trip — fetch, then persist
        (each upsert runs its own short transaction);
      • an enumeration failure fails the ``rwb_job`` (recoverable via the existing
        retry machinery) but NEVER touches the EDM's ``ready`` status (FR-005);
      • a missing EDM or one with no exposureId is a graceful skip — a
        pre-capability/never-finished EDM stays in the empty state (R7)."""
    ctx = _load_input(rwb_job_id)
    edm_id = ctx.get("edm_id")
    edm = edm_service.get_edm(edm_id) if edm_id else None
    if edm is None:
        return runtime.JobResult.ok(skipped="edm missing")
    edm_irp_id = edm.irp_id
    if edm_irp_id is None:
        # Pre-capability EDMs may lack the exposureId (normally backfilled at
        # import FINISHED). A manual Sync resolves it by name — but stricter
        # than the poller: the poller takes the newest of multiple hits (it
        # KNOWS the just-created one is newest), while here ambiguity means we
        # can't tell which entity is ours, so zero or >1 hits keep the graceful
        # skip (R7).
        try:
            hits = irp_gateway.search_edms(edm.name)
        except Exception as exc:  # noqa: BLE001 — resolution is best-effort
            logger.warning("backfill_edm_detail: exposureId resolution failed "
                           "for %s (%s): %s", edm_id, edm.name, exc)
            hits = []
        if len(hits) != 1:
            return runtime.JobResult.ok(
                skipped="edm has no exposureId — nothing to fetch")
        edm_irp_id = int(hits[0].irp_id)
        execute_command(
            "UPDATE irp_edm SET irp_id = :x, updated_at = :now WHERE id = :id",
            {"x": edm_irp_id, "now": _utcnow(), "id": str(edm_id)},
            connection="WORKBENCH")

    try:
        portfolios = irp_gateway.list_portfolios(edm_irp_id=int(edm_irp_id))
    except Exception as exc:  # noqa: BLE001 — enumeration failed → recoverable job failure
        logger.warning("backfill_edm_detail: portfolio enumeration failed for %s: %s",
                       edm_id, exc)
        return runtime.JobResult.fail(f"portfolio enumeration failed: {exc}")

    # ONE DataBridge aggregate per EDM (geography/LOB/currency — absent
    # from every RM REST read; Addendum A T057). Enrichment only: ANY failure
    # (databaseName resolution / databridge extra / env / SQL) degrades to
    # "summary": null — the metrics half of the snapshot must still land.
    summary_map: dict[str, dict] | None = None
    if portfolios:
        try:
            summary_map = irp_gateway.get_edm_exposure_summary(
                edm_name=edm.name, edm_irp_id=int(edm_irp_id))
        except Exception as exc:  # noqa: BLE001 — enrichment only
            logger.warning("backfill_edm_detail: exposure summary unavailable "
                           "(edm=%s): %s", edm_id, exc)

    now = _utcnow()
    # Reconcile the row set against the successful enumeration BEFORE the
    # overwrites: seen = every enumerated portfolio (a failed exposure read
    # below skips the snapshot, not the row's existence).
    pruned_portfolios = portfolio_service.prune_missing(
        edm_id=edm_id, seen=[(p.irp_id, p.name) for p in portfolios], now=now)
    stored = 0
    exposure_failures = 0
    for p in portfolios:
        try:
            exposure = irp_gateway.get_portfolio_exposure(
                edm_irp_id=int(edm_irp_id), portfolio_irp_id=int(p.irp_id))
        except Exception as exc:  # noqa: BLE001 — per-portfolio isolation
            logger.warning("backfill_edm_detail: exposure read failed "
                           "(edm=%s portfolio=%s): %s", edm_id, p.irp_id, exc)
            exposure_failures += 1
            continue  # skip — never overwrite a prior good snapshot with nothing
        # The aggregate keys on portinfo.PORTINFOID (assumed == RM portfolioId);
        # portfolio_name is the contract's fallback join key if they diverge.
        summary = (summary_map or {}).get(str(p.irp_id))
        if summary is None and summary_map:
            summary = next((s for s in summary_map.values()
                            if s.get("portfolio_name") == p.name), None)
        # Namespaced snapshot (data-model §2): the /metrics payload verbatim under
        # "metrics"; "summary" is the DataBridge aggregate (null when unavailable —
        # never a stale prior, so the row's as_of can't overstate its freshness).
        # "stamp_date" is the portfolio's RM stampDate from the enumeration —
        # read BEFORE the DataBridge summary read, so the stored stamp is
        # conservative — the FR-002a freshness anchor the breakout confirm
        # compares against (spec 005).
        portfolio_service.upsert_portfolio_detail(
            edm_id=edm_id, irp_id=p.irp_id, name=p.name,
            exposure_detail={"metrics": exposure.payload, "summary": summary,
                             "stamp_date": p.stamp},
            as_of=now)
        stored += 1

    if portfolios and exposure_failures and stored == 0:
        return runtime.JobResult.fail(
            f"backfill_edm_detail stored nothing ({exposure_failures} exposure "
            "reads failed)", portfolios=0, exposure_failures=exposure_failures)

    # Treaties ride the same job (US2): one enumeration whose rows ARE the full
    # attribute maps (no per-treaty round-trip), upserted idempotently. An
    # enumeration failure fails the rwb_job (recoverable) AFTER the portfolio
    # snapshots landed — a re-run overwrites both halves in place; the EDM's
    # ready status and its already-written portfolio detail stay (FR-005).
    try:
        treaties = irp_gateway.search_treaties(edm_irp_id=int(edm_irp_id))
    except Exception as exc:  # noqa: BLE001 — recoverable job failure
        logger.warning("backfill_edm_detail: treaty enumeration failed for %s: %s",
                       edm_id, exc)
        return runtime.JobResult.fail(
            f"treaty enumeration failed: {exc}",
            portfolios=stored, exposure_failures=exposure_failures)
    pruned_treaties = treaty_service.prune_missing(
        edm_id=edm_id, seen=[(t.irp_id, t.name) for t in treaties], now=now)
    for t in treaties:
        treaty_service.upsert_treaty_detail(
            edm_id=edm_id, irp_id=t.irp_id, name=t.name,
            attributes=t.attributes, as_of=now)

    # Stamp the EDM-level last-synced trust signal (FR-052) — the header's
    # "synced <ts>"; per-portfolio/per-treaty truth is each row's own as_of.
    execute_command(
        "UPDATE irp_edm SET as_of = :now, updated_at = :now WHERE id = :id",
        {"now": now, "id": str(edm_id)}, connection="WORKBENCH")
    out: dict[str, Any] = {"portfolios": stored, "treaties": len(treaties),
                           "exposure_failures": exposure_failures}
    if pruned_portfolios:
        out["pruned_portfolios"] = pruned_portfolios
    if pruned_treaties:
        out["pruned_treaties"] = pruned_treaties
    if portfolios:
        out["summary"] = "ok" if summary_map is not None else "unavailable"
    return runtime.JobResult.ok(**out)


@dramatiq.actor(max_retries=0)
def backfill_edm_detail(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _backfill_edm_detail_body(rwb_job_id))


# ── synchronous drain (unit tier + simple worker) ────────────────────────────────

_BODIES: dict[str, Callable[[Any], runtime.JobResult | dict | None]] = {
    "upload_edm": _upload_edm_body,
    "upload_rdm": _upload_rdm_body,
    "backfill_rdm_analyses": _backfill_rdm_analyses_body,
    "backfill_edm_detail": _backfill_edm_detail_body,
}


def run_one(*, rwb_job_id: Any, rwb_job_type: str, worker_id: str = "worker") -> bool:
    """Claim + run a single ``rwb_job`` through its body. Returns ``run_job``'s result
    (``False`` if the row was already claimed / the type has no body yet)."""
    body = _BODIES.get(rwb_job_type)
    if body is None:
        logger.debug("no body for rwb_job_type %s — skipping", rwb_job_type)
        return False
    return runtime.run_job(rwb_job_id=rwb_job_id, worker_id=worker_id,
                           body=lambda: body(rwb_job_id))


def run_pending(*, worker_id: str = "worker") -> int:
    """Claim + run every currently-``pending`` ``rwb_job`` once. Snapshot-based (rows a
    body enqueues are picked up on the next call), so tests advance the queue by
    calling this after each poller pass. Returns the number of rows run."""
    rows = execute(
        "SELECT id, rwb_job_type FROM rwb_job WHERE status_code = 'pending' "
        "ORDER BY inserted_at, id",
        {}, connection="WORKBENCH",
    )
    count = 0
    for row in rows:
        if run_one(rwb_job_id=row["id"], rwb_job_type=row["rwb_job_type"],
                   worker_id=worker_id):
            count += 1
    return count


__all__ = [
    "upload_edm", "upload_rdm", "backfill_rdm_analyses", "backfill_edm_detail",
    "run_one", "run_pending",
]
