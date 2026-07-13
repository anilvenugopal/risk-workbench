"""Unit tests for app/services/package_service.py (US6).

Structure-only package operations on the SQLite unit tier. Covers the FR-024
≥1-member invariant, member counting across both child tables, member
add/remove with soft-delete-on-empty, and the M:N attach (idempotent + shared
across submissions).
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
from app.services.errors import EmptyPackageError


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
