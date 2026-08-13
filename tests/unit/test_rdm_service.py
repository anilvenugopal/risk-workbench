"""RDM import and RDM-wide analysis capture tests."""

from __future__ import annotations

import json

import pytest

from app.poller import run as poller
from app.services import analysis_service, rdm_service
from app.services.errors import InvalidMemberName, NameCollisionError
from app.workers import entity_jobs
from db import execute, execute_command, execute_scalar


def _import(iteration2_db, drive, *, name: str = "R", source: str = "rdm1.mdf"):
    return rdm_service.import_rdm(
        name=name,
        source_file_path=str(drive / source),
        actor_id=iteration2_db.user_a,
    )


def _finish_import(fake_irp) -> None:
    for row in execute(
        "SELECT irp_id FROM irp_job WHERE irp_job_type='import_rdm'",
        {}, connection="WORKBENCH",
    ):
        fake_irp.finish(str(row["irp_id"]))


def _import_and_backfill(iteration2_db, fake_irp, drive) -> str:
    result = _import(iteration2_db, drive)
    entity_jobs.run_pending()
    _finish_import(fake_irp)
    poller.poll_once()
    entity_jobs.run_pending()
    return result.entity_id


def test_import_submits_one_standalone_rdm(iteration2_db, fake_irp, drive):
    result = _import(iteration2_db, drive)

    assert execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE requestor_id=:r "
        "AND rwb_job_type='upload_rdm'",
        {"r": result.entity_id}, connection="WORKBENCH",
    ) == 1

    entity_jobs.run_pending()

    jobs = execute(
        "SELECT irp_edm_id FROM irp_job WHERE irp_rdm_id=:r "
        "AND irp_job_type='import_rdm'",
        {"r": result.entity_id}, connection="WORKBENCH",
    )
    assert len(jobs) == 1
    assert jobs[0]["irp_edm_id"] is None
    assert fake_irp.submits[-1]["exposure_set_name"] == "R"
    assert rdm_service.get_rdm(result.entity_id).status == rdm_service.IMPORTING


@pytest.mark.parametrize("bad_name", ["Rev Only", 'r\"; DROP--', "r" * 51, "  "])
def test_import_rejects_disallowed_name(
    iteration2_db, fake_irp, drive, bad_name,
):
    with pytest.raises(InvalidMemberName):
        rdm_service.import_rdm(
            name=bad_name,
            source_file_path=str(drive / "rdm1.mdf"),
            actor_id=iteration2_db.user_a,
        )
    assert execute_scalar(
        "SELECT COUNT(*) FROM irp_rdm", {}, connection="WORKBENCH",
    ) == 0


def test_import_worker_is_idempotent(iteration2_db, fake_irp, drive):
    result = _import(iteration2_db, drive)
    entity_jobs.run_pending()
    entity_jobs.run_pending()
    assert execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_rdm_id=:r "
        "AND irp_job_type='import_rdm'",
        {"r": result.entity_id}, connection="WORKBENCH",
    ) == 1


def test_import_blocks_on_collision(iteration2_db, fake_irp, drive):
    fake_irp.add_rdm_name("Dupe")
    with pytest.raises(NameCollisionError):
        rdm_service.import_rdm(
            name="Dupe",
            source_file_path=str(drive / "rdm1.mdf"),
            actor_id=iteration2_db.user_a,
        )
    assert execute_scalar(
        "SELECT COUNT(*) FROM irp_rdm", {}, connection="WORKBENCH",
    ) == 0


def test_import_fails_open_when_gateway_down(iteration2_db, fake_irp, drive):
    fake_irp.raise_on_search = True
    result = _import(iteration2_db, drive)
    assert result.collision_unchecked is True
    assert rdm_service.get_rdm(result.entity_id) is not None


def test_submit_failure_surfaces_import_error(iteration2_db, fake_irp, drive):
    result = _import(iteration2_db, drive)
    fake_irp.raise_on_submit = True
    entity_jobs.run_pending()
    assert rdm_service.get_rdm(result.entity_id).status == rdm_service.ERROR
    assert rdm_service.latest_import_error(result.entity_id) == (
        "fake IRP: forced submit failure"
    )


def test_list_rdms_has_no_row_scope(iteration2_db, fake_irp, drive):
    rdm_service.import_rdm(
        name="RA", source_file_path=str(drive / "rdm1.mdf"),
        actor_id=iteration2_db.user_a,
    )
    rdm_service.import_rdm(
        name="RB", source_file_path=str(drive / "rdm2.mdf"),
        actor_id=iteration2_db.user_b,
    )
    assert {row.name for row in rdm_service.list_rdms()} >= {"RA", "RB"}


