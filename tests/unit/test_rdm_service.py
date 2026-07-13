"""Unit tests for app/services/rdm_service.py (US2, T025).

Applied import fans out one apply per EDM; review-only is a single apply with no EDM
(FR-002/FR-016). Collision warning is non-blocking; ``retry_import`` idempotent;
``list_rdms`` applies no scoping. Runs on the SQLite mirror with the fake IRP; the
worker fan-out is exercised via ``package_jobs.run_pending``.
"""

from __future__ import annotations

from app.services import edm_service, rdm_service
from app.workers import package_jobs
from db import execute, execute_scalar


def _edm(drive, actor, name, fname):
    return edm_service.import_edm(name=name, source_file_path=str(drive / fname),
                                  actor_id=actor).entity_id


def test_applied_import_fans_out_one_apply_per_edm(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    e2 = _edm(drive, a, "E2", "edm2.bak")
    res = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 applied_edm_ids=[e1, e2], actor_id=a)
    # one upload_rdm head enqueued …
    heads = execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE requestor_id=:r AND rwb_job_type='upload_rdm'",
        {"r": res.entity_id}, connection="WORKBENCH")
    assert heads == 1
    package_jobs.run_pending()  # … fanning out to one apply per EDM
    applies = execute(
        "SELECT irp_edm_id FROM irp_job WHERE irp_rdm_id=:r AND irp_job_type='import_rdm'",
        {"r": res.entity_id}, connection="WORKBENCH")
    assert len(applies) == 2
    assert {str(a_["irp_edm_id"]) for a_ in applies} == {e1, e2}
    assert rdm_service.get_rdm(res.entity_id).status == rdm_service.IMPORTING


def test_review_only_import_single_apply_no_edm(iteration2_db, fake_irp, drive):
    res = rdm_service.import_rdm(name="RevOnly", source_file_path=str(drive / "rdm1.mdf"),
                                 applied_edm_ids=[], actor_id=iteration2_db.user_a)
    package_jobs.run_pending()
    applies = execute(
        "SELECT irp_edm_id FROM irp_job WHERE irp_rdm_id=:r AND irp_job_type='import_rdm'",
        {"r": res.entity_id}, connection="WORKBENCH")
    assert len(applies) == 1
    assert applies[0]["irp_edm_id"] is None  # review-only apply carries no EDM (SC-004)


def test_fanout_is_idempotent_per_pair(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    res = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 applied_edm_ids=[e1], actor_id=a)
    package_jobs.run_pending()
    # Re-run the same head body (e.g. reconciler redelivery) → no duplicate apply.
    rdm_service.retry_import(rdm_id=res.entity_id, actor_id=a)  # errored? no — in flight → noop
    package_jobs.run_pending()
    n = execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_rdm_id=:r AND irp_job_type='import_rdm'",
        {"r": res.entity_id}, connection="WORKBENCH")
    assert n == 1


def test_check_name_collision_non_blocking(iteration2_db, fake_irp, drive):
    fake_irp.add_rdm_name("Dupe")
    res = rdm_service.import_rdm(name="Dupe", source_file_path=str(drive / "rdm1.mdf"),
                                 actor_id=iteration2_db.user_a)
    assert res.collision == ["Dupe"]
    assert rdm_service.get_rdm(res.entity_id) is not None


def test_list_rdms_no_scoping(iteration2_db, fake_irp, drive):
    rdm_service.import_rdm(name="RA", source_file_path=str(drive / "rdm1.mdf"),
                           actor_id=iteration2_db.user_a)
    rdm_service.import_rdm(name="RB", source_file_path=str(drive / "rdm2.mdf"),
                           actor_id=iteration2_db.user_b)
    names = {r.name for r in rdm_service.list_rdms()}
    assert {"RA", "RB"} <= names
