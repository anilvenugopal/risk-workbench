"""Unit tests for the EDM header's parent package and submission links (8/4
D13/CR14): ``get_edm_detail`` carries the package name and every owning
submission (M:N via ``submission_package``, oldest first). A standalone EDM
with no package carries neither."""

from __future__ import annotations

import datetime as dt

from app.services import edm_service, package_service, submission_service
from app.services import package_sync_service as sync

MS = sync.MemberSpec


def _submission(actor, name="Deal", cedant="Cedant X"):
    # distinct cedants — a same-cedant look-alike short-circuits the create
    # with warnings instead of writing (FR-004)
    return submission_service.create_submission(
        name=name, cedant_name=cedant, treaty_type_code="cat_xol",
        inception_date=dt.date(2026, 1, 1), actor_id=actor).submission_id


def _package(drive, actor, submission_id):
    pid = sync.save_package(
        package_id=None, name="Pkg",
        members=[MS(kind="edm", name="E1",
                    source_file_path=str(drive / "edm1.bak"))],
        actor_id=actor).package_id
    package_service.attach_to_submission(
        submission_id=submission_id, package_id=pid, actor_id=actor)
    return pid


def test_detail_carries_package_name_and_owning_submissions(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    first = _submission(a, name="Deal A")
    pid = _package(drive, a, first)
    second = _submission(a, name="Deal B", cedant="Cedant Y")
    package_service.attach_to_submission(
        submission_id=second, package_id=pid, actor_id=a)

    edm = edm_service.list_edms(package_id=pid)[0]
    detail = edm_service.get_edm_detail(edm.id)
    assert detail.package_name == "Pkg"
    # every owning submission, oldest first (8/4 D7 — packages are shared)
    assert [s.name for s in detail.submissions] == ["Deal A", "Deal B"]
    assert [s.id for s in detail.submissions] == [str(first), str(second)]


def test_standalone_edm_has_no_navigation_context(iteration2_db, fake_irp):
    import uuid

    from app.services._common import _utcnow
    from db import execute_command

    edm_id = str(uuid.uuid4())
    now = _utcnow()
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:id, 'EDM', 'ready', :now, :now)",
        {"id": edm_id, "now": now}, connection="WORKBENCH")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.package_name is None
    assert detail.submissions == []
