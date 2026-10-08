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

import pytest

from app import log_context
from app.services.rwb_job_service import (
    analysis_link,
    cancel_rwb_job,
    claim_rwb_job,
    complete_rwb_job,
    enqueue_rwb_job,
    ensure_pending_rwb_job,
    get_rwb_job,
    reconcile_stale_rwb_jobs,
)
from app.workers.runtime import upsert_heartbeat
from db import execute_command, execute_one, execute_scalar
from tests.unit.conftest import cedant_id

# Filler for tests exercising dedup/claim/reconcile/cancel mechanics that
# don't care about link/context semantics (CR-04c) — a real EDM/RDM id would
# be noise here.
_NO_LINK = {"link_type": "not_applicable", "link_id": None,
           "context_type": None, "context_id": None}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── idempotent enqueue (A21 dedup key) ────────────────────────────────────────

def test_enqueue_is_idempotent_on_composite_key(iteration2_db):
    rid = str(uuid.uuid4())
    first = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                            rwb_job_type="upload_edm", input_data={"n": 1}, **_NO_LINK)
    dup = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                          rwb_job_type="upload_edm", input_data={"n": 2}, **_NO_LINK)
    assert first is not None
    assert dup is None  # dedup hit — nothing inserted
    n = execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE requestor_id = :r",
                       {"r": rid}, connection="WORKBENCH")
    assert n == 1


def test_enqueue_distinct_type_not_deduped(iteration2_db):
    rid = str(uuid.uuid4())
    a = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                        rwb_job_type="upload_edm", **_NO_LINK)
    b = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                        rwb_job_type="upload_rdm", **_NO_LINK)
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
                           rwb_job_type="upload_edm", **_NO_LINK) is not None
    monkeypatch.setattr(rwb_job_service, "_INSERT_IF_ABSENT", _plain_insert_sql())
    # The losing insert hits the UNIQUE key; it must be absorbed as a dedup hit, not
    # raise (an unhandled IntegrityError would be a 500 on the request path).
    dup = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=rid,
                          rwb_job_type="upload_edm", **_NO_LINK)
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
                                    rwb_job_type="upload_rdm", conn=conn, **_NO_LINK)
            dup = enqueue_rwb_job(requestor_type="irp_job", requestor_id=rid,
                                  rwb_job_type="upload_rdm", conn=conn, **_NO_LINK)
            # the outer txn must survive the absorbed violation and still commit work.
            other = enqueue_rwb_job(requestor_type="irp_job", requestor_id=rid,
                                    rwb_job_type="upload_edm", conn=conn, **_NO_LINK)
    assert first is not None and dup is None and other is not None
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE requestor_id = :r",
                          {"r": rid}, connection="WORKBENCH") == 2


# ── atomic claim (rowcount 1 → 0) ─────────────────────────────────────────────

