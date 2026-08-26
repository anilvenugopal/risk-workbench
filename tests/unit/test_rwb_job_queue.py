"""Unit tests for the Article-10 rwb_job queue state machine (T016).

The constitutional mandate (Article 10 / data-model §9): the SQL table is the
queue — an **atomic claim** (rowcount 1 then 0), a **heartbeat** (one row per job),
and a **reconciler** that reclaims a dead worker's stale ``running`` row. Also
covers the idempotent enqueue (the A21 dedup backbone) and in-place completion.

Runs on the SQLite unit mirror (``iteration2_db``); no external deps.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app import log_context
from app.services.rwb_job_service import (
    claim_rwb_job,
    complete_rwb_job,
    enqueue_rwb_job,
    ensure_pending_rwb_job,
    get_rwb_job,
    reconcile_stale_rwb_jobs,
)
from app.workers.runtime import upsert_heartbeat
from db import execute_one, execute_scalar


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── idempotent enqueue (A21 dedup key) ────────────────────────────────────────

def test_enqueue_is_idempotent_on_composite_key(iteration2_db):
    rid = str(uuid.uuid4())
    first = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                            rwb_job_type="upload_edm", input_data={"n": 1})
    dup = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                          rwb_job_type="upload_edm", input_data={"n": 2})
    assert first is not None
    assert dup is None  # dedup hit — nothing inserted
    n = execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE requestor_id = :r",
                       {"r": rid}, connection="WORKBENCH")
    assert n == 1


def test_enqueue_distinct_type_not_deduped(iteration2_db):
    rid = str(uuid.uuid4())
    a = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                        rwb_job_type="upload_edm")
    b = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                        rwb_job_type="upload_rdm")
    assert a is not None and b is not None and a != b


# ── concurrent-writer race: the UNIQUE key (not the pre-check) is the dedup guard ──
# The unit tier is single-threaded, so a true race can't occur here; stripping the
# NOT EXISTS guard reproduces the exact statement a losing concurrent writer runs
# under READ COMMITTED once both pass the pre-check (review item 2).

def _plain_insert_sql() -> str:
    from app.services import rwb_job_service
    return rwb_job_service._INSERT_IF_ABSENT.split("WHERE NOT EXISTS")[0]


def test_enqueue_absorbs_unique_violation_request_path(iteration2_db, monkeypatch):
    from app.services import rwb_job_service
    rid = str(uuid.uuid4())
    assert enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                           rwb_job_type="upload_edm") is not None
    monkeypatch.setattr(rwb_job_service, "_INSERT_IF_ABSENT", _plain_insert_sql())
    # The losing insert hits the UNIQUE key; it must be absorbed as a dedup hit, not
    # raise (an unhandled IntegrityError would be a 500 on the request path).
    dup = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                          rwb_job_type="upload_edm")
    assert dup is None
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE requestor_id = :r",
                          {"r": rid}, connection="WORKBENCH") == 1


def test_enqueue_absorbs_unique_violation_conn_path(iteration2_db, monkeypatch):
    from app.services import rwb_job_service
    from db import get_connection
    rid = str(uuid.uuid4())
    monkeypatch.setattr(rwb_job_service, "_INSERT_IF_ABSENT", _plain_insert_sql())
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            first = enqueue_rwb_job(requestor_type="irp_job", requestor_id=rid,
                                    rwb_job_type="upload_rdm", conn=conn)
            dup = enqueue_rwb_job(requestor_type="irp_job", requestor_id=rid,
                                  rwb_job_type="upload_rdm", conn=conn)
            # the outer txn must survive the absorbed violation and still commit work.
            other = enqueue_rwb_job(requestor_type="irp_job", requestor_id=rid,
                                    rwb_job_type="upload_edm", conn=conn)
    assert first is not None and dup is None and other is not None
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE requestor_id = :r",
                          {"r": rid}, connection="WORKBENCH") == 2


# ── atomic claim (rowcount 1 → 0) ─────────────────────────────────────────────

def test_atomic_claim_wins_once_then_loses(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm")
    assert claim_rwb_job(rwb_job_id=job_id, worker_id="w1") is True
    assert claim_rwb_job(rwb_job_id=job_id, worker_id="w2") is False  # already claimed
    row = execute_one("SELECT status_code, claimed_by FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "running"
    assert row["claimed_by"] == "w1"


# ── heartbeat upsert (one row per job) ────────────────────────────────────────

def test_heartbeat_upsert_keeps_one_row_per_job(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm")
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")
    n = execute_scalar("SELECT COUNT(*) FROM rwb_job_heartbeat WHERE rwb_job_id = :id",
                       {"id": job_id}, connection="WORKBENCH")
    assert n == 1


# ── reconciler (reclaim stale running rows) ───────────────────────────────────

def test_reconciler_reclaims_stale_running_row(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm")
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    # heartbeat is 10 minutes old → stale.
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1",
                     now=_utcnow() - timedelta(minutes=10))
    reclaimed = reconcile_stale_rwb_jobs(stale_secs=120)
    assert reclaimed == 1
    row = execute_one("SELECT status_code, claimed_by FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "pending"
    assert row["claimed_by"] is None


def test_reconciler_leaves_fresh_running_row(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm")
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")  # fresh
    assert reconcile_stale_rwb_jobs(stale_secs=120) == 0
    status = execute_scalar("SELECT status_code FROM rwb_job WHERE id = :id",
                            {"id": job_id}, connection="WORKBENCH")
    assert status == "running"


def test_reconciler_reclaims_running_row_with_no_heartbeat(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm")
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")  # never heartbeated
    assert reconcile_stale_rwb_jobs(stale_secs=120) == 1
    status = execute_scalar("SELECT status_code FROM rwb_job WHERE id = :id",
                            {"id": job_id}, connection="WORKBENCH")
    assert status == "pending"


# ── in-place completion ───────────────────────────────────────────────────────

def test_complete_sets_terminal_status_and_payload(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm")
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=job_id, status="succeeded", output_data={"ok": True})
    row = execute_one(
        "SELECT status_code, output_data, completed_at FROM rwb_job WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "succeeded"
    assert '"ok": true' in row["output_data"]
    assert row["completed_at"] is not None


# ── correlation_id stamping (issue #28) ───────────────────────────────────────
# The chain id defaults from the bound log context (request middleware / poller /
# worker binds it), so no enqueue call site passes it explicitly.

def _correlation_of(job_id: str) -> str | None:
    return execute_scalar("SELECT correlation_id FROM rwb_job WHERE id = :id",
                          {"id": job_id}, connection="WORKBENCH")


def test_enqueue_stamps_bound_context_correlation(iteration2_db):
    token = log_context.bind(correlation_id="chain-1")
    try:
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="upload_edm")
    finally:
        log_context.clear(token)
    assert _correlation_of(job_id) == "chain-1"


def test_enqueue_explicit_correlation_wins_over_context(iteration2_db):
    token = log_context.bind(correlation_id="context-id")
    try:
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="upload_edm",
                                 correlation_id="explicit-id")
    finally:
        log_context.clear(token)
    assert _correlation_of(job_id) == "explicit-id"


def test_enqueue_without_context_leaves_null(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()),
                             rwb_job_type="upload_edm")
    assert _correlation_of(job_id) is None


def test_ensure_pending_restamps_on_retry(iteration2_db):
    # An analyst retry is a NEW causal chain — the revived row is re-stamped.
    rid = str(uuid.uuid4())
    token = log_context.bind(correlation_id="first-request")
    try:
        job_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                        requestor_id=rid, rwb_job_type="upload_edm")
    finally:
        log_context.clear(token)
    assert _correlation_of(job_id) == "first-request"
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="x")
    token = log_context.bind(correlation_id="retry-request")
    try:
        revived = ensure_pending_rwb_job(requestor_type="analyst_request",
                                         requestor_id=rid, rwb_job_type="upload_edm")
    finally:
        log_context.clear(token)
    assert revived == job_id
    assert _correlation_of(job_id) == "retry-request"


def test_ensure_pending_in_flight_skip_keeps_original_chain(iteration2_db):
    rid = str(uuid.uuid4())
    token = log_context.bind(correlation_id="original")
    try:
        job_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                        requestor_id=rid, rwb_job_type="upload_edm")
    finally:
        log_context.clear(token)
    token = log_context.bind(correlation_id="second")
    try:
        assert ensure_pending_rwb_job(requestor_type="analyst_request",
                                      requestor_id=rid,
                                      rwb_job_type="upload_edm") is None
    finally:
        log_context.clear(token)
    assert _correlation_of(job_id) == "original"


def test_get_rwb_job_returns_row_or_none(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()),
                             rwb_job_type="upload_edm", correlation_id="c-9")
    row = get_rwb_job(rwb_job_id=job_id)
    assert row["rwb_job_type"] == "upload_edm"
    assert row["correlation_id"] == "c-9"
    assert row["status_code"] == "pending"
    assert get_rwb_job(rwb_job_id=str(uuid.uuid4())) is None


def test_reconciler_preserves_original_chain(iteration2_db):
    # A reclaimed row is the SAME causal chain retrying — never re-stamped.
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()),
                             rwb_job_type="upload_edm", correlation_id="chain-1")
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")  # never heartbeated → stale
    assert reconcile_stale_rwb_jobs(stale_secs=120) == 1
    assert _correlation_of(job_id) == "chain-1"


# ── per-queue worker isolation (CR-004): queue_name derivation ───────────────────
#
# No database fixture — these only check Dramatiq's in-memory actor registry
# after app.workers.loader.discover_jobs() runs. No Redis command is sent:
# RedisBroker(url=...) only constructs a lazy redis.Redis client at import time
# (app.workers.broker), and discover_jobs()'s importlib.import_module calls are
# no-ops against an already-imported module, so calling it more than once in one
# test process never re-registers (and never raises "already registered" on) an
# actor.

_EXPECTED_QUEUE_NAMES = [
    "backfill_edm_detail",
    "backfill_rdm_analyses",
    "run_breakout_country",
    "run_breakout_custom",
    "run_breakout_lob",
    "run_breakout_peril",
    "run_breakout_state",
    "run_geohaz",
    "sync_irp_metadata",
    "upload_edm",
    "upload_rdm",
]


def test_every_actor_queue_name_matches_actor_name():
    # Catches a future actor declared with a raw @dramatiq.actor instead of
    # @app.workers.queues.rwb_actor, in ANY *_jobs.py module — not just the
    # ones this feature was originally scoped around.
    import dramatiq

    from app.workers import loader

    loader.discover_jobs()
    for name, actor in dramatiq.get_broker().actors.items():
        assert actor.queue_name == name, (
            f"actor {name!r} has queue_name {actor.queue_name!r} — "
            "every actor must use @rwb_actor, never a raw @dramatiq.actor"
        )


def test_queue_names_returns_current_actors():
    # Exact list, not membership-only: a real job type silently disappearing
    # from the queue list must fail this test, not just an unexpected new one
    # appearing. Update _EXPECTED_QUEUE_NAMES when a job type is intentionally
    # added or removed — that one-line update is the cost of catching a
    # silent drop immediately instead of only in production.
    from app.workers.queues import queue_names

    assert queue_names() == _EXPECTED_QUEUE_NAMES
