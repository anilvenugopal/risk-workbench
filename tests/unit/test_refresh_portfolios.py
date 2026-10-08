"""Unit tests for the ``refresh_portfolios`` worker (spec 207).

A breakout or hazard lookup refreshes the exposure detail of the portfolios it
changed and of no other portfolio. The refresh never prunes, never touches
treaties and never stamps ``irp_edm.as_of``.
"""

from __future__ import annotations

import json

from app.services import edm_service, rwb_job_service
from app.workers import entity_jobs
from db import execute, execute_one
from tests.unit.edm_detail_rows import (
    EXPOSURE_A,
    EXPOSURE_B,
    SUMMARY_A,
    TREATY_CAT,
    edm_ready,
    treaty_rows,
)


def _synced_edm(drive, fake, actor) -> tuple[str, int]:
    """An EDM with portfolios 501 and 502 and one treaty, after its full sync."""
    edm_id = edm_ready(drive, fake, actor)
    exposure_id = fake.edm_exposure_id("EDM")
    fake.add_portfolio(edm_exposure_id=exposure_id, irp_id="501",
                       name="Primary 2026", exposure=EXPOSURE_A)
    fake.add_portfolio(edm_exposure_id=exposure_id, irp_id="502",
                       name="Excess 2026", exposure=EXPOSURE_B)
    fake.add_treaty(edm_exposure_id=exposure_id, irp_id="1042",
                    name="Cat XoL", attributes=TREATY_CAT)
    entity_jobs.run_pending(worker_id="w1")
    return edm_id, exposure_id


def _rows(edm_id: str) -> dict[str, dict]:
    return {r["irp_id"]: r for r in execute(
        "SELECT irp_id, exposure_detail, as_of, deleted_at FROM irp_portfolio "
        "WHERE edm_id=:e", {"e": edm_id}, connection="WORKBENCH")}


def _refresh(edm_id: str, irp_ids: list[str]) -> dict:
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=edm_id,
        rwb_job_type="refresh_portfolios",
        link_type="edm", link_id=edm_id, context_type="edm", context_id=edm_id,
        input_data={"edm_id": edm_id, "portfolio_irp_ids": irp_ids})
    entity_jobs.run_one(rwb_job_id=job_id, rwb_job_type="refresh_portfolios")
    job = execute_one(
        "SELECT status_code, output_data, error_detail FROM rwb_job WHERE id=:i",
        {"i": job_id}, connection="WORKBENCH")
    return {**job, "output": json.loads(job["output_data"] or "{}")}


def _edm_as_of(edm_id: str):
    return execute_one("SELECT as_of FROM irp_edm WHERE id=:i", {"i": edm_id},
                       connection="WORKBENCH")["as_of"]


def _set_exposure(fake, exposure_id, irp_id, exposure) -> None:
    next(p for p in fake._portfolios[str(exposure_id)]
         if p["irp_id"] == irp_id)["exposure"] = exposure


def test_refresh_rewrites_only_the_covered_portfolios(
        iteration2_db, fake_irp, drive):
    edm_id, exposure_id = _synced_edm(drive, fake_irp, iteration2_db.user_a)
    before = _rows(edm_id)
    _set_exposure(fake_irp, exposure_id, "501", dict(EXPOSURE_A, totalLocations=1))
    _set_exposure(fake_irp, exposure_id, "502", dict(EXPOSURE_B, totalLocations=2))

    job = _refresh(edm_id, ["501"])

    after = _rows(edm_id)
    assert json.loads(after["501"]["exposure_detail"])["metrics"]["totalLocations"] == 1
    assert after["502"] == before["502"]
    assert job["status_code"] == "succeeded"
    assert job["output"] == {"portfolios": 1, "covered": ["501"], "summary": "ok"}