def test_atomic_claim_wins_once_then_loses(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    assert claim_rwb_job(rwb_job_id=job_id, worker_id="w1") is True
    assert claim_rwb_job(rwb_job_id=job_id, worker_id="w2") is False  # already claimed
    row = execute_one("SELECT status_code, claimed_by FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "running"
    assert row["claimed_by"] == "w1"


# ── heartbeat upsert (one row per job) ────────────────────────────────────────

def test_heartbeat_upsert_keeps_one_row_per_job(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")
    n = execute_scalar("SELECT COUNT(*) FROM rwb_job_heartbeat WHERE rwb_job_id = :id",
                       {"id": job_id}, connection="WORKBENCH")
    assert n == 1


# ── reconciler (reclaim stale running rows) ───────────────────────────────────

def test_reconciler_reclaims_stale_running_row(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
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
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")  # fresh
    assert reconcile_stale_rwb_jobs(stale_secs=120) == 0
    status = execute_scalar("SELECT status_code FROM rwb_job WHERE id = :id",
                            {"id": job_id}, connection="WORKBENCH")
    assert status == "running"


def test_reconciler_reclaims_running_row_with_no_heartbeat(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")  # never heartbeated
    assert reconcile_stale_rwb_jobs(stale_secs=120) == 1
    status = execute_scalar("SELECT status_code FROM rwb_job WHERE id = :id",
                            {"id": job_id}, connection="WORKBENCH")
    assert status == "pending"


# ── in-place completion ───────────────────────────────────────────────────────

def test_complete_sets_terminal_status_and_payload(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=job_id, status="succeeded", output_data={"ok": True})
    row = execute_one(
        "SELECT status_code, output_data, completed_at FROM rwb_job WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "succeeded"
    assert '"ok": true' in row["output_data"]
    assert row["completed_at"] is not None


def test_complete_does_not_revive_a_cancelled_row(iteration2_db):
    # A worker whose row was cancelled while it ran (dead heartbeat) finishes
    # and reports success. `cancelled` is terminal — the report is dropped.
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    assert cancel_rwb_job(rwb_job_id=job_id) is True

    complete_rwb_job(rwb_job_id=job_id, status="succeeded", output_data={"ok": True})

    row = execute_one(
        "SELECT status_code, output_data FROM rwb_job WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "cancelled"
    assert row["output_data"] is None


def test_complete_does_not_stomp_a_reclaimed_row(iteration2_db):
    # The reconciler reset a stale row to pending for another attempt. The
    # original worker then returns; its completion must not land on the row the
    # queue is about to re-dispatch.
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")  # never heartbeated
    assert reconcile_stale_rwb_jobs(stale_secs=120) == 1

    complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="boom")

    row = execute_one("SELECT status_code, error_detail FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "pending"
    assert row["error_detail"] is None


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
                                 rwb_job_type="upload_edm", **_NO_LINK)
    finally:
        log_context.clear(token)
    assert _correlation_of(job_id) == "chain-1"


def test_enqueue_explicit_correlation_wins_over_context(iteration2_db):
    token = log_context.bind(correlation_id="context-id")
    try:
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="upload_edm",
                                 correlation_id="explicit-id", **_NO_LINK)
    finally:
        log_context.clear(token)
    assert _correlation_of(job_id) == "explicit-id"


def test_enqueue_without_context_leaves_null(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()),
                             rwb_job_type="upload_edm", **_NO_LINK)
    assert _correlation_of(job_id) is None


def test_ensure_pending_restamps_on_retry(iteration2_db):
    # An analyst retry is a NEW causal chain — the revived row is re-stamped.
    rid = str(uuid.uuid4())
    token = log_context.bind(correlation_id="first-request")
    try:
        job_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                        requestor_id=rid, rwb_job_type="upload_edm",
                                        **_NO_LINK)
    finally:
        log_context.clear(token)
    assert _correlation_of(job_id) == "first-request"
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="x")
    token = log_context.bind(correlation_id="retry-request")
    try:
        revived = ensure_pending_rwb_job(requestor_type="analyst_request",
                                         requestor_id=rid, rwb_job_type="upload_edm",
                                         **_NO_LINK)
    finally:
        log_context.clear(token)
    assert revived == job_id
    assert _correlation_of(job_id) == "retry-request"


def test_ensure_pending_in_flight_skip_keeps_original_chain(iteration2_db):
    rid = str(uuid.uuid4())
    token = log_context.bind(correlation_id="original")
    try:
        job_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                        requestor_id=rid, rwb_job_type="upload_edm",
                                        **_NO_LINK)
    finally:
        log_context.clear(token)
    token = log_context.bind(correlation_id="second")
    try:
        assert ensure_pending_rwb_job(requestor_type="analyst_request",
                                      requestor_id=rid,
                                      rwb_job_type="upload_edm", **_NO_LINK) is None
    finally:
        log_context.clear(token)
    assert _correlation_of(job_id) == "original"


def test_ensure_pending_does_not_revive_cancelled_job(iteration2_db):
    rid = str(uuid.uuid4())
    job_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                    requestor_id=rid, rwb_job_type="upload_edm",
                                    **_NO_LINK)
    assert cancel_rwb_job(rwb_job_id=job_id) is True

    assert ensure_pending_rwb_job(requestor_type="analyst_request",
                                  requestor_id=rid,
                                  rwb_job_type="upload_edm", **_NO_LINK) is None
    row = execute_one(
        "SELECT status_code, attempt_count FROM rwb_job WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")
    assert row == {"status_code": "cancelled", "attempt_count": 0}