def test_finished_import_captures_rdm_wide_analyses(
    iteration2_db, fake_irp, drive,
):
    result = _import(iteration2_db, drive)
    entity_jobs.run_pending()
    fake_irp.add_analysis(
        source_rdm_name="R", exposure_name="E1", analysis_id="900",
        name="Analysis 900",
    )
    fake_irp.add_analysis(
        source_rdm_name="R", exposure_name="E2", analysis_id="901",
        name="Analysis 901",
    )
    _finish_import(fake_irp)
    poller.poll_once()
    entity_jobs.run_pending()

    rows = execute(
        "SELECT irp_id, edm_id, status_code FROM irp_analysis WHERE rdm_id=:r",
        {"r": result.entity_id}, connection="WORKBENCH",
    )
    assert {str(row["irp_id"]) for row in rows} == {"900", "901"}
    assert all(row["edm_id"] is None for row in rows)
    assert all(row["status_code"] == "ready" for row in rows)
    assert rdm_service.get_rdm(result.entity_id).status == rdm_service.READY


def test_backfill_captures_metadata_and_portfolio_pointer(
    iteration2_db, fake_irp, drive,
):
    fake_irp.add_analysis(
        source_rdm_name="R", exposure_name="E1", analysis_id="900", name="AEP",
        metadata={"engineType": "DLM"}, exposure_resource_id="501",
        exposure_resource_type="PORTFOLIO",
    )
    fake_irp.add_analysis(
        source_rdm_name="R", exposure_name="E2", analysis_id="901", name="Treaty",
        exposure_resource_id="1042", exposure_resource_type="TREATY",
    )
    rdm_id = _import_and_backfill(iteration2_db, fake_irp, drive)

    rows = {str(row["irp_id"]): row for row in execute(
        "SELECT irp_id, settings_metadata, exposure_resource_id "
        "FROM irp_analysis WHERE rdm_id=:r",
        {"r": rdm_id}, connection="WORKBENCH",
    )}
    assert json.loads(rows["900"]["settings_metadata"])["engineType"] == "DLM"
    assert rows["900"]["exposure_resource_id"] == "501"
    assert rows["901"]["exposure_resource_id"] is None


def test_failed_metadata_read_keeps_analysis(iteration2_db, fake_irp, drive):
    fake_irp.add_analysis(
        source_rdm_name="R", exposure_name="E1", analysis_id="900", name="AEP",
        exposure_resource_id="501", exposure_resource_type="PORTFOLIO",
    )
    fake_irp.raise_on_analysis_metadata = True
    rdm_id = _import_and_backfill(iteration2_db, fake_irp, drive)
    row = execute(
        "SELECT settings_metadata, exposure_resource_id FROM irp_analysis "
        "WHERE rdm_id=:r", {"r": rdm_id}, connection="WORKBENCH",
    )[0]
    assert row["settings_metadata"] is None
    assert row["exposure_resource_id"] == "501"


def test_backfill_prunes_and_restores_analyses(iteration2_db, fake_irp, drive):
    fake_irp.add_analysis(
        source_rdm_name="R", exposure_name="E1", analysis_id="900", name="A",
    )
    rdm_id = _import_and_backfill(iteration2_db, fake_irp, drive)
    head = execute(
        "SELECT id FROM rwb_job WHERE rwb_job_type='backfill_rdm_analyses'",
        {}, connection="WORKBENCH",
    )[0]["id"]
    saved = list(fake_irp._analyses)

    fake_irp._analyses = []
    assert entity_jobs._backfill_rdm_analyses_body(head)["pruned"] == 1
    assert analysis_service.list_broker_analyses(rdm_id=rdm_id) == []

    fake_irp._analyses = saved
    entity_jobs._backfill_rdm_analyses_body(head)
    rows = execute(
        "SELECT edm_id, deleted_at FROM irp_analysis WHERE rdm_id=:r",
        {"r": rdm_id}, connection="WORKBENCH",
    )
    assert len(rows) == 1
    assert rows[0]["edm_id"] is None
    assert rows[0]["deleted_at"] is None


def test_backfill_is_idempotent(iteration2_db, fake_irp, drive):
    fake_irp.add_analysis(
        source_rdm_name="R", exposure_name="E1", analysis_id="900", name="A",
    )
    rdm_id = _import_and_backfill(iteration2_db, fake_irp, drive)
    head = execute(
        "SELECT id FROM rwb_job WHERE rwb_job_type='backfill_rdm_analyses'",
        {}, connection="WORKBENCH",
    )[0]["id"]
    entity_jobs._backfill_rdm_analyses_body(head)
    assert execute_scalar(
        "SELECT COUNT(*) FROM irp_analysis WHERE rdm_id=:r",
        {"r": rdm_id}, connection="WORKBENCH",
    ) == 1