def test_refresh_leaves_the_edm_treaties_and_uncovered_portfolios(
        iteration2_db, fake_irp, drive):
    edm_id, exposure_id = _synced_edm(drive, fake_irp, iteration2_db.user_a)
    edm_as_of = _edm_as_of(edm_id)
    treaties = treaty_rows(edm_id)
    # 502 is gone from Risk Modeler and the treaty changed; only a full sync
    # reconciles either.
    fake_irp._portfolios[str(exposure_id)] = [
        p for p in fake_irp._portfolios[str(exposure_id)] if p["irp_id"] != "502"]
    fake_irp._treaties[str(exposure_id)][0]["attributes"] = {"treatyId": 0}

    _refresh(edm_id, ["501"])

    assert _edm_as_of(edm_id) == edm_as_of
    assert treaty_rows(edm_id) == treaties
    assert _rows(edm_id)["502"]["deleted_at"] is None


def test_refresh_skips_a_covered_portfolio_rm_no_longer_returns(
        iteration2_db, fake_irp, drive):
    edm_id, exposure_id = _synced_edm(drive, fake_irp, iteration2_db.user_a)
    before = _rows(edm_id)
    fake_irp._portfolios[str(exposure_id)] = [
        p for p in fake_irp._portfolios[str(exposure_id)] if p["irp_id"] != "502"]

    job = _refresh(edm_id, ["501", "502"])

    assert job["status_code"] == "succeeded"
    assert job["output"]["missing"] == ["502"]
    assert job["output"]["portfolios"] == 1
    assert _rows(edm_id)["502"] == before["502"]


def test_one_failed_read_keeps_its_snapshot_and_the_rest_store(
        iteration2_db, fake_irp, drive):
    edm_id, exposure_id = _synced_edm(drive, fake_irp, iteration2_db.user_a)
    before = _rows(edm_id)
    _set_exposure(fake_irp, exposure_id, "501", dict(EXPOSURE_A, totalLocations=1))
    fake_irp.fail_exposure_for = {"502"}

    job = _refresh(edm_id, ["501", "502"])

    assert job["status_code"] == "succeeded"
    assert job["output"]["exposure_failures"] == ["502"]
    assert job["output"]["portfolios"] == 1
    after = _rows(edm_id)
    assert after["502"] == before["502"]
    assert json.loads(after["501"]["exposure_detail"])["metrics"]["totalLocations"] == 1


def test_every_read_failing_fails_the_job(iteration2_db, fake_irp, drive):
    edm_id, _ = _synced_edm(drive, fake_irp, iteration2_db.user_a)
    fake_irp.fail_exposure_for = {"501", "502"}

    job = _refresh(edm_id, ["501", "502"])

    assert job["status_code"] == "failed"
    assert job["error_detail"]


def test_databridge_failure_stores_a_null_summary_and_succeeds(
        iteration2_db, fake_irp, drive):
    edm_id, _ = _synced_edm(drive, fake_irp, iteration2_db.user_a)
    fake_irp.set_exposure_summary("EDM", {"501": SUMMARY_A})
    fake_irp.raise_on_exposure_summary = True

    job = _refresh(edm_id, ["501"])

    assert job["status_code"] == "succeeded"
    assert job["output"]["summary"] == "unavailable"
    assert json.loads(_rows(edm_id)["501"]["exposure_detail"])["summary"] is None


def test_summary_is_read_for_the_covered_portfolios_only(
        iteration2_db, fake_irp, drive):
    edm_id, _ = _synced_edm(drive, fake_irp, iteration2_db.user_a)
    fake_irp.set_exposure_summary("EDM", {"501": SUMMARY_A, "502": SUMMARY_A})

    _refresh(edm_id, ["501"])

    assert fake_irp.summary_portfolio_ids[-1] == ["501"]
    rows = _rows(edm_id)
    assert json.loads(rows["501"]["exposure_detail"])["summary"] == SUMMARY_A
    assert json.loads(rows["502"]["exposure_detail"])["summary"] is None


def test_missing_edm_and_no_irp_id_skip(iteration2_db, fake_irp, drive):
    res = edm_service.import_edm(name="EDM", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=iteration2_db.user_a)  # no irp_id yet

    for edm_id in (res.entity_id, "00000000-0000-0000-0000-000000000000"):
        job = _refresh(edm_id, ["501"])
        assert job["status_code"] == "succeeded"
        assert job["output"]["skipped"]
    assert _rows(res.entity_id) == {}
