"""Dramatiq actors for the app-side work queue (Article 10 / worker-poller.md §2).

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
    package_sync_service,
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
    package_id = ctx.get("package_id")
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
            package_id=package_id, irp_job_type="import_edm", irp_edm_id=edm_id,
            payload={"name": edm.name, "source_file_path": edm.source_file_path})
        edm_service.mark_error(edm_id=edm_id)
        return runtime.JobResult.fail(f"upload_edm submit failed: {exc}",
                                      submit_failed=str(exc))

    # AT-LEAST-ONCE WINDOW (see module docstring): the submit above already reached RM.
    # A crash before this record + mark_importing complete leaves the EDM pending_import
    # with no irp_job → a retry re-submits (duplicate exposure). Accepted this iteration.
    irp_job_id = irp_job_service.record_submitted_irp_job(
        package_id=package_id, irp_job_type="import_edm", irp_edm_id=edm_id,
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

def _apply_exists(rdm_id: Any, edm_id: Any) -> bool:
    """True if an ``import_rdm`` apply already exists for this (RDM, EDM) pair (a
    prior successful submit). Makes the fan-out idempotent per pair across re-runs."""
    n = execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm' "
        "AND irp_rdm_id=:r AND irp_edm_id=:e AND status<>'SUBMISSION FAILED'",
        {"r": str(rdm_id), "e": str(edm_id)}, connection="WORKBENCH")
    return bool(n)


def _upload_rdm_body(rwb_job_id: Any) -> runtime.JobResult:
    """Fan out one apply per (RDM, EDM) pair — every apply targets an EDM (D3;
    review-only is deferred). One ``irp_job(import_rdm)`` per apply; idempotent per
    pair. The EDM is name-resolved at submit time (Article 2).

    Each apply that never reaches Risk Modeler is recorded as a ``SUBMISSION FAILED``
    ``irp_job`` (for the retry batch) and flips the RDM to ``error`` — its combined
    rollup is ``error`` if *any* apply fails (rdm_service). The ``rwb_job`` fails only
    when the fan-out submitted nothing at all (every attempted apply failed); a partial
    fan-out ``succeeds`` (the failures are carried by their ``irp_job`` rows + the RDM's
    ``error`` state)."""
    ctx = _load_input(rwb_job_id)
    rdm_ids = ctx.get("rdm_ids", [])
    edm_ids = [e for e in (ctx.get("edm_ids") or []) if e]
    package_id = ctx.get("package_id")

    submitted = 0
    failed = 0
    for rdm_id in rdm_ids:
        rdm = rdm_service.get_rdm(rdm_id)
        if rdm is None:
            continue
        for edm_id in edm_ids:
            if _apply_exists(rdm_id, edm_id):
                continue
            edm = edm_service.get_edm(edm_id)
            edm_name = edm.name if edm is not None else None
            if edm_name is None:
                continue  # target EDM vanished — nothing to apply against
            try:
                res = irp_gateway.submit_rdm_import(
                    name=rdm.name, source_file_path=rdm.source_file_path,
                    edm_name=edm_name)
            except Exception as exc:  # noqa: BLE001 — SUBMISSION FAILED, not a crash
                logger.warning("upload_rdm submit failed (rdm=%s edm=%s): %s",
                               rdm_id, edm_id, exc)
                irp_job_service.record_submission_failure(
                    package_id=package_id, irp_job_type="import_rdm",
                    irp_edm_id=edm_id, irp_rdm_id=rdm_id,
                    payload={"name": rdm.name, "edm_name": edm_name})
                rdm_service.mark_error(rdm_id=rdm_id)
                failed += 1
                continue
            # AT-LEAST-ONCE WINDOW (see module docstring): the apply above reached RM;
            # a crash before this record leaves the pair un-recorded → a retry re-applies.
            irp_job_service.record_submitted_irp_job(
                package_id=package_id, irp_job_type="import_rdm",
                irp_edm_id=edm_id, irp_rdm_id=rdm_id, irp_id=res.irp_id,
                resource_uri=res.resource_uri, payload=res.payload,
                response=res.response)
            rdm_service.mark_importing(rdm_id=rdm_id)
            logger.info("import_rdm submitted (rdm=%s edm=%s irp_id=%s)",
                        rdm_id, edm_id, res.irp_id)
            submitted += 1
    if failed and submitted == 0:
        return runtime.JobResult.fail(
            f"upload_rdm submitted no applies ({failed} failed)",
            applies_submitted=0, applies_failed=failed)
    return runtime.JobResult.ok(applies_submitted=submitted, applies_failed=failed)


@dramatiq.actor(max_retries=0)
def upload_rdm(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _upload_rdm_body(rwb_job_id))


# ── backfill_rdm_analyses (US2, D2) ──────────────────────────────────────────────

_INSERT_ANALYSIS_IF_ABSENT = """
    INSERT INTO irp_analysis (id, rdm_id, edm_id, package_id, irp_id, name,
        source_rdm_name, status_code, created_by_irp_job_irp_id, is_group,
        inserted_at, updated_at)
    SELECT :id, :rdm, :edm, :pkg, :irp, :name, :srdm, 'ready', :cby, 0, :now, :now
    WHERE NOT EXISTS (
        SELECT 1 FROM irp_analysis
        WHERE rdm_id = :rdm AND edm_id = :edm AND irp_id = :irp
    )