def test_get_rwb_job_returns_row_or_none(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()),
                             rwb_job_type="upload_edm", correlation_id="c-9",
                             **_NO_LINK)
    row = get_rwb_job(rwb_job_id=job_id)
    assert row["rwb_job_type"] == "upload_edm"
    assert row["correlation_id"] == "c-9"
    assert row["status_code"] == "pending"
    assert get_rwb_job(rwb_job_id=str(uuid.uuid4())) is None


def test_reconciler_preserves_original_chain(iteration2_db):
    # A reclaimed row is the SAME causal chain retrying — never re-stamped.
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()),
                             rwb_job_type="upload_edm", correlation_id="chain-1",
                             **_NO_LINK)
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
    "dummy_fail",
    "dummy_wait",
    "execute_analysis_batch",
    "finalize_analysis",
    "load_results_export",
    "refresh_portfolios",
    "retrieve_analysis_results",
    "run_breakout_country",
    "run_breakout_custom",
    "run_breakout_lob",
    "run_breakout_peril",
    "run_breakout_state",
    "run_geohaz",
    "stage_results_export",
    "submit_grouping",
    "submit_results_export",
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


# ── cancel (CR-004a): pending -> cancelled, same race-safety as claim ────────

def test_cancel_pending_row_succeeds(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    assert cancel_rwb_job(rwb_job_id=job_id) is True
    row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "cancelled"


def test_cancel_non_pending_row_is_noop(iteration2_db):
    # succeeded/cancelled are the only statuses cancel_rwb_job still refuses —
    # failed and a dead running row are now cancellable (CR-04a extension).
    for target_status in ("succeeded", "cancelled"):
        job_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                                 **_NO_LINK)
        if target_status == "cancelled":
            cancel_rwb_job(rwb_job_id=job_id)
        else:
            claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
            complete_rwb_job(rwb_job_id=job_id, status=target_status)

        before = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                             {"id": job_id}, connection="WORKBENCH")["status_code"]
        assert before == target_status  # sanity: we set up the state we meant to

        assert cancel_rwb_job(rwb_job_id=job_id) is False
        after = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                            {"id": job_id}, connection="WORKBENCH")["status_code"]
        assert after == target_status  # unchanged


def test_cancel_failed_row_succeeds(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="boom")
    assert cancel_rwb_job(rwb_job_id=job_id) is True
    row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "cancelled"


def test_cancel_running_row_with_live_heartbeat_is_noop(iteration2_db):
    from app.workers.runtime import upsert_heartbeat
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")  # alive — not dead
    assert cancel_rwb_job(rwb_job_id=job_id) is False
    row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "running"


def test_cancel_running_row_with_no_heartbeat_at_all_succeeds(iteration2_db):
    # A worker that claimed the row and crashed before its first heartbeat —
    # no rwb_job_heartbeat row exists at all, which still counts as dead.
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    assert cancel_rwb_job(rwb_job_id=job_id) is True
    row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "cancelled"


def test_cancel_running_row_with_stale_heartbeat_succeeds(iteration2_db):
    from app.workers.runtime import upsert_heartbeat
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    stale_at = _utcnow() - timedelta(seconds=999999)
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1", now=stale_at)
    assert cancel_rwb_job(rwb_job_id=job_id) is True
    row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "cancelled"


def test_claim_racing_cancel_resolves_to_one_winner(iteration2_db):
    # Whichever of claim_rwb_job / cancel_rwb_job runs first against a
    # pending row wins; the other's UPDATE matches zero rows and is a
    # no-op — same shape as two claims racing (test_atomic_claim_wins_once_
    # then_loses above), just the second contender is cancel instead of a
    # second claim. A live heartbeat keeps the claimed row from also matching
    # cancel's dead-running guard, isolating this race to the pending/running
    # transition alone.
    from app.workers.runtime import upsert_heartbeat
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    assert claim_rwb_job(rwb_job_id=job_id, worker_id="w1") is True
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")
    assert cancel_rwb_job(rwb_job_id=job_id) is False  # lost the race — already running
    row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "running"

    job_id_2 = enqueue_rwb_job(requestor_type="analyst_request",
                               requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                               **_NO_LINK)
    assert cancel_rwb_job(rwb_job_id=job_id_2) is True
    assert claim_rwb_job(rwb_job_id=job_id_2, worker_id="w1") is False  # lost — already cancelled
    row_2 = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                        {"id": job_id_2}, connection="WORKBENCH")
    assert row_2["status_code"] == "cancelled"


