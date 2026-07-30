"""Unit tests for app/services/package_service.py (US6).

Structure-only package operations on the SQLite unit tier. Covers the FR-024
≥1-member invariant, member counting across both child tables, member
add/remove with soft-delete-on-empty, and the M:N attach (idempotent + shared
across submissions).

Issue #22 adds the attach/detach guards: ``add_member`` is idempotent on the
(package, member) pair but refuses a member that is soft-deleted, outgoing, or
owned by another package, and ``remove_member`` binds the package in its WHERE so
a mismatched pair can no longer detach the member from its *real* package.
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
    get_packages_for_submission,
    package_member_count,
    remove_member,
    soft_delete_package,
)
from app.services.errors import EmptyPackageError, MemberNotAttachable


def _edm(name="EDM"):
    mid = str(uuid.uuid4())
    execute_command("INSERT INTO irp_edm (id, name) VALUES (:id, :n)",
                    {"id": mid, "n": name}, connection="WORKBENCH")
    return mid


def _rdm(name="RDM"):
    mid = str(uuid.uuid4())
    execute_command("INSERT INTO irp_rdm (id, name) VALUES (:id, :n)",
                    {"id": mid, "n": name}, connection="WORKBENCH")
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


@pytest.mark.parametrize("status", ["delete_pending", "deleted"])
def test_add_member_refuses_an_outgoing_member(iteration1_db, status):
    a = iteration1_db.user_a
    pid = create_package(name="P", edm_ids=[_edm()], actor_id=a)
    leaving = _rdm("Leaving")
    execute_command("UPDATE irp_rdm SET status = :s WHERE id = :id",
                    {"s": status, "id": leaving}, connection="WORKBENCH")
    with pytest.raises(MemberNotAttachable):
        add_member(package_id=pid, member_id=leaving, member_kind="rdm", actor_id=a)
    assert _package_of(leaving, "irp_rdm") is None


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


def test_invalid_member_kind_rejected(iteration1_db):
    with pytest.raises(ValueError):
        add_member(package_id="x", member_id="y", member_kind="bogus",
                   actor_id=iteration1_db.user_a)
