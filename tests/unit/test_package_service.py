"""Unit tests for app/services/package_service.py (US6).

Structure-only package operations on the SQLite unit tier. Covers the FR-024
≥1-member invariant, member counting across both child tables, member
add/remove with soft-delete-on-empty, and the M:N attach (idempotent + shared
across submissions).

Issue #22 adds the attach/detach guards, which are deliberately NOT symmetric:

  • ``add_member`` takes ``ready`` and nothing else, on top of refusing a member that
    is soft-deleted or owned by another package. It stays idempotent on the
    (package, member) pair.
  • ``remove_member`` is wider — a ``pending_import`` or ``error`` member must be able
    to leave, or the card's Delete (which removes members FROM Risk Modeler) would be
    the only way out. Only ``importing``/``delete_pending``/``deleted`` are refused.
    It also binds the package in its WHERE, so a mismatched pair can no longer detach
    the member from its *real* package.
"""

from __future__ import annotations

import uuid

import pytest

from db import execute_one, execute_scalar, execute_command
from app.services.package_service import (
    add_member,
    attach_to_submission,
    create_package,
    detach_from_submission,
    get_attachable_packages,
    get_packages_for_submission,
    package_member_count,
    remove_member,
    soft_delete_package,
)
from app.services.errors import EmptyPackageError, MemberNotAttachable


def _edm(name="EDM", status="ready"):
    """``ready`` by default: since issue #22 that is the only status ``add_member``
    accepts, so it is what "an entity you could attach" now means."""
    mid = str(uuid.uuid4())
    execute_command("INSERT INTO irp_edm (id, name, status) VALUES (:id, :n, :s)",
                    {"id": mid, "n": name, "s": status}, connection="WORKBENCH")
    return mid


def _rdm(name="RDM", status="ready"):
    mid = str(uuid.uuid4())
    execute_command("INSERT INTO irp_rdm (id, name, status) VALUES (:id, :n, :s)",
                    {"id": mid, "n": name, "s": status}, connection="WORKBENCH")
    return mid


def _package_of(member_id, table="irp_edm"):
    row = execute_one(f"SELECT package_id FROM {table} WHERE id = :id",
                      {"id": member_id}, connection="WORKBENCH")
    return row["package_id"]


def test_create_package_empty_raises(iteration1_db):
    with pytest.raises(EmptyPackageError):
        create_package(name="P", actor_id=iteration1_db.user_a)


def test_create_package_counts_both_child_tables(iteration1_db):
    pid = create_package(name="Bundle", edm_ids=[_edm(), _edm()], rdm_ids=[_rdm()],
                         actor_id=iteration1_db.user_a)
    assert package_member_count(pid) == 3  # 2 EDM + 1 RDM


def test_remove_last_member_soft_deletes_package(iteration1_db):
    a = iteration1_db.user_a
    e1 = _edm()
    pid = create_package(name="P", edm_ids=[e1], actor_id=a)
    r1 = _rdm()
    add_member(package_id=pid, member_id=r1, member_kind="rdm", actor_id=a)
    assert package_member_count(pid) == 2
    remove_member(package_id=pid, member_id=r1, member_kind="rdm", actor_id=a)
    assert package_member_count(pid) == 1
    row = execute_one("SELECT deleted_at FROM package WHERE id = :id",
                      {"id": pid}, connection="WORKBENCH")
    assert row["deleted_at"] is None  # still has a member — not deleted
    remove_member(package_id=pid, member_id=e1, member_kind="edm", actor_id=a)
    assert package_member_count(pid) == 0
    row = execute_one("SELECT deleted_at FROM package WHERE id = :id",
                      {"id": pid}, connection="WORKBENCH")
    assert row["deleted_at"] is not None  # emptied → soft-deleted (R5)
    # ...but the members themselves are only UNBUNDLED, never deleted (issue #22):
    # detach is bookkeeping, unlike the card's Delete action.
    for mid, table in ((e1, "irp_edm"), (r1, "irp_rdm")):
        row = execute_one(f"SELECT deleted_at FROM {table} WHERE id = :id",
                          {"id": mid}, connection="WORKBENCH")
        assert row["deleted_at"] is None


# ── attach/detach guards (issue #22) ─────────────────────────────────────────────

def test_add_member_attaches_a_standalone_member(iteration1_db):
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    orphan = _rdm("Standalone")
    add_member(package_id=pid, member_id=orphan, member_kind="rdm", actor_id=a)
    assert _package_of(orphan, "irp_rdm") == pid
    assert package_member_count(pid) == 2