# ── resubmit (CR-004a): ensure_pending_rwb_job is unchanged, same row reused ─

def test_resubmit_via_ensure_pending_resets_same_row(iteration2_db):
    # Regression check, not new behavior: CR-004a's Resubmit action calls
    # ensure_pending_rwb_job as-is. This pins the exact contract the
    # monitoring page depends on — same id, attempt_count incremented,
    # error_detail cleared — so an accidental future change to that
    # function is caught here, not discovered from the UI.
    rid = str(uuid.uuid4())
    job_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                    requestor_id=rid, rwb_job_type="upload_edm",
                                    **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="boom")

    before = execute_one(
        "SELECT status_code, attempt_count, error_detail FROM rwb_job WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")
    assert before["status_code"] == "failed"
    assert before["attempt_count"] == 0
    assert before["error_detail"] == "boom"

    resubmitted_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                            requestor_id=rid, rwb_job_type="upload_edm",
                                            **_NO_LINK)
    # Compared case-insensitively, not by exact string equality: confirmed
    # directly against the real SQL Server tier that a uniqueidentifier
    # round-trips with different letter casing than the lowercase string
    # Python's uuid.uuid4() generated it as — same row, different casing.
    # A plain "==" here would falsely fail on SQL Server despite being the
    # same id (see test_ensure_pending_restamps_on_retry above for the
    # pre-existing test that has this same latent risk, untested against
    # SQL Server for this specific comparison).
    assert str(resubmitted_id).lower() == str(job_id).lower()  # same row, not a new one

    after = execute_one(
        "SELECT status_code, attempt_count, error_detail, output_data, completed_at "
        "FROM rwb_job WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")
    assert after["status_code"] == "pending"
    assert after["attempt_count"] == 1
    assert after["error_detail"] is None
    assert after["output_data"] is None
    assert after["completed_at"] is None

    # No second row was created for this (requestor_type, requestor_id, rwb_job_type).
    count = execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE requestor_type = 'analyst_request' "
        "AND requestor_id = :rid AND rwb_job_type = 'upload_edm'",
        {"rid": rid}, connection="WORKBENCH")
    assert count == 1


# ── monitoring page reads and by-id resubmit (CR-004a) ───────────────────────

def test_list_rwb_jobs_for_monitoring_returns_all_types_and_fields(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring

    wait_id = enqueue_rwb_job(requestor_type="analyst_request",
                              requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                              **_NO_LINK)
    fail_id = enqueue_rwb_job(requestor_type="analyst_request",
                              requestor_id=str(uuid.uuid4()), rwb_job_type="upload_rdm",
                              **_NO_LINK)
    claim_rwb_job(rwb_job_id=fail_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=fail_id, status="failed", error_detail="boom")

    rows_by_id = {r["id"]: r for r in list_rwb_jobs_for_monitoring()[0]}
    assert wait_id in rows_by_id
    assert fail_id in rows_by_id
    assert rows_by_id[wait_id]["status_code"] == "pending"
    assert rows_by_id[wait_id]["submitted_at"] is None  # never claimed
    assert rows_by_id[fail_id]["status_code"] == "failed"
    assert rows_by_id[fail_id]["error_detail"] == "boom"


def test_resubmit_rwb_job_by_id_matches_ensure_pending_contract(iteration2_db):
    from app.services.rwb_job_service import resubmit_rwb_job

    rid = str(uuid.uuid4())
    job_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                    requestor_id=rid, rwb_job_type="upload_edm",
                                    input_data={"edm_id": "e1"}, **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="boom")

    resubmitted_id = resubmit_rwb_job(rwb_job_id=job_id)
    assert str(resubmitted_id).lower() == str(job_id).lower()  # same row (see casing note above)

    after = execute_one(
        "SELECT status_code, attempt_count, error_detail, input_data "
        "FROM rwb_job WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")
    assert after["status_code"] == "pending"
    assert after["attempt_count"] == 1
    assert after["error_detail"] is None
    assert after["input_data"] == '{"edm_id": "e1"}'  # the row's OWN input, carried forward


