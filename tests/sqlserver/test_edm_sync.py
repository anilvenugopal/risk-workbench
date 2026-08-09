"""Service and worker tests for the manual per-EDM Sync action (spec 004
Addendum A, T056): ``edm_service.sync_detail`` enqueue/revive/guard behavior,
``detail_state`` visibility through both backfill head keys, and the worker's
name resolution for pre-capability EDMs. The route/template contract lives in
``tests/unit/test_edm_sync.py``.
"""

from __future__ import annotations

import uuid

from app.poller import run as poller
from app.services import edm_service
from app.workers import dispatch, package_jobs
from db import execute, execute_command, execute_one
from tests.sqlserver.test_backfill_edm_detail import EXPOSURE_A


def _edm_ready(drive, fake, actor, name="EDM") -> str:
    """Import a standalone EDM and drive it to ``ready`` (submit → FINISHED →
    poll). Leaves the poller-enqueued backfill head PENDING (undrained)."""
    res = edm_service.import_edm(name=name, source_file_path=str(drive / "edm1.bak"),
                                 actor_id=actor)
    package_jobs.run_pending(worker_id="w1")
    row = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_edm_id=:e AND irp_job_type='import_edm'",
        {"e": res.entity_id}, connection="WORKBENCH")
    fake.finish(str(row["irp_id"]))
    poller.poll_once()
    return res.entity_id


def _legacy_edm(*, name="legacy_edm", irp_id=None) -> str:
    """A pre-capability EDM: ``ready``, no backfill head ever enqueued, no
    ``as_of`` — exactly the forward-only gap the Sync button exists for."""
    eid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status, source_file_path, irp_id, "
        "inserted_at, updated_at) VALUES (:i, :n, 'ready', '/share/legacy.bak', "
        ":x, '2026-01-01', '2026-01-01')",
        {"i": eid, "n": name, "x": irp_id}, connection="WORKBENCH")
    return eid


def _analyst_heads(edm_id: str) -> list[dict]:
    return execute(
        "SELECT id, status_code, attempt_count, updated_by FROM rwb_job "
        "WHERE requestor_type='analyst_request' AND requestor_id=:r "
        "AND rwb_job_type='backfill_edm_detail'",
        {"r": edm_id}, connection="WORKBENCH")


# ── sync_detail: enqueue, dispatch, revive, guards ────────────────────────────────