def test_add_member_is_idempotent_on_the_same_package(iteration1_db):
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    orphan = _edm("Standalone")
    add_member(package_id=pid, member_id=orphan, member_kind="edm", actor_id=a)
    # a double-submitted picker must not report a phantom failure for a member
    # that IS attached.
    add_member(package_id=pid, member_id=orphan, member_kind="edm", actor_id=a)
    assert package_member_count(pid) == 2


def test_add_member_refuses_a_member_in_another_package(iteration1_db):
    a = iteration1_db.user_a
    owned = _edm("Owned")
    p1 = create_package(name="P1", edm_ids=[owned], actor_id=a)
    p2 = create_package(name="P2", edm_ids=[_edm()], actor_id=a)
    with pytest.raises(MemberNotAttachable):
        add_member(package_id=p2, member_id=owned, member_kind="edm", actor_id=a)
    assert _package_of(owned) == p1     # still where it was


def test_add_member_refuses_a_soft_deleted_member(iteration1_db):
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    gone = _edm("Gone")
    execute_command("UPDATE irp_edm SET deleted_at = :t WHERE id = :id",
                    {"t": "2026-01-01 00:00:00", "id": gone}, connection="WORKBENCH")
    with pytest.raises(MemberNotAttachable):
        add_member(package_id=pid, member_id=gone, member_kind="edm", actor_id=a)


@pytest.mark.parametrize(
    "status", ["pending_import", "importing", "error", "delete_pending", "deleted", None])
def test_add_member_takes_ready_and_nothing_else(iteration1_db, status):
    """``ready`` is the whole rule (``_ATTACHABLE``). An entity that has not finished
    importing is not yet in Risk Modeler under its name, so it is refused rather than
    handled — including a NULL status, which is not evidence of a finished import."""
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    not_ready = _rdm("NotReady", status=status)
    with pytest.raises(MemberNotAttachable):
        add_member(package_id=pid, member_id=not_ready, member_kind="rdm", actor_id=a)
    assert _package_of(not_ready, "irp_rdm") is None


def test_add_member_refuses_an_unknown_member(iteration1_db):
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    with pytest.raises(MemberNotAttachable):
        add_member(package_id=pid, member_id=str(uuid.uuid4()),
                   member_kind="edm", actor_id=a)


def test_remove_member_binds_the_package_in_its_where(iteration1_db):
    """The bug this feature fixes: an unbound WHERE let a mismatched
    (package_id, member_id) pair clear the member's REAL package and then run the
    emptiness check against the wrong package."""
    a = iteration1_db.user_a
    owned = _edm("Owned")
    p1 = create_package(name="P1", edm_ids=[owned], actor_id=a)
    p2 = create_package(name="P2", edm_ids=[_edm()], actor_id=a)
    with pytest.raises(MemberNotAttachable):
        remove_member(package_id=p2, member_id=owned, member_kind="edm", actor_id=a)
    assert _package_of(owned) == p1
    row = execute_one("SELECT deleted_at FROM package WHERE id = :id",
                      {"id": p1}, connection="WORKBENCH")
    assert row["deleted_at"] is None     # p1 was never emptied


def test_remove_member_refuses_a_non_member(iteration1_db):
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    with pytest.raises(MemberNotAttachable):
        remove_member(package_id=pid, member_id=_rdm("Elsewhere"),
                      member_kind="rdm", actor_id=a)


@pytest.mark.parametrize("status", ["pending_import", "error", None])
def test_remove_member_lets_an_unimported_or_failed_member_leave(iteration1_db, status):
    """Detach is deliberately wider than attach (``_UNDETACHABLE``). These members
    cannot be re-attached until they are ``ready``, so refusing to detach them would
    strand them: the only remaining exit would be the card's Delete, which removes
    every member FROM Risk Modeler."""
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm(), _edm("Keeper")], actor_id=a)
    stuck = _rdm("Stuck", status=status)
    add_member(package_id=pid, member_id=_rdm("Ready"), member_kind="rdm", actor_id=a)
    execute_command("UPDATE irp_rdm SET package_id = :p WHERE id = :id",
                    {"p": pid, "id": stuck}, connection="WORKBENCH")

    remove_member(package_id=pid, member_id=stuck, member_kind="rdm", actor_id=a)

    assert _package_of(stuck, "irp_rdm") is None
    row = execute_one("SELECT deleted_at FROM irp_rdm WHERE id = :id",
                      {"id": stuck}, connection="WORKBENCH")
    assert row["deleted_at"] is None      # unbundled, never deleted


