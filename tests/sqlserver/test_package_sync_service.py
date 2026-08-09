"""Tests for app/services/package_sync_service.py (US3, T031).

Covers save (persist members, block on Risk Modeler name collisions pre-txn,
fail open into ``SaveResult.unchecked_names``, member-name validation) and
Save-and-Sync (one ``upload_edm`` per EDM, idempotent re-sync, ready members
never self-collide, RDM-only packages rejected, member error detail on cards).
"""

from __future__ import annotations

import pytest

from app.services import name_check
from app.services import package_sync_service as sync
from app.services.errors import (
    ConcurrencyConflict,
    EmptyPackageError,
    InvalidMemberName,
    NameCollisionError,
)
from db import execute_one, execute_scalar

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


def test_save_blocks_on_collision_and_persists_nothing(iteration2_db, fake_irp, drive):
    fake_irp.add_edm_name("E1")
    before = execute_scalar("SELECT COUNT(*) FROM package", {}, connection="WORKBENCH")
    with pytest.raises(NameCollisionError, match=r"E1 \(EDM\)"):
        sync.save_package(
            package_id=None, name="B",
            members=_members(drive, edms=[("E1", "edm1.bak")],
                             rdms=[("R1", "rdm1.mdf")]),
            actor_id=iteration2_db.user_a)
    # the check runs BEFORE the write txn — a blocked save persists nothing.
    assert execute_scalar("SELECT COUNT(*) FROM package", {},
                          connection="WORKBENCH") == before
    assert execute_scalar("SELECT COUNT(*) FROM irp_edm", {},
                          connection="WORKBENCH") == 0
    assert execute_scalar("SELECT COUNT(*) FROM irp_rdm", {},
                          connection="WORKBENCH") == 0
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job", {},
                          connection="WORKBENCH") == 0


def test_save_blocks_duplicate_names_within_one_batch(
        iteration2_db, fake_irp, drive):
    # Two files auto-named identically in one save: neither exists in RM yet,
    # so the per-name check passes both — but the first submit would create the
    # name and the second would fail minutes later at the worker backstop.
    # The batch itself must block (issue #17's whole point), persisting nothing.
    with pytest.raises(NameCollisionError, match="duplicated in this package"):
        sync.save_package(
            package_id=None, name="B",
            members=_members(drive, edms=[("EXPOSURE", "edm1.bak"),
                                          ("EXPOSURE", "edm2.bak")]),
            actor_id=iteration2_db.user_a)
    assert execute_scalar("SELECT COUNT(*) FROM irp_edm", {},
                          connection="WORKBENCH") == 0


def test_same_name_across_kinds_is_not_a_batch_duplicate(
        iteration2_db, fake_irp, drive):
    # EDM and RDM names live in separate RM namespaces (the check is
    # kind-dispatched) — one save may reuse a name across kinds.
    res = sync.save_package(
        package_id=None, name="B",
        members=_members(drive, edms=[("townsend", "edm1.bak")],
                         rdms=[("townsend", "rdm1.mdf")]),
        actor_id=iteration2_db.user_a)
    assert res.package_id is not None


def test_save_fail_open_returns_unchecked_names(iteration2_db, fake_irp, drive):
    fake_irp.raise_on_search = True
    res = sync.save_package(
        package_id=None, name="B",
        members=_members(drive, edms=[("E1", "edm1.bak")]),
        actor_id=iteration2_db.user_a)
    # RM unreachable → the save goes through, the caller gets the names to warn on.
    assert res.unchecked_names == ["E1"]
    assert execute_scalar("SELECT COUNT(*) FROM irp_edm WHERE package_id=:p",
                          {"p": res.package_id}, connection="WORKBENCH") == 1


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


def test_sync_blocks_when_pending_member_name_got_taken(iteration2_db, fake_irp, drive):
    # save→sync TOCTOU window (issue #17): the name was free at save time but is
    # taken by sync time (cache cleared to mimic TTL expiry).
    res = sync.save_package(package_id=None, name="B",
                            members=_members(drive, edms=[("E1", "edm1.bak")]),
                            actor_id=iteration2_db.user_a)
    fake_irp.add_edm_name("E1")
    name_check.clear_cache()
    with pytest.raises(NameCollisionError, match=r"E1 \(EDM\)"):
        sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job", {},
                          connection="WORKBENCH") == 0  # nothing enqueued


