"""Unit tests for app/services/package_sync_service.py (US3, T031).

Save persists members + runs the collision check + submits nothing. Save-and-Sync
enqueues one ``upload_edm`` per EDM (RDM applies arrive via chaining), is idempotent on
re-sync (skips ready/in-flight), and rejects a package with no EDM — every apply targets
an EDM (D3), so an RDM-only sync raises ``EmptyPackageError`` (review-only deferred).
Editing with a stale marker raises ``ConcurrencyConflict``.
"""

from __future__ import annotations

import pytest

from app.services import package_sync_service as sync
from app.services.errors import ConcurrencyConflict, EmptyPackageError
from db import execute, execute_one, execute_scalar

MS = sync.MemberSpec


def _members(drive, edms=(), rdms=()):
    m = [MS(kind="edm", name=n, source_file_path=str(drive / f)) for n, f in edms]
    m += [MS(kind="rdm", name=n, source_file_path=str(drive / f)) for n, f in rdms]
    return m


# ── save: persist, collision-check, submit nothing ───────────────────────────────

def test_save_persists_members_and_submits_nothing(iteration2_db, fake_irp, drive):
    res = sync.save_package(
        package_id=None, name="Bundle",
        members=_members(drive, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")]),
        actor_id=iteration2_db.user_a)
    assert execute_scalar("SELECT COUNT(*) FROM irp_edm WHERE package_id=:p",
                          {"p": res.package_id}, connection="WORKBENCH") == 1
    assert execute_scalar("SELECT COUNT(*) FROM irp_rdm WHERE package_id=:p",
                          {"p": res.package_id}, connection="WORKBENCH") == 1
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job", {},
                          connection="WORKBENCH") == 0   # nothing enqueued
    assert fake_irp.submits == []                        # no Risk Modeler call


def test_save_returns_per_member_collision_warning(iteration2_db, fake_irp, drive):
    fake_irp.add_edm_name("E1")
    res = sync.save_package(
        package_id=None, name="B",
        members=_members(drive, edms=[("E1", "edm1.bak")]),
        actor_id=iteration2_db.user_a)
    assert len(res.warnings) == 1
    assert res.warnings[0].name == "E1" and res.warnings[0].collision == ["E1"]


def test_save_empty_package_raises(iteration2_db, fake_irp, drive):
    with pytest.raises(EmptyPackageError):
        sync.save_package(package_id=None, name="B", members=[],
                          actor_id=iteration2_db.user_a)


def test_edit_stale_marker_conflicts(iteration2_db, fake_irp, drive):
    res = sync.save_package(package_id=None, name="B",
                            members=_members(drive, edms=[("E1", "edm1.bak")]),
                            actor_id=iteration2_db.user_a)
    with pytest.raises(ConcurrencyConflict):
        sync.save_package(package_id=res.package_id, name="B2", members=[],
                          actor_id=iteration2_db.user_a,
                          expected_updated_at="1999-01-01 00:00:00")


# ── save_and_sync: one upload_edm per EDM, idempotent, review-only ────────────────

def test_sync_enqueues_one_upload_edm_per_edm(iteration2_db, fake_irp, drive):
    res = sync.save_package(
        package_id=None, name="B",
        members=_members(drive, edms=[("E1", "edm1.bak"), ("E2", "edm2.bak")],
                         rdms=[("R1", "rdm1.mdf")]),
        actor_id=iteration2_db.user_a)
    sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
    n = execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_edm'",
                       {}, connection="WORKBENCH")
    assert n == 2  # one per EDM; RDM applies arrive via chaining, not here


def test_resync_is_idempotent(iteration2_db, fake_irp, drive):
    res = sync.save_package(package_id=None, name="B",
                            members=_members(drive, edms=[("E1", "edm1.bak")]),
                            actor_id=iteration2_db.user_a)
    sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
    sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
    n = execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_edm'",
                       {}, connection="WORKBENCH")
    assert n == 1  # re-sync skips the in-flight member (SC-013)


def test_resync_skips_ready_members(iteration2_db, fake_irp, drive):
    res = sync.save_package(package_id=None, name="B",
                            members=_members(drive, edms=[("E1", "edm1.bak")]),
                            actor_id=iteration2_db.user_a)
    sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
    # mark the EDM ready → a re-sync must not re-enqueue it.
    edm_id = execute_one("SELECT id FROM irp_edm WHERE package_id=:p",
                         {"p": res.package_id}, connection="WORKBENCH")["id"]
    from db import execute_command
    execute_command("UPDATE irp_edm SET status='ready' WHERE id=:id",
                    {"id": str(edm_id)}, connection="WORKBENCH")
    execute_command("UPDATE rwb_job SET status_code='succeeded' WHERE requestor_id=:r",
                    {"r": str(edm_id)}, connection="WORKBENCH")
    sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
    still = execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_edm' "
                           "AND status_code='succeeded'", {}, connection="WORKBENCH")
    assert still == 1  # left ready member alone (no reset to pending)


def test_rdm_only_package_sync_rejected(iteration2_db, fake_irp, drive):
    # An RDM-only package can be SAVED, but SYNC requires an EDM (D3 / FR-016).
    res = sync.save_package(package_id=None, name="RdmOnly",
                            members=_members(drive, rdms=[("R1", "rdm1.mdf"),
                                                          ("R2", "rdm2.mdf")]),
                            actor_id=iteration2_db.user_a)
    with pytest.raises(EmptyPackageError):
        sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
    # nothing enqueued — review-only sync is deferred
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job", {},
                          connection="WORKBENCH") == 0


def test_sync_empty_package_raises(iteration2_db, fake_irp, drive):
    # a package whose members were all removed → sync rejects (defensive; SC-012).
    res = sync.save_package(package_id=None, name="B",
                            members=_members(drive, edms=[("E1", "edm1.bak")]),
                            actor_id=iteration2_db.user_a)
    from db import execute_command
    execute_command("UPDATE irp_edm SET deleted_at=:now WHERE package_id=:p",
                    {"now": "2026-01-01 00:00:00", "p": res.package_id},
                    connection="WORKBENCH")
    with pytest.raises(EmptyPackageError):
        sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
