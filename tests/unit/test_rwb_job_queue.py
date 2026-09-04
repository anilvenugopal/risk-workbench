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
    "retrieve_analysis_results",
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

    rows_by_id = {r["id"]: r for r in list_rwb_jobs_for_monitoring()}
    assert wait_id in rows_by_id
    assert fail_id in rows_by_id
    assert rows_by_id[wait_id]["status_code"] == "pending"
    assert rows_by_id[wait_id]["submitted_at"] is None  # never claimed
    assert rows_by_id[fail_id]["status_code"] == "failed"
    assert rows_by_id[fail_id]["error_detail"] == "boom"


def test_list_rwb_jobs_for_monitoring_orders_by_type_then_status_then_recency(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring

    # Two rows of the SAME type, both pending — updated_at DESC within the
    # group means the more-recently-touched one (job_b, cancelled after
    # job_a was enqueued) sorts first.
    job_a = enqueue_rwb_job(requestor_type="analyst_request",
                            requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                            **_NO_LINK)
    job_b = enqueue_rwb_job(requestor_type="analyst_request",
                            requestor_id=str(uuid.uuid4()), rwb_job_type="upload_edm",
                            **_NO_LINK)
    cancel_rwb_job(rwb_job_id=job_b)  # touches job_b's updated_at

    rows = [r for r in list_rwb_jobs_for_monitoring()
            if r["id"] in (job_a, job_b)]
    # Different status_code ('cancelled' vs 'pending') sorts by status_code
    # first (alphabetical: 'cancelled' < 'pending'), so job_b comes first
    # here for that reason, not recency — this pins the actual ORDER BY
    # clause (rwb_job_type, status_code, updated_at DESC), not just "newest
    # first" in general.
    assert [r["id"] for r in rows] == [job_b, job_a]


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


# ── monitoring search — submission join via link_type/link_id (CR-04a) ───────

def _edm(*, name="EDM") -> str:
    eid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, source_file_path, name, status, "
        "inserted_at, updated_at) VALUES (:id, :src, :name, 'ready', :now, :now)",
        {"id": eid, "src": r"\\share\intake\x.bak", "name": name,
         "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")
    return eid


def _rdm(*, name="RDM") -> str:
    rid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_rdm (id, source_file_path, name, status, "
        "inserted_at, updated_at) VALUES (:id, :src, :name, 'ready', :now, :now)",
        {"id": rid, "src": r"\\share\intake\x.bak", "name": name,
         "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")
    return rid


def _submission(*, name="Sub", cedant_name="Cedant", status_code="ACTIVE",
                assigned_analyst_id) -> str:
    sid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO submission (id, assigned_analyst_id, name, cedant_name, "
        "treaty_type_code, inception_date, status_code, inserted_at, updated_at) "
        "VALUES (:id, :a, :name, :cedant, 'cat_xol', '2026-01-01', :status, "
        ":now, :now)",
        {"id": sid, "a": assigned_analyst_id, "name": name, "cedant": cedant_name,
         "status": status_code, "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")
    return sid


def _attach_edm(submission_id, edm_id) -> None:
    execute_command(
        "INSERT INTO submission_edm (submission_id, edm_id, inserted_at) "
        "VALUES (:s, :e, :now)",
        {"s": submission_id, "e": edm_id, "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")


def _attach_rdm(submission_id, rdm_id) -> None:
    execute_command(
        "INSERT INTO submission_rdm (submission_id, rdm_id, inserted_at) "
        "VALUES (:s, :r, :now)",
        {"s": submission_id, "r": rdm_id, "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")


def _job_for(*, link_type, link_id, rwb_job_type="backfill_edm_detail") -> str:
    return enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=str(uuid.uuid4()),
        rwb_job_type=rwb_job_type, link_type=link_type, link_id=link_id,
        context_type=link_type if link_type != "not_applicable" else None,
        context_id=link_id)


def test_monitoring_no_filters_returns_every_row(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    edm_id = _edm()
    job_id = _job_for(link_type="edm", link_id=edm_id)
    dummy_id = enqueue_rwb_job(requestor_type="analyst_request",
                              requestor_id=str(uuid.uuid4()),
                              rwb_job_type="dummy_wait", **_NO_LINK)
    ids = {r["id"] for r in list_rwb_jobs_for_monitoring()}
    assert {job_id, dummy_id} <= ids


def test_monitoring_submission_name_matches_via_edm_link(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    user_a, _ = iteration2_db.user_a, iteration2_db.user_b
    edm_id = _edm()
    sub_id = _submission(name="American Family Renewal", assigned_analyst_id=user_a)
    _attach_edm(sub_id, edm_id)
    job_id = _job_for(link_type="edm", link_id=edm_id)

    rows = list_rwb_jobs_for_monitoring(submission_name="american fam")
    assert job_id in {r["id"] for r in rows}


def test_monitoring_submission_name_matches_via_rdm_link(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    user_a = iteration2_db.user_a
    rdm_id = _rdm()
    sub_id = _submission(name="Coastal Re", assigned_analyst_id=user_a)
    _attach_rdm(sub_id, rdm_id)
    job_id = _job_for(link_type="rdm", link_id=rdm_id, rwb_job_type="backfill_rdm_analyses")

    rows = list_rwb_jobs_for_monitoring(submission_name="coastal")
    assert job_id in {r["id"] for r in rows}


def test_monitoring_submission_filter_excludes_not_applicable_link(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    dummy_id = enqueue_rwb_job(requestor_type="analyst_request",
                              requestor_id=str(uuid.uuid4()),
                              rwb_job_type="dummy_wait", **_NO_LINK)
    rows = list_rwb_jobs_for_monitoring(submission_name="anything")
    assert dummy_id not in {r["id"] for r in rows}


def test_monitoring_submission_filter_excludes_edm_with_no_submission(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    edm_id = _edm()  # never attached to any submission
    job_id = _job_for(link_type="edm", link_id=edm_id)
    rows = list_rwb_jobs_for_monitoring(submission_status_codes=["ACTIVE"])
    assert job_id not in {r["id"] for r in rows}


def test_monitoring_no_submission_filter_still_shows_unlinked_jobs(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    dummy_id = enqueue_rwb_job(requestor_type="analyst_request",
                              requestor_id=str(uuid.uuid4()),
                              rwb_job_type="dummy_wait", **_NO_LINK)
    rows = list_rwb_jobs_for_monitoring(rwb_job_types=["dummy_wait"])
    assert dummy_id in {r["id"] for r in rows}


def test_monitoring_submission_status_filter(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    user_a = iteration2_db.user_a
    edm_active = _edm(name="Active EDM")
    edm_done = _edm(name="Completed EDM")
    sub_active = _submission(status_code="ACTIVE", assigned_analyst_id=user_a)
    sub_done = _submission(status_code="COMPLETED", assigned_analyst_id=user_a)
    _attach_edm(sub_active, edm_active)
    _attach_edm(sub_done, edm_done)
    job_active = _job_for(link_type="edm", link_id=edm_active)
    job_done = _job_for(link_type="edm", link_id=edm_done)

    rows = list_rwb_jobs_for_monitoring(submission_status_codes=["COMPLETED"])
    ids = {r["id"] for r in rows}
    assert job_done in ids
    assert job_active not in ids


def test_monitoring_owner_filter(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    user_a, user_b = iteration2_db.user_a, iteration2_db.user_b
    edm_a = _edm(name="A's EDM")
    edm_b = _edm(name="B's EDM")
    sub_a = _submission(assigned_analyst_id=user_a)
    sub_b = _submission(assigned_analyst_id=user_b)
    _attach_edm(sub_a, edm_a)
    _attach_edm(sub_b, edm_b)
    job_a = _job_for(link_type="edm", link_id=edm_a)
    job_b = _job_for(link_type="edm", link_id=edm_b)

    rows = list_rwb_jobs_for_monitoring(owner_ids=[user_a])
    ids = {r["id"] for r in rows}
    assert job_a in ids
    assert job_b not in ids


def test_monitoring_job_type_and_status_filters_independent_of_submission(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    upload_id = enqueue_rwb_job(requestor_type="analyst_request",
                               requestor_id=str(uuid.uuid4()),
                               rwb_job_type="upload_edm", **_NO_LINK)
    backfill_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="backfill_edm_detail", **_NO_LINK)
    rows = list_rwb_jobs_for_monitoring(rwb_job_types=["upload_edm"])
    ids = {r["id"] for r in rows}
    assert upload_id in ids
    assert backfill_id not in ids

    claim_rwb_job(rwb_job_id=backfill_id, worker_id="w1")
    complete_rwb_job(rwb_job_id=backfill_id, status="failed")
    rows = list_rwb_jobs_for_monitoring(status_codes=["failed"])
    ids = {r["id"] for r in rows}
    assert backfill_id in ids
    assert upload_id not in ids


def test_monitoring_returns_linked_entity_name(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    edm_id = _edm(name="Meridian Property")
    job_id = _job_for(link_type="edm", link_id=edm_id)
    rows_by_id = {r["id"]: r for r in list_rwb_jobs_for_monitoring()}
    assert rows_by_id[job_id]["entity_name"] == "Meridian Property"


def test_list_submissions_for_rwb_jobs_resolves_multiple_submissions(iteration2_db):
    from app.services.rwb_job_service import list_submissions_for_rwb_jobs
    user_a = iteration2_db.user_a
    edm_id = _edm()
    sub1 = _submission(name="First Deal", assigned_analyst_id=user_a)
    sub2 = _submission(name="Second Deal", assigned_analyst_id=user_a)
    _attach_edm(sub1, edm_id)
    _attach_edm(sub2, edm_id)

    result = list_submissions_for_rwb_jobs([("edm", edm_id)])
    names = {s["name"] for s in result[("edm", edm_id)]}
    assert names == {"First Deal", "Second Deal"}


def test_list_submissions_for_rwb_jobs_empty_for_unattached_link(iteration2_db):
    from app.services.rwb_job_service import list_submissions_for_rwb_jobs
    edm_id = _edm()
    result = list_submissions_for_rwb_jobs([("edm", edm_id)])
    assert result.get(("edm", edm_id), []) == []


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
    rows_by_id = {r["id"]: r for r in list_rwb_jobs_for_monitoring()}
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
    rows_by_id = {r["id"]: r for r in list_rwb_jobs_for_monitoring()}
    assert rows_by_id[job_id]["is_dead"] == 0


def test_monitoring_pending_and_terminal_rows_are_never_dead(iteration2_db):
    from app.services.rwb_job_service import list_rwb_jobs_for_monitoring
    pending_id = enqueue_rwb_job(requestor_type="analyst_request",
                                 requestor_id=str(uuid.uuid4()),
                                 rwb_job_type="upload_edm", **_NO_LINK)
    rows_by_id = {r["id"]: r for r in list_rwb_jobs_for_monitoring()}
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

    ids = {r["id"] for r in list_rwb_jobs_for_monitoring(status_codes=["dead"])}
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
           list_rwb_jobs_for_monitoring(status_codes=["dead", "failed"])}
    assert dead_id in ids
    assert failed_id in ids
