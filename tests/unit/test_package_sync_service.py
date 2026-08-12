"""Unit tests for app/services/package_sync_service.py (US3, T031).

Save persists members + submits nothing; a member name that already exists in Risk
Modeler BLOCKS the save pre-txn (issue #17 — nothing persisted), and an unreachable
check fails open into ``SaveResult.unchecked_names``. Save-and-Sync enqueues one
``upload_edm`` per EDM (RDM applies arrive via chaining), collision-checks only the
members it will actually (re)submit (a ready member never self-collides), is
idempotent on re-sync (skips ready/in-flight), and rejects a package with no EDM —
every apply targets an EDM (D3), so an RDM-only sync raises ``EmptyPackageError``
(review-only deferred). Editing with a stale marker raises ``ConcurrencyConflict``.
"""

from __future__ import annotations

import pytest

from app.services import name_check
from app.services import package_sync_service as sync
from app.services.errors import (
    ConcurrencyConflict, EmptyPackageError, InvalidMemberName,
    NameCollisionError)
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


# ── save_and_sync: one head per member, idempotent, any package shape ─────────────

def test_sync_enqueues_one_head_per_member(iteration2_db, fake_irp, drive):
    res = sync.save_package(
        package_id=None, name="B",
        members=_members(drive, edms=[("E1", "edm1.bak"), ("E2", "edm2.bak")],
                         rdms=[("R1", "rdm1.mdf")]),
        actor_id=iteration2_db.user_a)
    sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
    # EDM and RDM heads go out together — the RDM never waits on an EDM import.
    assert execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_edm'",
        {}, connection="WORKBENCH") == 2
    assert execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_rdm'",
        {}, connection="WORKBENCH") == 1


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


def test_rdm_only_package_syncs(iteration2_db, fake_irp, drive):
    # Each RDM imports standalone, so a package with no EDM is syncable.
    res = sync.save_package(package_id=None, name="RdmOnly",
                            members=_members(drive, rdms=[("R1", "rdm1.mdf"),
                                                          ("R2", "rdm2.mdf")]),
                            actor_id=iteration2_db.user_a)
    sync.save_and_sync(package_id=res.package_id, actor_id=iteration2_db.user_a)
    assert execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type='upload_rdm'",
        {}, connection="WORKBENCH") == 2
    from app.workers import package_jobs
    package_jobs.run_pending()
    assert execute_scalar(
        "SELECT COUNT(*) FROM irp_job WHERE irp_job_type='import_rdm'",
        {}, connection="WORKBENCH") == 2


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
    (failed upload head, worker framing stripped). Both members submit in the
    same pass now, so a gateway that is down errors both."""
    from app.workers import package_jobs
    a = iteration2_db.user_a
    res = sync.save_package(
        package_id=None, name="P",
        members=_members(drive, edms=[("E1", "edm1.bak")], rdms=[("R1", "rdm1.mdf")]),
        actor_id=a)
    sync.save_and_sync(package_id=res.package_id, actor_id=a)
    fake_irp.raise_on_submit = True
    package_jobs.run_pending()          # both submits fail → both members error

    card = sync.get_package_card(res.package_id)
    assert card.edms[0].status == "error"
    assert card.edms[0].error_detail == "fake IRP: forced submit failure"
    assert card.rdms[0].status == "error"
    assert card.rdms[0].error_detail == "fake IRP: forced submit failure"


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


# ── member name: extension-stripped default + charset/length validation ───────────

@pytest.mark.parametrize("filename, expected", [
    ("PORTFOLIO.BAK", "PORTFOLIO"),        # trailing extension dropped
    ("acme_re-2024.mdf", "acme_re-2024"),  # underscores/hyphens preserved
    ("no_extension", "no_extension"),      # nothing to strip
    ("multi.part.name.bak", "multi.part.name"),  # only the final extension goes
])
def test_default_name_strips_trailing_extension(filename, expected):
    from app.routers.packages import _default_name
    assert _default_name(f"C:\\share\\{filename}") == expected   # Windows path
    assert _default_name(f"/mnt/share/{filename}") == expected    # POSIX path


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