@pytest.mark.parametrize("status", ["importing", "delete_pending", "deleted"])
def test_remove_member_refuses_an_in_flight_or_outgoing_member(iteration1_db, status):
    """``importing`` is the interesting one: its import is in flight and the poller
    will still chain this package's RDM applies onto it when the job lands, so letting
    it leave mid-flight would apply the package's RDMs to a non-member."""
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    in_flight = _rdm("InFlight")
    add_member(package_id=pid, member_id=in_flight, member_kind="rdm", actor_id=a)
    execute_command("UPDATE irp_rdm SET status = :s WHERE id = :id",
                    {"s": status, "id": in_flight}, connection="WORKBENCH")

    with pytest.raises(MemberNotAttachable):
        remove_member(package_id=pid, member_id=in_flight, member_kind="rdm", actor_id=a)
    assert _package_of(in_flight, "irp_rdm") == pid


def test_attach_idempotent_and_shared_across_submissions(iteration1_db):
    a = iteration1_db.user_a
    pid = create_package(name="Shared", edm_ids=[_edm()], actor_id=a)
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    attach_to_submission(submission_id=s1, package_id=pid, actor_id=a)
    attach_to_submission(submission_id=s1, package_id=pid, actor_id=a)  # idempotent
    attach_to_submission(submission_id=s2, package_id=pid, actor_id=a)
    n = execute_scalar("SELECT COUNT(*) FROM submission_package WHERE package_id = :p",
                       {"p": pid}, connection="WORKBENCH")
    assert n == 2  # one package attached to two submissions (SC-008)
    for_s1 = get_packages_for_submission(s1)
    assert len(for_s1) == 1 and for_s1[0].member_count == 1


def test_detach_and_soft_delete_hide_from_listing(iteration1_db):
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    s1 = str(uuid.uuid4())
    attach_to_submission(submission_id=s1, package_id=pid, actor_id=a)
    detach_from_submission(submission_id=s1, package_id=pid)
    assert get_packages_for_submission(s1) == []
    s2 = str(uuid.uuid4())
    attach_to_submission(submission_id=s2, package_id=pid, actor_id=a)
    soft_delete_package(package_id=pid, actor_id=a)
    assert get_packages_for_submission(s2) == []  # deleted_at filters it out


def test_get_attachable_packages_offers_unattached_and_other_deal_packages(iteration1_db):
    """The attach picker's candidate rule (D7): a live package not attached to THIS
    submission is offered — whether it belongs to another deal or to none."""
    a = iteration1_db.user_a
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    mine = create_package(name="Mine", edm_ids=[_edm()], actor_id=a)
    attach_to_submission(submission_id=s1, package_id=mine, actor_id=a)
    other_deal = create_package(name="OtherDeal", edm_ids=[_edm()], actor_id=a)
    attach_to_submission(submission_id=s2, package_id=other_deal, actor_id=a)
    library = create_package(name="Library", edm_ids=[_edm()], actor_id=a)
    deleted = create_package(name="Deleted", edm_ids=[_edm()], actor_id=a)
    soft_delete_package(package_id=deleted, actor_id=a)

    candidates = get_attachable_packages(s1)

    assert [c.name for c in candidates] == ["OtherDeal", "Library"]  # inserted_at order
    assert all(c.member_count == 1 for c in candidates)


def test_attach_refuses_a_soft_deleted_package(iteration1_db):
    """Liveness lives in the INSERT predicate: a candidate soft-deleted between the
    picker's render and its submit is a silent no-op, never a row."""
    a = iteration1_db.user_a
    pid = create_package(name="Gone", edm_ids=[_edm()], actor_id=a)
    soft_delete_package(package_id=pid, actor_id=a)
    s1 = str(uuid.uuid4())
    attach_to_submission(submission_id=s1, package_id=pid, actor_id=a)
    n = execute_scalar("SELECT COUNT(*) FROM submission_package WHERE package_id = :p",
                       {"p": pid}, connection="WORKBENCH")
    assert n == 0


def test_detach_from_last_submission_leaves_a_library_package(iteration1_db):
    """No last-submission restriction (product decision, issue #22): a package
    detached from every deal stays live and is offered again by every attach picker."""
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    s1 = str(uuid.uuid4())
    attach_to_submission(submission_id=s1, package_id=pid, actor_id=a)
    detach_from_submission(submission_id=s1, package_id=pid)
    row = execute_one("SELECT deleted_at FROM package WHERE id = :id",
                      {"id": pid}, connection="WORKBENCH")
    assert row["deleted_at"] is None
    assert [c.id for c in get_attachable_packages(s1)] == [pid]


def test_invalid_member_kind_rejected(iteration1_db):
    with pytest.raises(ValueError):
        add_member(package_id="x", member_id="y", member_kind="bogus",
                   actor_id=iteration1_db.user_a)