def test_resubmit_rwb_job_unknown_id_returns_none(iteration2_db):
    from app.services.rwb_job_service import resubmit_rwb_job

    assert resubmit_rwb_job(rwb_job_id=str(uuid.uuid4())) is None


def test_resubmit_rwb_job_non_terminal_row_returns_none(iteration2_db):
    # ensure_pending_rwb_job's own contract: pending/running rows are
    # skipped (already in flight), not resubmitted — resubmit_rwb_job
    # inherits that unchanged.
    from app.services.rwb_job_service import resubmit_rwb_job

    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                             **_NO_LINK)
    assert resubmit_rwb_job(rwb_job_id=job_id) is None
    row = execute_one("SELECT status_code FROM rwb_job WHERE id = :id",
                      {"id": job_id}, connection="WORKBENCH")
    assert row["status_code"] == "pending"  # untouched


def test_resubmit_rwb_job_rejects_succeeded_and_cancelled_rows(iteration2_db):
    from app.services.rwb_job_service import resubmit_rwb_job

    succeeded_id = enqueue_rwb_job(requestor_type="analyst_request",
                                   requestor_id=str(uuid.uuid4()),
                                   rwb_job_type="upload_edm", **_NO_LINK)
    claim_rwb_job(rwb_job_id=succeeded_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=succeeded_id, status="succeeded")

    cancelled_id = enqueue_rwb_job(requestor_type="analyst_request",
                                   requestor_id=str(uuid.uuid4()),
                                   rwb_job_type="upload_edm", **_NO_LINK)
    cancel_rwb_job(rwb_job_id=cancelled_id)

    assert resubmit_rwb_job(rwb_job_id=succeeded_id) is None
    assert resubmit_rwb_job(rwb_job_id=cancelled_id) is None
    for job_id, status in ((succeeded_id, "succeeded"),
                           (cancelled_id, "cancelled")):
        row = execute_one(
            "SELECT status_code, attempt_count FROM rwb_job WHERE id = :id",
            {"id": job_id}, connection="WORKBENCH")
        assert row == {"status_code": status, "attempt_count": 0}


# ── link/context fields (CR-04c) ──────────────────────────────────────────────

def test_enqueue_requires_link_type(iteration2_db):
    import pytest
    with pytest.raises(TypeError):
        enqueue_rwb_job(requestor_type="analyst_request",
                        requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                        link_id=None, context_type=None, context_id=None)


def test_enqueue_stores_link_and_context_fields(iteration2_db):
    edm_id = str(uuid.uuid4())
    job_id = enqueue_rwb_job(requestor_type="analyst_request", requestor_id=edm_id,
                             rwb_job_type="upload_edm",
                             link_type="edm", link_id=edm_id,
                             context_type="edm", context_id=edm_id)
    row = get_rwb_job(rwb_job_id=job_id)
    assert row["link_type"] == "edm"
    assert row["link_id"] == edm_id
    assert row["context_type"] == "edm"
    assert row["context_id"] == edm_id


def test_analysis_link_is_the_analysis_owner(iteration2_db):
    edm, rdm, sub = "e", "r", "s"
    ids = {}
    for owner, columns in (("edm", (edm, rdm, sub)), ("rdm", (None, rdm, sub)),
                           ("submission", (None, None, sub))):
        ids[owner] = str(uuid.uuid4())
        execute_command(
            "INSERT INTO irp_analysis (id, edm_id, rdm_id, submission_id) "
            "VALUES (:id, :e, :r, :s)",
            {"id": ids[owner], "e": columns[0], "r": columns[1], "s": columns[2]},
            connection="WORKBENCH")
    assert analysis_link(ids["edm"]) == ("edm", edm)
    assert analysis_link(ids["rdm"]) == ("rdm", rdm)
    assert analysis_link(ids["submission"]) == ("submission", sub)


def test_analysis_link_refuses_an_unknown_analysis(iteration2_db):
    with pytest.raises(LookupError):
        analysis_link(str(uuid.uuid4()))


def test_enqueue_allows_null_context_when_job_has_none(iteration2_db):
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()),
                             rwb_job_type="dummy_wait", **_NO_LINK)
    row = get_rwb_job(rwb_job_id=job_id)
    assert row["link_type"] == "not_applicable"
    assert row["link_id"] is None
    assert row["context_type"] is None
    assert row["context_id"] is None