def test_sync_skips_ready_member_self_collision(iteration2_db, fake_irp, drive):
    # A ready EDM's name legitimately exists in RM — it IS that entity. Its
    # "collision" must not block the sync, and the RDM head is still enqueued.
    res = sync.save_package(
        package_id=None, name="B",
        members=_members(drive, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")]),
        actor_id=iteration2_db.user_a)
    edm_id = execute_one("SELECT id FROM irp_edm WHERE package_id=:p",
                         {"p": res.package_id}, connection="WORKBENCH")["id"]
    from db import execute_command
    execute_command("UPDATE irp_edm SET status='ready' WHERE id=:id",
                    {"id": str(edm_id)}, connection="WORKBENCH")
    fake_irp.add_edm_name("E1")
    name_check.clear_cache()
    unchecked = sync.save_and_sync(package_id=res.package_id,
                                   actor_id=iteration2_db.user_a)
    assert unchecked == []
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_rdm'",
                          {}, connection="WORKBENCH") == 1


def test_sync_fail_open_returns_unchecked_names(iteration2_db, fake_irp, drive):
    fake_irp.raise_on_search = True   # failures are never cached → sync re-queries
    res = sync.save_package(package_id=None, name="B",
                            members=_members(drive, edms=[("E1", "edm1.bak")]),
                            actor_id=iteration2_db.user_a)
    unchecked = sync.save_and_sync(package_id=res.package_id,
                                   actor_id=iteration2_db.user_a)
    assert unchecked == ["E1"]
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_edm'",
                          {}, connection="WORKBENCH") == 1  # fail open: still enqueued


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


# ── card backstop surfacing (issue #17 Slice 3) ───────────────────────────────────

def test_card_surfaces_member_error_detail(iteration2_db, fake_irp, drive):
    """An ``error`` member's card carries the specific submit-failure message
    (failed upload head, worker framing stripped); a member that never
    submitted stays ``None``."""
    from app.workers import package_jobs
    a = iteration2_db.user_a
    res = sync.save_package(
        package_id=None, name="P",
        members=_members(drive, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")]),
        actor_id=a)
    sync.save_and_sync(package_id=res.package_id, actor_id=a)
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()          # upload_edm submit fails → EDM error

    card = sync.get_package_card(res.package_id)
    assert card.edms[0].status == "error"
    assert card.edms[0].error_detail == "fake IRP: forced submit failure"
    # the RDM never had a failed analyst-keyed head — nothing to attribute
    assert card.rdms[0].error_detail is None


def test_card_surfaces_rdm_error_detail_from_failed_head(iteration2_db, fake_irp, drive):
    """The RDM arm of the batched read: a failed analyst-keyed ``upload_rdm``
    head (standalone import / member-retry key) surfaces on the card member."""
    from app.services import rwb_job_service
    from db import execute_command
    a = iteration2_db.user_a
    res = sync.save_package(
        package_id=None, name="P",
        members=_members(drive, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")]),
        actor_id=a)
    rid = execute_one("SELECT id FROM irp_rdm WHERE package_id=:p",
                      {"p": res.package_id}, connection="WORKBENCH")["id"]
    rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=rid,
        rwb_job_type="upload_rdm", input_data={})
    execute_command(
        "UPDATE rwb_job SET status_code='failed', "
        "error_detail='upload_rdm submit failed: RDM name taken' "
        "WHERE requestor_id=:r AND rwb_job_type='upload_rdm'",
        {"r": str(rid)}, connection="WORKBENCH")
    execute_command("UPDATE irp_rdm SET status='error' WHERE id=:id",
                    {"id": str(rid)}, connection="WORKBENCH")

    card = sync.get_package_card(res.package_id)
    assert card.rdms[0].error_detail == "RDM name taken"
    assert card.edms[0].error_detail is None   # healthy member untouched


# ── member name: charset/length validation ───────────────────────────────────────

def test_save_rejects_name_with_disallowed_characters(iteration2_db, fake_irp, drive):
    before = execute_scalar("SELECT COUNT(*) FROM package", {}, connection="WORKBENCH")
    with pytest.raises(InvalidMemberName):
        sync.save_package(
            package_id=None, name="B",
            members=_members(drive, edms=[("bad name!", "edm1.bak")]),
            actor_id=iteration2_db.user_a)
    # validation runs before the write txn — nothing is persisted.
    after = execute_scalar("SELECT COUNT(*) FROM package", {}, connection="WORKBENCH")
    assert after == before


def test_save_rejects_overlong_name(iteration2_db, fake_irp, drive):
    with pytest.raises(InvalidMemberName):
        sync.save_package(
            package_id=None, name="B",
            members=_members(drive, edms=[("A" * 51, "edm1.bak")]),
            actor_id=iteration2_db.user_a)


def test_save_accepts_letters_digits_underscore_hyphen(iteration2_db, fake_irp, drive):
    res = sync.save_package(
        package_id=None, name="B",
        members=_members(drive, edms=[("Acme_Re-2024", "edm1.bak")]),
        actor_id=iteration2_db.user_a)
    stored = execute_scalar("SELECT name FROM irp_edm WHERE package_id=:p",
                            {"p": res.package_id}, connection="WORKBENCH")
    assert stored == "Acme_Re-2024"
