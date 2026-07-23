"""Unit tests for the ``backfill_edm_detail`` worker (spec 004 US1, T012).

On ``import_edm`` FINISHED the poller enqueues a ``backfill_edm_detail`` head;
the worker fetches the EDM's portfolios + per-portfolio exposure through the
gateway (fake here — Article 12) and idempotently upserts ``irp_portfolio`` rows
with the JSON snapshot + ``as_of`` (R2). Covers: the happy path, the in-place
overwrite on re-run (no duplicate rows under UNIQUE(edm_id, irp_id)), a gateway
failure failing the ``rwb_job`` while the EDM stays ``ready`` and recoverable
(FR-005), per-portfolio failure isolation, and the graceful skip paths.
"""

from __future__ import annotations

import json

from app.poller import run as poller
from app.services import edm_service, rwb_job_service
from app.workers import package_jobs
from db import execute, execute_one

EXPOSURE_A = {
    "location_count": 8240, "account_count": 1120, "policy_count": 1180,
    "record_volume": 8240,
    "perils": ["WS", "EQ"], "sub_perils": ["storm_surge"],
    "geography": {"regions": ["North America"], "states": ["FL", "TX", "LA"]},
    "currencies": ["USD"],
    "tiv": {"amount": 2.8e9, "currency": "USD"},
}
EXPOSURE_B = {
    "location_count": 3900, "account_count": 720, "policy_count": 760,
    "record_volume": 3900,
    "perils": ["WS", "FL"], "sub_perils": ["storm_surge", "sprinkler_leakage"],
    "geography": {"regions": ["North America"], "states": ["NY", "NJ"]},
    "currencies": ["USD"],
}


def _edm_ready(drive, fake, actor, name="EDM") -> str:
    """Import a standalone EDM and drive it to ``ready`` (submit → FINISHED →
    poll). The poller pass also enqueues the ``backfill_edm_detail`` head; the
    caller seeds fake portfolios (before or after — the worker fetches at run
    time) then drains the queue with ``run_pending``. Returns the edm id."""
    res = edm_service.import_edm(name=name, source_file_path=str(drive / "edm1.bak"),
                                 actor_id=actor)
    package_jobs.run_pending(worker_id="w1")  # submit → irp_job(import_edm, QUEUED)
    row = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_edm_id=:e AND irp_job_type='import_edm'",
        {"e": res.entity_id}, connection="WORKBENCH")
    fake.finish(str(row["irp_id"]))
    poller.poll_once()  # EDM → ready + exposureId; enqueues backfill_edm_detail
    return res.entity_id


def _portfolio_rows(edm_id: str) -> list[dict]:
    return execute(
        "SELECT name, irp_id, exposure_detail, as_of FROM irp_portfolio "
        "WHERE edm_id=:e ORDER BY name",
        {"e": edm_id}, connection="WORKBENCH")


def _backfill_job() -> dict:
    return execute_one(
        "SELECT id, status_code, output_data, error_detail FROM rwb_job "
        "WHERE rwb_job_type='backfill_edm_detail'", {}, connection="WORKBENCH")


def test_backfill_upserts_portfolios_with_snapshot_and_as_of(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="502",
                           name="Excess 2026", exposure=EXPOSURE_B)

    package_jobs.run_pending(worker_id="w1")  # runs backfill_edm_detail

    rows = _portfolio_rows(edm_id)
    assert [r["name"] for r in rows] == ["Excess 2026", "Primary 2026"]
    assert {r["irp_id"] for r in rows} == {"501", "502"}
    by_irp = {r["irp_id"]: r for r in rows}
    assert json.loads(by_irp["501"]["exposure_detail"]) == EXPOSURE_A
    assert json.loads(by_irp["502"]["exposure_detail"]) == EXPOSURE_B
    assert all(r["as_of"] is not None for r in rows)
    # the EDM-level last-synced trust signal is stamped on success (FR-052)
    edm = execute_one("SELECT as_of, status FROM irp_edm WHERE id=:i",
                      {"i": edm_id}, connection="WORKBENCH")
    assert edm["as_of"] is not None
    assert edm["status"] == edm_service.READY
    job = _backfill_job()
    assert job["status_code"] == "succeeded"
    assert json.loads(job["output_data"])["portfolios"] == 2


def test_rerun_overwrites_snapshot_in_place_no_duplicates(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    package_jobs.run_pending(worker_id="w1")
    first = _portfolio_rows(edm_id)
    assert len(first) == 1

    # RM's figures change; a redelivery / reconciler re-run of the SAME job body
    # must overwrite exposure_detail/as_of in place — never insert a duplicate.
    updated = dict(EXPOSURE_A, location_count=9999)
    fake_irp._portfolios[str(exposure_id)][0]["exposure"] = updated
    job = _backfill_job()
    package_jobs._backfill_edm_detail_body(job["id"])

    rows = _portfolio_rows(edm_id)
    assert len(rows) == 1  # UNIQUE(edm_id, irp_id) — no duplicate row
    assert json.loads(rows[0]["exposure_detail"])["location_count"] == 9999


def test_gateway_failure_fails_job_but_edm_stays_ready_and_recoverable(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.raise_on_list_portfolios = True

    package_jobs.run_pending(worker_id="w1")

    job = _backfill_job()
    assert job["status_code"] == "failed"
    assert job["error_detail"]
    assert edm_service.get_edm(edm_id).status == edm_service.READY  # FR-005
    assert _portfolio_rows(edm_id) == []

    # Recoverable: a later re-run of the same body (retry machinery) succeeds
    # and populates the snapshot idempotently.
    fake_irp.raise_on_list_portfolios = False
    package_jobs._backfill_edm_detail_body(job["id"])
    assert len(_portfolio_rows(edm_id)) == 1


def test_one_portfolio_exposure_failure_does_not_abort_the_rest(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    exposure_id = fake_irp.edm_exposure_id("EDM")
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="502",
                           name="Excess 2026", exposure=EXPOSURE_B)
    fake_irp.add_portfolio(edm_exposure_id=exposure_id, irp_id="503",
                           name="Facultative 2026")
    fake_irp.fail_exposure_for = {"502"}

    package_jobs.run_pending(worker_id="w1")

    rows = _portfolio_rows(edm_id)
    assert {r["irp_id"] for r in rows} == {"501", "503"}  # 502 skipped, rest stored
    assert edm_service.get_edm(edm_id).status == edm_service.READY
    job = _backfill_job()
    assert job["status_code"] == "succeeded"  # partial snapshot beats none
    out = json.loads(job["output_data"])
    assert out["portfolios"] == 2
    assert out["exposure_failures"] == 1


def test_missing_edm_and_no_irp_id_skip_gracefully(iteration2_db, fake_irp, drive):
    # An EDM that never finished importing has no irp_id (exposureId) — there is
    # nothing to fetch; the job succeeds as a skip and writes nothing (R7).
    res = edm_service.import_edm(name="EDM", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=iteration2_db.user_a)
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=res.entity_id,
        rwb_job_type="backfill_edm_detail", input_data={"edm_id": res.entity_id})
    package_jobs.run_one(rwb_job_id=job_id, rwb_job_type="backfill_edm_detail")
    assert _portfolio_rows(res.entity_id) == []
    job = execute_one("SELECT status_code FROM rwb_job WHERE id=:i",
                      {"i": job_id}, connection="WORKBENCH")
    assert job["status_code"] == "succeeded"
