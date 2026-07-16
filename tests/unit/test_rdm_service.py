"""Unit tests for app/services/rdm_service.py (US2, T025 + T027a backfill).

Applied import fans out one apply per EDM; **every apply targets an EDM** — a no-EDM
(review-only) import is rejected with ``EmptyPackageError`` (D3 / FR-016). Collision
warning is non-blocking; ``retry_import`` idempotent; ``list_rdms`` applies no scoping.
On ``import_rdm`` FINISHED the poller enqueues ``backfill_rdm_analyses``, whose worker
captures ``irp_analysis`` rows (D2) and rolls the RDM up to ``ready``. Runs on the SQLite
mirror with the fake IRP; the worker fan-out is exercised via ``package_jobs.run_pending``.
"""

from __future__ import annotations

import pytest

from app.poller import run as poller
from app.services import edm_service, rdm_service
from app.services.errors import EmptyPackageError, InvalidMemberName
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


def test_import_with_no_edm_rejected(iteration2_db, fake_irp, drive):
    # Every apply targets an EDM (D3) — a no-EDM import is rejected, nothing persisted.
    with pytest.raises(EmptyPackageError):
        rdm_service.import_rdm(name="RevOnly", source_file_path=str(drive / "rdm1.mdf"),
                               applied_edm_ids=[], actor_id=iteration2_db.user_a)
    assert execute_scalar("SELECT COUNT(*) FROM irp_rdm", {},
                          connection="WORKBENCH") == 0


@pytest.mark.parametrize("bad_name", ['Rev Only', 'r"; DROP--', "r" * 51, "  "])
def test_import_rejects_disallowed_name(iteration2_db, fake_irp, drive, bad_name):
    # Standalone import enforces the same rule as package members ([A-Za-z0-9_-]+, ≤50)
    # so a name with a quote/space can't reach Risk Modeler or a search filter.
    e1 = _edm(drive, iteration2_db.user_a, "E1", "edm1.bak")
    with pytest.raises(InvalidMemberName):
        rdm_service.import_rdm(name=bad_name, source_file_path=str(drive / "rdm1.mdf"),
                               applied_edm_ids=[e1], actor_id=iteration2_db.user_a)
    assert execute_scalar("SELECT COUNT(*) FROM irp_rdm", {},
                          connection="WORKBENCH") == 0


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
    e1 = _edm(drive, iteration2_db.user_a, "E1", "edm1.bak")
    res = rdm_service.import_rdm(name="Dupe", source_file_path=str(drive / "rdm1.mdf"),
                                 applied_edm_ids=[e1], actor_id=iteration2_db.user_a)
    assert res.collision == ["Dupe"]
    assert rdm_service.get_rdm(res.entity_id) is not None


def test_list_rdms_no_scoping(iteration2_db, fake_irp, drive):
    a, b = iteration2_db.user_a, iteration2_db.user_b
    ea = _edm(drive, a, "EA", "edm1.bak")
    eb = _edm(drive, b, "EB", "edm2.bak")
    rdm_service.import_rdm(name="RA", source_file_path=str(drive / "rdm1.mdf"),
                           applied_edm_ids=[ea], actor_id=a)
    rdm_service.import_rdm(name="RB", source_file_path=str(drive / "rdm2.mdf"),
                           applied_edm_ids=[eb], actor_id=b)
    names = {r.name for r in rdm_service.list_rdms()}
    assert {"RA", "RB"} <= names


# ── backfill_rdm_analyses (D2, T027a) ─────────────────────────────────────────────

def _finish_all(fake, job_type):
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type=:t",
                       {"t": job_type}, connection="WORKBENCH"):
        fake.finish(str(row["irp_id"]))


def test_import_rdm_finished_backfills_analyses_and_readies_rdm(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    res = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 applied_edm_ids=[e1], actor_id=a)
    package_jobs.run_pending()                       # submit the import_rdm apply
    fake_irp.add_analysis(source_rdm_name="R", exposure_name="E1", analysis_id="900",
                          name="Analysis 900")
    fake_irp.add_analysis(source_rdm_name="R", exposure_name="E1", analysis_id="901")
    _finish_all(fake_irp, "import_rdm")
    poller.poll_once()                               # enqueue backfill_rdm_analyses head
    assert execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='backfill_rdm_analyses'",
        {}, connection="WORKBENCH") == 1
    package_jobs.run_pending()                        # run the backfill worker

    rows = execute("SELECT irp_id, status_code, deleted_at FROM irp_analysis "
                   "WHERE rdm_id=:r", {"r": res.entity_id}, connection="WORKBENCH")
    assert {str(x["irp_id"]) for x in rows} == {"900", "901"}
    assert all(x["status_code"] == "ready" and x["deleted_at"] is None for x in rows)
    # all applies FINISHED → RDM rolled up to ready (combined rollup)
    assert rdm_service.get_rdm(res.entity_id).status == rdm_service.READY


def test_backfill_is_idempotent(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    res = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 applied_edm_ids=[e1], actor_id=a)
    package_jobs.run_pending()
    fake_irp.add_analysis(source_rdm_name="R", exposure_name="E1", analysis_id="900")
    _finish_all(fake_irp, "import_rdm")
    poller.poll_once()
    package_jobs.run_pending()
    # Re-run the backfill head (reconciler redelivery) → no duplicate irp_analysis row.
    head = execute("SELECT id FROM rwb_job WHERE rwb_job_type='backfill_rdm_analyses'",
                   {}, connection="WORKBENCH")[0]["id"]
    package_jobs.run_one(rwb_job_id=head, rwb_job_type="backfill_rdm_analyses")
    from db import execute_command
    execute_command("UPDATE rwb_job SET status_code='pending' WHERE id=:id",
                    {"id": str(head)}, connection="WORKBENCH")
    package_jobs.run_one(rwb_job_id=head, rwb_job_type="backfill_rdm_analyses")
    assert execute_scalar("SELECT COUNT(*) FROM irp_analysis WHERE rdm_id=:r",
                          {"r": res.entity_id}, connection="WORKBENCH") == 1