def test_sync_enqueues_analyst_head_and_dispatches(iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")  # drain the poller head → succeeded

    sent: list[tuple[str, str]] = []
    dispatch.configure(lambda *, rwb_job_id, rwb_job_type: sent.append(
        (rwb_job_id, rwb_job_type)))
    try:
        job_id = edm_service.sync_detail(edm_id=edm_id,
                                         actor_id=iteration2_db.user_a)
    finally:
        dispatch.reset()

    assert job_id is not None
    heads = _analyst_heads(edm_id)
    assert len(heads) == 1
    assert heads[0]["status_code"] == "pending"
    assert sent == [(str(job_id), "backfill_edm_detail")]


def test_sync_revives_terminal_head_with_attempt_and_actor(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")
    first = edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")  # analyst head → succeeded

    again = edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_b)
    assert again == first  # the SAME row revived, not a duplicate
    heads = _analyst_heads(edm_id)
    assert len(heads) == 1
    assert heads[0]["status_code"] == "pending"
    assert heads[0]["attempt_count"] == 1
    # .lower(): raw uniqueidentifier reads come back UPPERCASE
    assert str(heads[0]["updated_by"]).lower() == str(iteration2_db.user_b)


def test_sync_skips_while_backfill_in_flight_either_key(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    # The poller-enqueued (irp_job-keyed) head is still pending — Sync must not
    # stack a second concurrent run on top of it.
    assert edm_service.sync_detail(edm_id=edm_id,
                                   actor_id=iteration2_db.user_a) is None
    assert _analyst_heads(edm_id) == []

    package_jobs.run_pending(worker_id="w1")
    assert edm_service.sync_detail(edm_id=edm_id,
                                   actor_id=iteration2_db.user_a) is not None
    # ... and an in-flight analyst head blocks a re-click the same way.
    assert edm_service.sync_detail(edm_id=edm_id,
                                   actor_id=iteration2_db.user_a) is None
    assert len(_analyst_heads(edm_id)) == 1


def test_sync_noop_when_edm_importing_or_missing(iteration2_db, fake_irp, drive):
    res = edm_service.import_edm(name="EDM", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=iteration2_db.user_a)  # pending_import
    assert edm_service.sync_detail(edm_id=res.entity_id,
                                   actor_id=iteration2_db.user_a) is None
    assert _analyst_heads(res.entity_id) == []
    assert edm_service.sync_detail(edm_id=str(uuid.uuid4()),
                                   actor_id=iteration2_db.user_a) is None


# ── detail_state visibility through the analyst-keyed head ────────────────────────

def test_sync_populates_pre_capability_edm_with_visible_states(
        iteration2_db, fake_irp, drive):
    edm_id = _legacy_edm(irp_id=77001)
    fake_irp.add_portfolio(edm_exposure_id=77001, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "unavailable"
    assert detail.sync_running is False

    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "pending"  # analyst-keyed head IS visible
    assert detail.sync_running is True

    package_jobs.run_pending(worker_id="w1")
    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "populated"
    assert detail.sync_running is False
    assert [p.name for p in detail.portfolios] == ["Primary 2026"]


def test_sync_failure_shows_failed_state_and_is_recoverable(
        iteration2_db, fake_irp, drive):
    edm_id = _legacy_edm(irp_id=77002)
    fake_irp.add_portfolio(edm_exposure_id=77002, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.raise_on_list_portfolios = True
    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "failed"
    assert detail.status == edm_service.READY  # FR-005 — never reverted

    fake_irp.raise_on_list_portfolios = False
    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")
    assert edm_service.get_edm_detail(edm_id).detail_state == "populated"


def test_detail_state_prefers_most_recently_updated_head(
        iteration2_db, fake_irp, drive):
    # Poller head succeeded (zero-portfolio EDM → "empty"), then a newer manual
    # sync goes pending — the page must say "pending", not stale "empty".
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")
    assert edm_service.get_edm_detail(edm_id).detail_state == "empty"

    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "pending"
    assert detail.sync_running is True


# ── the Risk Modeler treaties deep link (Treaties polish, 2026-07-24) ─────────────

def test_detail_carries_rm_treaties_deep_link(iteration2_db, monkeypatch):
    # https://<RISK_MODELER_TENANT_NAME>.<rm-domain>/riskmodeler/datasources/
    # <edm-name>/treaties — the RM web UI lives on the TENANT subdomain of the
    # API base URL's domain (rms-ppe.com in the sandbox, rms.com in prod), NOT
    # on the API host itself. The EDM name is URL-encoded; a missing tenant or
    # base URL yields None (link hidden).
    edm_id = _legacy_edm(name="townsend edm")
    monkeypatch.setattr(edm_service.settings, "risk_modeler_base_url",
                        "https://api-euw1.rms-ppe.com/")
    monkeypatch.setattr(edm_service.settings, "risk_modeler_tenant_name", "acme")
    assert edm_service.get_edm_detail(edm_id).rm_treaties_url == (
        "https://acme.rms-ppe.com/riskmodeler/datasources/townsend%20edm/treaties")

    monkeypatch.setattr(edm_service.settings, "risk_modeler_tenant_name", "")
    assert edm_service.get_edm_detail(edm_id).rm_treaties_url is None


# ── worker: pre-capability EDMs without an exposureId (name resolution) ───────────

def test_sync_resolves_missing_exposure_id_by_name(iteration2_db, fake_irp, drive):
    fake_irp.add_edm_name("legacy_named")
    xid = fake_irp.search_edms("legacy_named")[0].irp_id
    fake_irp.add_portfolio(edm_exposure_id=xid, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    edm_id = _legacy_edm(name="legacy_named", irp_id=None)

    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "populated"
    assert detail.irp_id == int(xid)  # exposureId resolved by name AND persisted
    assert len(_analyst_heads(edm_id)) == 1
    assert _analyst_heads(edm_id)[0]["status_code"] == "succeeded"


def test_sync_skips_gracefully_when_name_unresolvable(
        iteration2_db, fake_irp, drive):
    edm_id = _legacy_edm(name="unknown_edm", irp_id=None)  # not in RM at all
    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "unavailable"  # succeeded-as-skip, no as_of
    assert detail.irp_id is None
    assert _analyst_heads(edm_id)[0]["status_code"] == "succeeded"
