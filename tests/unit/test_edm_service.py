"""Unit tests for app/services/edm_service.py (US1, T018).

The request-path contract (FR-042): ``import_edm`` creates the ``irp_edm``
(``pending_import``) and enqueues exactly one ``upload_edm`` head with **no** Risk
Modeler call — the worker submits later. Name collision is a non-blocking warning
(SC-005). Recovery helpers (``retry_import`` / ``replace_source_file``) are idempotent
and concurrency-checked. No function applies row scoping (SC-009).

Runs on the SQLite unit mirror (``iteration2_db``) with the fake IRP.
"""

from __future__ import annotations

import pytest

from app.services import edm_service, rwb_job_service
from app.services.errors import ConcurrencyConflict, InvalidSourceFile
from db import execute_command, execute_one, execute_scalar


def _import(drive, actor, name="Alpha EDM", fname="edm1.bak"):
    return edm_service.import_edm(
        name=name, source_file_path=str(drive / fname), actor_id=actor)


# ── import: entity + one enqueue, no gateway call ─────────────────────────────────

def test_import_creates_pending_edm_and_one_upload_head(iteration2_db, fake_irp, drive):
    res = _import(drive, iteration2_db.user_a)
    edm = edm_service.get_edm(res.entity_id)
    assert edm is not None
    assert edm.status == edm_service.PENDING
    assert edm.source_file_path.endswith("edm1.bak")
    n = execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE requestor_type='analyst_request' "
        "AND requestor_id=:r AND rwb_job_type='upload_edm'",
        {"r": res.entity_id}, connection="WORKBENCH")
    assert n == 1
    assert fake_irp.submits == []  # NO Risk Modeler call on the request path (FR-042)


def test_import_rejects_source_outside_root(iteration2_db, fake_irp, drive):
    with pytest.raises(InvalidSourceFile):
        edm_service.import_edm(name="X", source_file_path="/etc/passwd",
                               actor_id=iteration2_db.user_a)


def test_import_is_idempotent_on_re_enqueue(iteration2_db, fake_irp, drive):
    res = _import(drive, iteration2_db.user_a)
    # The head already exists; a second head for the same edm dedups (UNIQUE key).
    dup = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=res.entity_id,
        rwb_job_type="upload_edm", input_data={})
    assert dup is None


# ── name collision (non-blocking) ────────────────────────────────────────────────

def test_check_name_collision_returns_hits_never_raises(iteration2_db, fake_irp, drive):
    fake_irp.add_edm_name("Dupe")
    assert edm_service.check_name_collision("Dupe") == ["Dupe"]
    assert edm_service.check_name_collision("Fresh") == []
    # import still proceeds despite a collision (warning, not a block).
    res = edm_service.import_edm(name="Dupe", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=iteration2_db.user_a)
    assert res.collision == ["Dupe"]
    assert edm_service.get_edm(res.entity_id) is not None


# ── list: no row scoping ─────────────────────────────────────────────────────────

def test_list_edms_applies_no_scoping(iteration2_db, fake_irp, drive):
    _import(drive, iteration2_db.user_a, name="A", fname="edm1.bak")
    _import(drive, iteration2_db.user_b, name="B", fname="edm2.bak")
    names = {e.name for e in edm_service.list_edms()}
    assert {"A", "B"} <= names  # every EDM visible regardless of actor (SC-009)


# ── recovery: retry + replace-file ───────────────────────────────────────────────

def _fail_head(edm_id):
    """Drive the upload_edm head + entity into the failed/error terminal state."""
    execute_command("UPDATE rwb_job SET status_code='failed' WHERE requestor_id=:r "
                    "AND rwb_job_type='upload_edm'", {"r": edm_id}, connection="WORKBENCH")
    execute_command("UPDATE irp_edm SET status='error' WHERE id=:id",
                    {"id": edm_id}, connection="WORKBENCH")


def test_retry_import_noop_when_in_flight(iteration2_db, fake_irp, drive):
    res = _import(drive, iteration2_db.user_a)  # head pending, edm pending_import
    edm_service.retry_import(edm_id=res.entity_id, actor_id=iteration2_db.user_a)
    row = execute_one("SELECT status_code FROM rwb_job WHERE requestor_id=:r",
                      {"r": res.entity_id}, connection="WORKBENCH")
    assert row["status_code"] == "pending"  # unchanged — still one in-flight head


def test_retry_import_resets_failed_head(iteration2_db, fake_irp, drive):
    res = _import(drive, iteration2_db.user_a)
    _fail_head(res.entity_id)
    edm_service.retry_import(edm_id=res.entity_id, actor_id=iteration2_db.user_a)
    row = execute_one("SELECT status_code FROM rwb_job WHERE requestor_id=:r",
                      {"r": res.entity_id}, connection="WORKBENCH")
    assert row["status_code"] == "pending"  # failed → reset to pending for a re-run


def test_replace_source_file_updates_path_and_reenqueues(iteration2_db, fake_irp, drive):
    res = _import(drive, iteration2_db.user_a)
    _fail_head(res.entity_id)
    edm = edm_service.get_edm(res.entity_id)
    edm_service.replace_source_file(
        edm_id=res.entity_id, new_source_file_path=str(drive / "edm2.bak"),
        expected_updated_at=edm.updated_at, actor_id=iteration2_db.user_a)
    refreshed = edm_service.get_edm(res.entity_id)
    assert refreshed.source_file_path.endswith("edm2.bak")
    assert refreshed.status == edm_service.PENDING
    row = execute_one("SELECT status_code FROM rwb_job WHERE requestor_id=:r",
                      {"r": res.entity_id}, connection="WORKBENCH")
    assert row["status_code"] == "pending"


def test_replace_source_file_stale_marker_conflicts(iteration2_db, fake_irp, drive):
    res = _import(drive, iteration2_db.user_a)
    with pytest.raises(ConcurrencyConflict):
        edm_service.replace_source_file(
            edm_id=res.entity_id, new_source_file_path=str(drive / "edm2.bak"),
            expected_updated_at="1999-01-01 00:00:00", actor_id=iteration2_db.user_a)