"""

# The spec-004 detail overwrite (idempotent in place, R2): settings_metadata +
# is_group only when the metadata fetch succeeded (never null a prior good
# snapshot on a failed re-run); the promoted pointer is always refreshed.
_UPDATE_ANALYSIS_DETAIL = """
    UPDATE irp_analysis
    SET settings_metadata = :sm, is_group = :grp, exposure_resource_id = :x,
        updated_at = :now
    WHERE rdm_id = :rdm AND edm_id = :edm AND irp_id = :irp
"""
_UPDATE_ANALYSIS_POINTER = """
    UPDATE irp_analysis
    SET exposure_resource_id = :x, updated_at = :now
    WHERE rdm_id = :rdm AND edm_id = :edm AND irp_id = :irp
"""


def _backfill_rdm_analyses_body(rwb_job_id: Any) -> dict:
    """Capture this (RDM, EDM) pair's broker analyses as ``irp_analysis`` rows so a
    later package delete can enumerate them (D2, data-model §6a). Enqueued by the
    poller when an ``import_rdm`` apply reaches FINISHED. Idempotent on
    ``UNIQUE(rdm_id, edm_id, irp_id)``. Once every apply of the RDM is FINISHED, roll
    ``irp_rdm.status`` up to ``ready`` (combined rollup, worker-poller.md §2).

    Spec 004 US3 extension (R3/R9): per captured analysis, also fetch its
    settings/metadata (single-item ``get_analysis_metadata``, looped app-side)
    and store the ``settings_metadata`` snapshot + ``is_group``, promoting RM's
    ``exposureResourceId`` to the typed column ONLY when the resource type is
    PORTFOLIO (null otherwise). One analysis's failed metadata read leaves its
    fields blank and never aborts the capture (blank, not error). No portfolio
    lookup here — resolution is read-time in ``analysis_service``.

    Manual RDM sync (spec 004 follow-up, 2026-07-24): the analyst-keyed head
    carries NO ``edm_id`` — the body then derives every applied (RDM, EDM) pair
    from the ``import_rdm`` irp_job rows and re-captures each, so rows captured
    before the detail extension shipped pick up their settings/pointer without
    a re-import."""
    ctx = _load_input(rwb_job_id)
    rdm_id = ctx.get("rdm_id")
    package_id = ctx.get("package_id")
    apply_irp_id = ctx.get("apply_irp_id")
    rdm = rdm_service.get_rdm(rdm_id) if rdm_id else None
    if rdm is None:
        return {"skipped": "rdm/edm missing"}
    if ctx.get("edm_id"):
        edm_ids = [str(ctx["edm_id"])]
    else:  # analyst sync — every EDM this RDM was ever applied to
        edm_ids = [str(r["irp_edm_id"]) for r in execute(
            "SELECT DISTINCT irp_edm_id FROM irp_job "
            "WHERE irp_rdm_id = :r AND irp_job_type = 'import_rdm' "
            "AND irp_edm_id IS NOT NULL",
            {"r": str(rdm_id)}, connection="WORKBENCH")]
    pairs = [(eid, edm) for eid in edm_ids
             if (edm := edm_service.get_edm(eid)) is not None]
    if not pairs:
        return {"skipped": "rdm/edm missing"}

    # Per pair: enumerate + fetch every analysis's metadata BEFORE opening the
    # transaction (no txn across a gateway round-trip, Article 11); per-analysis
    # isolation. The gateway builds the filter with safe json.dumps quoting —
    # never interpolate names into a filter here.
    hits_by_edm: dict[str, list] = {}
    meta_by_id: dict[str, Any] = {}
    metadata_failures = 0
    for eid, edm in pairs:
        hits_by_edm[eid] = irp_gateway.search_analyses(
            source_rdm_name=rdm.name, exposure_name=edm.name)
        for hit in hits_by_edm[eid]:
            if hit.analysis_id in meta_by_id:
                continue
            try:
                meta_by_id[hit.analysis_id] = irp_gateway.get_analysis_metadata(
                    analysis_id=int(hit.analysis_id))
            except Exception as exc:  # noqa: BLE001 — blank, never error (US3 acc. 3)
                logger.warning("backfill_rdm_analyses: metadata read failed "
                               "(analysis=%s): %s", hit.analysis_id, exc)
                metadata_failures += 1

    now = _utcnow()
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            for edm_id, hits in hits_by_edm.items():
                for hit in hits:
                    # The NOT EXISTS pre-check is not atomic under READ COMMITTED;
                    # a concurrent backfill of the same pair can win the race and
                    # leave this insert violating UNIQUE(rdm_id, edm_id, irp_id).
                    # Absorb that in a SAVEPOINT as a dedup hit so the outer txn
                    # (and the rollup) survives.
                    try:
                        with conn.begin_nested():
                            conn.execute(text(_INSERT_ANALYSIS_IF_ABSENT), {
                                "id": str(uuid.uuid4()),
                                "rdm": str(rdm_id), "edm": str(edm_id),
                                "pkg": (str(package_id) if package_id else None),
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
                    key = {"rdm": str(rdm_id), "edm": str(edm_id),
                           "irp": str(hit.analysis_id), "x": pointer, "now": now}
                    if meta is not None:
                        conn.execute(text(_UPDATE_ANALYSIS_DETAIL), {
                            **key,
                            "sm": (json.dumps(meta.payload) if meta.payload else None),
                            "grp": (1 if meta.is_group else 0)})
                    else:
                        conn.execute(text(_UPDATE_ANALYSIS_POINTER), key)
            # Combined rollup: irp_rdm → ready once all its applies are FINISHED;
            # stamp the RDM's last-synced trust signal alongside (FR-052).
            rdm_service.rollup_on_terminal(
                conn, rdm_id=rdm_id, rm_status="FINISHED", irp_id=apply_irp_id)
            conn.execute(text(
                "UPDATE irp_rdm SET as_of = :now, updated_at = :now WHERE id = :id"
            ), {"now": now, "id": str(rdm_id)})
    captured = sum(len(h) for h in hits_by_edm.values())
    logger.info("captured %d analysis row(s) for rdm=%s across %d edm(s)",
                captured, rdm_id, len(hits_by_edm))
    out: dict[str, Any] = {"captured": captured}
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
        # import FINISHED). A manual Sync resolves it by name exactly like the
        # poller; zero or ambiguous hits keep the graceful skip (R7).
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

    # ONE DataBridge aggregate per EDM (TIV/geography/currency/sub-perils —
    # absent from every RM REST read; Addendum A T057). Enrichment only: ANY
    # failure (missing wheel method / databridge extra / env / SQL) degrades to
    # "summary": null — the metrics half of the snapshot must still land.
    summary_map: dict[str, dict] | None = None
    if portfolios:
        try:
            summary_map = irp_gateway.get_edm_exposure_summary(edm_name=edm.name)
        except Exception as exc:  # noqa: BLE001 — enrichment only
            logger.warning("backfill_edm_detail: exposure summary unavailable "
                           "(edm=%s): %s", edm_id, exc)

    now = _utcnow()
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
        portfolio_service.upsert_portfolio_detail(
            edm_id=edm_id, irp_id=p.irp_id, name=p.name,
            exposure_detail={"metrics": exposure.payload, "summary": summary},
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
    if portfolios:
        out["summary"] = "ok" if summary_map is not None else "unavailable"
    return runtime.JobResult.ok(**out)


@dramatiq.actor(max_retries=0)
def backfill_edm_detail(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _backfill_edm_detail_body(rwb_job_id))


# ── delete_rdm (US4) — SYNCHRONOUS, no irp_job ───────────────────────────────────

def _delete_rdm_body(rwb_job_id: Any) -> dict:
    """Synchronously delete the RDM's captured analyses in Risk Modeler (no ``irp_job``,
    R6), mark it ``deleted``, then run the RDM→EDM fan-in: once **all** the package's
    RDMs are ``deleted``, enqueue the ``delete_edm`` heads (or finalize the package if it
    has no EDMs). Idempotent — a duplicate success never double-enqueues.

    Delete-enumeration reads the ``irp_analysis`` rows the ``backfill_rdm_analyses``
    worker captured at import (D2): loop ``delete_analysis(analysis_id)`` over every
    not-yet-deleted row and stamp its ``deleted_at`` — a re-run skips already-deleted
    analyses, so it is safe under redelivery/reconcile."""
    ctx = _load_input(rwb_job_id)
    rdm_id = ctx.get("rdm_id")
    package_id = ctx.get("package_id")
    rdm = rdm_service.get_rdm(rdm_id) if rdm_id else None
    if rdm is None:
        return {"skipped": "rdm missing"}

    # Synchronous per-analysis delete (no irp_job). Stamp deleted_at per row so a
    # redelivery skips analyses already removed.
    deleted = 0
    for row in execute(
        "SELECT id, irp_id FROM irp_analysis "
        "WHERE rdm_id = :r AND deleted_at IS NULL",
        {"r": str(rdm_id)}, connection="WORKBENCH",
    ):
        irp_gateway.delete_analysis(analysis_id=int(row["irp_id"]))
        execute_command(
            "UPDATE irp_analysis SET deleted_at = :now, updated_at = :now "
            "WHERE id = :id",
            {"now": _utcnow(), "id": str(row["id"])}, connection="WORKBENCH")
        deleted += 1

    dispatch_edm_ids: list[str] = []
    finalize = False
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            conn.execute(text(
                "UPDATE irp_rdm SET status='deleted', updated_at=:now "
                "WHERE id=:r AND status<>'deleted'"),
                {"now": _utcnow(), "r": str(rdm_id)})
            remaining = conn.execute(text(
                "SELECT COUNT(*) FROM irp_rdm WHERE package_id=:p "
                "AND deleted_at IS NULL AND status<>'deleted'"),
                {"p": str(package_id)}).scalar()
            if not remaining or int(remaining) == 0:
                edms = conn.execute(text(
                    "SELECT id FROM irp_edm WHERE package_id=:p AND deleted_at IS NULL"),
                    {"p": str(package_id)}).mappings().all()
                if edms:
                    for e in edms:
                        eid = str(e["id"])
                        jid = rwb_job_service.enqueue_rwb_job(
                            requestor_type="analyst_request", requestor_id=eid,
                            rwb_job_type="delete_edm",
                            input_data={"edm_id": eid, "package_id": str(package_id)},
                            conn=conn)
                        if jid:
                            dispatch_edm_ids.append(jid)
                else:
                    finalize = True
            if finalize:
                package_sync_service.finalize_package(package_id=package_id, conn=conn)
    for jid in dispatch_edm_ids:
        dispatch.dispatch(rwb_job_id=jid, rwb_job_type="delete_edm")
    logger.info("rdm %s deleted (%d analysis delete(s) in RM, %d delete_edm head(s))",
                rdm_id, deleted, len(dispatch_edm_ids))
    return {"deleted_rdm": str(rdm_id), "analyses_deleted": deleted}


@dramatiq.actor(max_retries=0)
def delete_rdm(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _delete_rdm_body(rwb_job_id))


# ── delete_edm (US4) — async (pollable irp_job) ──────────────────────────────────

def _delete_edm_body(rwb_job_id: Any) -> runtime.JobResult:
    """Under the atomic ``delete_pending`` guard, submit the EDM delete and record a
    pollable ``irp_job(delete_edm)``. An EDM never imported to RM (no ``irp_id``) is
    marked ``deleted`` inline and the package finalized (no async op to poll). A submit
    that never reaches Risk Modeler records a ``SUBMISSION FAILED`` ``irp_job`` and fails
    the ``rwb_job``; the EDM stays ``delete_pending`` (a visible, non-``deleted`` state,
    so the package will not wrongly finalize) pending re-trigger."""
    ctx = _load_input(rwb_job_id)
    edm_id = ctx.get("edm_id")
    package_id = ctx.get("package_id")
    edm = edm_service.get_edm(edm_id) if edm_id else None
    if edm is None:
        return runtime.JobResult.ok(skipped="edm missing")
    if not edm_service.claim_for_delete(edm_id=edm_id):
        return runtime.JobResult.ok(skipped="already deleting/deleted")

    if edm.irp_id is None:
        with get_connection("WORKBENCH") as conn:
            with conn.begin():
                edm_service.set_deleted(conn, edm_id=edm_id)
                package_sync_service.finalize_package(package_id=package_id, conn=conn)
        logger.info("edm %s deleted inline (never imported to RM)", edm_id)
        return runtime.JobResult.ok(deleted_edm=str(edm_id), no_rm=True)

    try:
        res = irp_gateway.submit_delete_edm(edm_irp_id=int(edm.irp_id))
    except Exception as exc:  # noqa: BLE001 — SUBMISSION FAILED, not a crash
        logger.warning("delete_edm submit failed for %s: %s", edm_id, exc)
        irp_job_service.record_submission_failure(
            package_id=package_id, irp_job_type="delete_edm", irp_edm_id=edm_id,
            payload={"edm_irp_id": edm.irp_id})
        return runtime.JobResult.fail(f"delete_edm submit failed: {exc}",
                                      submit_failed=str(exc))

    # AT-LEAST-ONCE WINDOW (see module docstring): the delete submit above reached RM;
    # a crash before this record leaves the EDM delete_pending with no irp_job → a retry
    # re-submits the delete (harmless-ish for delete, but the same seam). Accepted here.
    irp_job_id = irp_job_service.record_submitted_irp_job(
        package_id=package_id, irp_job_type="delete_edm", irp_edm_id=edm_id,
        irp_id=res.irp_id, resource_uri=res.resource_uri, payload=res.payload,
        response=res.response)
    logger.info("delete_edm submitted for edm=%s (irp_id=%s)", edm_id, res.irp_id)
    return runtime.JobResult.ok(irp_job_id=irp_job_id, irp_id=res.irp_id)


@dramatiq.actor(max_retries=0)
def delete_edm(rwb_job_id: str) -> None:
    runtime.run_job(rwb_job_id=rwb_job_id, worker_id=_worker_id(),
                    body=lambda: _delete_edm_body(rwb_job_id))


# ── synchronous drain (unit tier + simple worker) ────────────────────────────────

_BODIES: dict[str, Callable[[Any], runtime.JobResult | dict | None]] = {
    "upload_edm": _upload_edm_body,
    "upload_rdm": _upload_rdm_body,
    "backfill_rdm_analyses": _backfill_rdm_analyses_body,
    "backfill_edm_detail": _backfill_edm_detail_body,
    "delete_rdm": _delete_rdm_body,
    "delete_edm": _delete_edm_body,
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
    "delete_rdm", "delete_edm",
    "run_one", "run_pending",
]