def test_ensure_pending_restamps_link_and_context_on_retry(iteration2_db):
    rid = str(uuid.uuid4())
    first_edm = str(uuid.uuid4())
    job_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                    requestor_id=rid, rwb_job_type="upload_edm",
                                    link_type="edm", link_id=first_edm,
                                    context_type="edm", context_id=first_edm)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="x")

    second_edm = str(uuid.uuid4())
    revived = ensure_pending_rwb_job(requestor_type="analyst_request",
                                     requestor_id=rid, rwb_job_type="upload_edm",
                                     link_type="edm", link_id=second_edm,
                                     context_type="edm", context_id=second_edm)
    assert revived == job_id
    row = get_rwb_job(rwb_job_id=job_id)
    assert row["link_id"] == second_edm
    assert row["context_id"] == second_edm


def test_resubmit_rwb_job_carries_link_and_context_through_unchanged(iteration2_db):
    from app.services.rwb_job_service import resubmit_rwb_job

    rid = str(uuid.uuid4())
    edm_id = str(uuid.uuid4())
    job_id = ensure_pending_rwb_job(requestor_type="analyst_request",
                                    requestor_id=rid, rwb_job_type="upload_edm",
                                    link_type="edm", link_id=edm_id,
                                    context_type="edm", context_id=edm_id)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=job_id, status="failed", error_detail="boom")

    resubmitted_id = resubmit_rwb_job(rwb_job_id=job_id)
    assert str(resubmitted_id).lower() == str(job_id).lower()
    row = get_rwb_job(rwb_job_id=job_id)
    assert row["link_type"] == "edm"
    assert row["link_id"] == edm_id
    assert row["context_type"] == "edm"
    assert row["context_id"] == edm_id


# ── monitoring read: Submitted by, Entity, sort order (#219) ─────────────────

def _edm(*, name="EDM") -> str:
    eid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, source_file_path, name, status, "
        "inserted_at, updated_at) VALUES (:id, :src, :name, 'ready', :now, :now)",
        {"id": eid, "src": r"\share\intake\x.bak", "name": name,
         "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")
    return eid


def _submission(*, name="Sub", assigned_analyst_id) -> str:
    sid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO submission (id, assigned_analyst_id, name, cedant_id, "
        "status_code, inserted_at, updated_at) "
        "VALUES (:id, :a, :name, :cedant, 'ACTIVE', :now, :now)",
        {"id": sid, "a": assigned_analyst_id, "name": name, "cedant": cedant_id(),
         "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")
    return sid


def _dummy(**kwargs) -> str:
    return enqueue_rwb_job(requestor_type="analyst_request",
                           requestor_id=str(uuid.uuid4()),
                           rwb_job_type=kwargs.pop("rwb_job_type", "dummy_wait"),
                           **_NO_LINK, **kwargs)


def _stamp(job_id, *, inserted_at, submitted_at=None) -> None:
    execute_command(
        "UPDATE rwb_job SET inserted_at = :i, submitted_at = :s WHERE id = :id",
        {"i": inserted_at, "s": submitted_at, "id": job_id}, connection="WORKBENCH")


def test_monitoring_no_filters_returns_every_row(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    mine, unattributed = _dummy(actor_id=iteration2_db.user_a), _dummy()
    ids = {r["id"] for r in list_rwb_jobs_for_monitoring()[0]}
    assert {mine, unattributed} <= ids


def test_monitoring_pages_through_every_row(iteration2_db):
    from app.services.rwb_job_service import PAGE_SIZE, list_rwb_jobs_for_monitoring
    ids = {_dummy() for _ in range(PAGE_SIZE + 5)}

    first, first_has_next = list_rwb_jobs_for_monitoring()
    second, second_has_next = list_rwb_jobs_for_monitoring(page=2)

    assert (len(first), first_has_next) == (PAGE_SIZE, True)
    assert (len(second), second_has_next) == (5, False)
    assert {r["id"] for r in first + second} == ids


def test_monitoring_reads_one_row_by_id(iteration2_db):
    # How cancel and resubmit re-render the row they changed.
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    job_id = _dummy()
    _dummy()

    rows = list_rwb_jobs_for_monitoring(rwb_job_ids=[job_id])[0]

    assert [r["id"] for r in rows] == [job_id]


def test_monitoring_submitted_by_matches_inserted_by(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    mine = _dummy(actor_id=iteration2_db.user_a)
    _dummy(actor_id=iteration2_db.user_b)
    _dummy()

    rows = list_rwb_jobs_for_monitoring(submitted_by=[iteration2_db.user_a])[0]

    assert [(r["id"], r["submitted_by"]) for r in rows] == [(mine, "Analyst A")]


def test_monitoring_lists_newest_first_by_start_or_queue_time(iteration2_db):
    # A queued job has no submitted_at yet, so it sorts by when it was queued.
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    old, queued, new = _dummy(), _dummy(), _dummy()
    _stamp(old, inserted_at="2026-10-01 00:00:00", submitted_at="2026-10-01 00:00:05")
    _stamp(queued, inserted_at="2026-10-02 00:00:00")
    _stamp(new, inserted_at="2026-10-03 00:00:00", submitted_at="2026-10-03 00:00:05")

    assert [r["id"] for r in list_rwb_jobs_for_monitoring()[0]] == [new, queued, old]


def test_monitoring_sorts_before_cutting_the_page(iteration2_db):
    from app.services.rwb_job_service import PAGE_SIZE, list_rwb_jobs_for_monitoring
    oldest = _dummy(rwb_job_type="backfill_edm_detail")
    _stamp(oldest, inserted_at="2026-01-01 00:00:00")
    for _ in range(PAGE_SIZE):
        _dummy()

    rows = list_rwb_jobs_for_monitoring(sort="rwb_job_type", descending=False)[0]

    assert rows[0]["id"] == oldest


def test_monitoring_job_type_and_status_filters(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    upload_id = _dummy(rwb_job_type="upload_edm")
    backfill_id = _dummy(rwb_job_type="backfill_edm_detail")
    rows = list_rwb_jobs_for_monitoring(rwb_job_types=["upload_edm"])[0]
    ids = {r["id"] for r in rows}
    assert upload_id in ids
    assert backfill_id not in ids

    claim_rwb_job(rwb_job_id=backfill_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=backfill_id, status="failed")
    rows = list_rwb_jobs_for_monitoring(status_codes=["failed"])[0]
    ids = {r["id"] for r in rows}
    assert backfill_id in ids
    assert upload_id not in ids


def test_monitoring_names_the_context_as_the_entity(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    edm_id = _edm(name="Meridian Property")
    portfolio_id, analysis_id, group_id = (str(uuid.uuid4()) for _ in range(3))
    execute_command("INSERT INTO irp_portfolio (id, edm_id, name) VALUES (:id, :e, 'FL Comm')",
                    {"id": portfolio_id, "e": edm_id}, connection="WORKBENCH")
    execute_command("INSERT INTO irp_analysis (id, edm_id, name) VALUES (:id, :e, 'FL DLM')",
                    {"id": analysis_id, "e": edm_id}, connection="WORKBENCH")
    execute_command("INSERT INTO breakout_group (id, label) VALUES (:id, 'Coastal')",
                    {"id": group_id}, connection="WORKBENCH")
    sub_id = _submission(name="American Family", assigned_analyst_id=iteration2_db.user_a)

    def job(rwb_job_type, link_type, link_id, context_type, context_id):
        return enqueue_rwb_job(
            requestor_type="analyst_request", requestor_id=str(uuid.uuid4()),
            rwb_job_type=rwb_job_type, link_type=link_type, link_id=link_id,
            context_type=context_type, context_id=context_id)

    expected = {
        job("upload_edm", "edm", edm_id, "edm", edm_id): ("EDM", "Meridian Property"),
        job("run_geohaz", "edm", edm_id, "portfolio", portfolio_id): ("Portfolio", "FL Comm"),
        job("finalize_analysis", "edm", edm_id, "irp_analysis", analysis_id):
            ("IRP Analysis", "FL DLM"),
        job("run_breakout_custom", "edm", edm_id, "breakout_group", group_id):
            ("Breakout Group", "Coastal"),
        job("execute_analysis_batch", "edm", edm_id, "execution", str(uuid.uuid4())):
            ("EDM", "Meridian Property"),
        job("submit_results_export", "submission", sub_id, "result_export",
            str(uuid.uuid4())): ("Submission", "American Family"),
        _dummy(): (None, None),
    }

    rows = list_rwb_jobs_for_monitoring()[0]

    assert {r["id"]: (r["entity_kind"], r["entity_name"]) for r in rows} == expected


def test_job_type_kinds_returns_seeded_codes(iteration2_db):
    from app.services.rwb_job_service import job_type_kinds
    codes = [code for code, _ in job_type_kinds()]
    assert "upload_edm" in codes
    assert "backfill_edm_detail" in codes


def test_status_kinds_inserts_synthetic_dead_after_running(iteration2_db):
    from app.services.rwb_job_service import status_kinds
    codes = [code for code, _ in status_kinds()]
    assert codes.index("dead") == codes.index("running") + 1
    assert "pending" in codes and "failed" in codes and "cancelled" in codes


# ── dead-job detection (running + stale/missing heartbeat) ───────────────────

def test_monitoring_marks_running_row_with_no_heartbeat_as_dead(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()),
                             rwb_job_type="upload_edm", **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    rows_by_id = {r["id"]: r for r in list_rwb_jobs_for_monitoring()[0]}
    assert rows_by_id[job_id]["is_dead"] == 1
    assert rows_by_id[job_id]["status_code"] == "running"  # unchanged underneath


def test_monitoring_marks_running_row_with_live_heartbeat_as_not_dead(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    from app.workers.runtime import upsert_heartbeat
    job_id = enqueue_rwb_job(requestor_type="analyst_request",
                             requestor_id=str(uuid.uuid4()),
                             rwb_job_type="upload_edm", **_NO_LINK)
    claim_rwb_job(rwb_job_id=job_id, worker_id="w1")
    upsert_heartbeat(rwb_job_id=job_id, worker_id="w1")
    rows_by_id = {r["id"]: r for r in list_rwb_jobs_for_monitoring()[0]}
    assert rows_by_id[job_id]["is_dead"] == 0


def test_monitoring_pending_and_terminal_rows_are_never_dead(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    pending_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="upload_edm", **_NO_LINK)
    rows_by_id = {r["id"]: r for r in list_rwb_jobs_for_monitoring()[0]}
    assert rows_by_id[pending_id]["is_dead"] == 0


def test_monitoring_dead_status_filter_matches_only_dead_running_rows(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    from app.workers.runtime import upsert_heartbeat
    dead_id = enqueue_rwb_job(requestor_type="analyst_request",
                              requestor_id=str(uuid.uuid4()),
                              rwb_job_type="upload_edm", **_NO_LINK)
    claim_rwb_job(rwb_job_id=dead_id, worker_id="w1")  # no heartbeat -> dead

    alive_id = enqueue_rwb_job(requestor_type="analyst_request",
                               requestor_id=str(uuid.uuid4()),
                               rwb_job_type="upload_edm", **_NO_LINK)
    claim_rwb_job(rwb_job_id=alive_id, worker_id="w2")
    upsert_heartbeat(rwb_job_id=alive_id, worker_id="w2")

    pending_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="upload_edm", **_NO_LINK)

    ids = {r["id"] for r in list_rwb_jobs_for_monitoring(status_codes=["dead"])[0]}
    assert dead_id in ids
    assert alive_id not in ids
    assert pending_id not in ids


def test_monitoring_dead_and_real_status_filter_combine_with_or(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    dead_id = enqueue_rwb_job(requestor_type="analyst_request",
                              requestor_id=str(uuid.uuid4()),
                              rwb_job_type="upload_edm", **_NO_LINK)
    claim_rwb_job(rwb_job_id=dead_id, worker_id="w1")

    failed_id = enqueue_rwb_job(requestor_type="analyst_request",
                                requestor_id=str(uuid.uuid4()),
                                rwb_job_type="upload_rdm", **_NO_LINK)
    claim_rwb_job(rwb_job_id=failed_id, worker_id="w2")
    complete_rwb_job(rwb_job_id=failed_id, status="failed")

    ids = {r["id"] for r in
           list_rwb_jobs_for_monitoring(status_codes=["dead", "failed"])[0]}
    assert dead_id in ids
    assert failed_id in ids
