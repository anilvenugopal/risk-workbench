"""app/services/edm_service.py (US1, T018) against SQL Server.

The request-path contract (FR-042): ``import_edm`` creates the ``irp_edm``
(``pending_import``) and enqueues exactly one ``upload_edm`` head with **no** Risk
Modeler submit — the worker submits later. Name collision **blocks** the save
(FR-012 as amended by issue #17) via a cached RM read; an unreachable gateway fails
open with ``collision_unchecked``. Recovery helpers (``retry_import`` /
``replace_source_file``) are idempotent and concurrency-checked. No function applies
row scoping (SC-009).

Runs on the SQL Server test database (``iteration2_db``) with the fake IRP.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import (
    edm_service,
    rdm_service,
    rwb_job_service,
    submission_service,
)
from app.services._common import SubmissionRef
from app.services.analysis_service import BrokerAnalysisGroup
from app.services.errors import (
    ConcurrencyConflict,
    InvalidMemberName,
    InvalidSourceFile,
    NameCollisionError,
)
from app.workers import entity_jobs
from db import execute_command, execute_one, execute_scalar


# The name rule ([A-Za-z0-9_-]+, ≤50) is enforced on the standalone import path too
# (review item 3), so the default here must be a valid name — a space would be rejected.
def _import(drive, actor, name="Alpha_EDM", fname="edm1.bak"):
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


@pytest.mark.parametrize("bad_name", ['Alpha EDM', 'a"; DROP--', "x" * 51, "  "])
def test_import_rejects_disallowed_name(iteration2_db, fake_irp, drive, bad_name):
    # Standalone import enforces the entity-name rule ([A-Za-z0-9_-]+,
    # ≤50) so a name with a quote/space can't reach Risk Modeler or a search filter.
    with pytest.raises(InvalidMemberName):
        edm_service.import_edm(name=bad_name, source_file_path=str(drive / "edm1.bak"),
                               actor_id=iteration2_db.user_a)
    # rejected before any entity/head is created
    assert execute_scalar("SELECT COUNT(*) FROM irp_edm", {},
                          connection="WORKBENCH") == 0


def test_import_is_idempotent_on_re_enqueue(iteration2_db, fake_irp, drive):
    res = _import(drive, iteration2_db.user_a)
    # The head already exists; a second head for the same edm dedups (UNIQUE key).
    dup = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=res.entity_id,
        rwb_job_type="upload_edm", input_data={})
    assert dup is None


# ── name collision (blocking, issue #17) ─────────────────────────────────────────

def test_check_name_collision_shapes(iteration2_db, fake_irp, drive):
    fake_irp.add_edm_name("Dupe")
    hit = edm_service.check_name_collision("Dupe")
    assert hit.collides and hit.names == ("Dupe",) and hit.checked
    clean = edm_service.check_name_collision("Fresh")
    assert not clean.collides and clean.checked
    fake_irp.raise_on_search = True
    down = edm_service.check_name_collision("Another")
    assert not down.collides and not down.checked  # fail open, never raises


def test_import_blocks_on_collision(iteration2_db, fake_irp, drive):
    fake_irp.add_edm_name("Dupe")
    with pytest.raises(NameCollisionError):
        edm_service.import_edm(name="Dupe", source_file_path=str(drive / "edm1.bak"),
                               actor_id=iteration2_db.user_a)
    # blocked BEFORE persisting anything — no entity, no upload head
    assert execute_scalar("SELECT COUNT(*) FROM irp_edm", {},
                          connection="WORKBENCH") == 0
    assert execute_scalar("SELECT COUNT(*) FROM rwb_job", {},
                          connection="WORKBENCH") == 0


def test_import_fails_open_when_gateway_down(iteration2_db, fake_irp, drive):
    fake_irp.raise_on_search = True
    res = _import(drive, iteration2_db.user_a)
    assert res.collision_unchecked is True
    assert edm_service.get_edm(res.entity_id) is not None  # save proceeded


# ── backstop surfacing (issue #17 Slice 3) ────────────────────────────────────────

def test_latest_import_error_surfaces_submit_failure(iteration2_db, fake_irp, drive):
    """The worker backstop's specific reason reaches the detail read: the failed
    ``upload_edm`` head's message, worker framing stripped — this is where the
    wheel's "already exist(s)" collision text lands when a fail-open save races
    a real duplicate."""
    res = _import(drive, iteration2_db.user_a)
    fake_irp.raise_on_submit = True
    entity_jobs.run_pending()
    assert edm_service.get_edm(res.entity_id).status == edm_service.ERROR
    assert (edm_service.latest_import_error(res.entity_id)
            == "fake IRP: forced submit failure")
    assert (edm_service.get_edm_detail(res.entity_id).import_error
            == "fake IRP: forced submit failure")


def test_latest_import_error_none_without_failed_head(iteration2_db, fake_irp, drive):
    res = _import(drive, iteration2_db.user_a)   # head still pending
    assert edm_service.latest_import_error(res.entity_id) is None
    assert edm_service.get_edm_detail(res.entity_id).import_error is None


# ── list: no row scoping ─────────────────────────────────────────────────────────

def test_list_edms_applies_no_scoping(iteration2_db, fake_irp, drive):
    _import(drive, iteration2_db.user_a, name="A", fname="edm1.bak")
    _import(drive, iteration2_db.user_b, name="B", fname="edm2.bak")
    names = {e.name for e in edm_service.list_edms()}
    assert {"A", "B"} <= names  # every EDM visible regardless of actor (SC-009)


def test_contextual_detail_validates_association_and_lists_submission_edms(
        iteration2_db):
    first = submission_service.create_submission(
        name="First submission", cedant_name="First", treaty_type_code="cat_xol",
        inception_date="2026-01-01", treaty_year=2026,
        actor_id=iteration2_db.user_a, confirmed=True).submission_id
    second = submission_service.create_submission(
        name="Second submission", cedant_name="Second", treaty_type_code="cat_xol",
        inception_date="2026-01-01", treaty_year=2026,
        actor_id=iteration2_db.user_a, confirmed=True).submission_id
    shared = str(uuid.uuid4())
    other = str(uuid.uuid4())
    for edm_id, name in ((shared, "Shared EDM"), (other, "Other EDM")):
        execute_command(
            "INSERT INTO irp_edm (id, name, status) VALUES (:id, :name, 'ready')",
            {"id": edm_id, "name": name}, connection="WORKBENCH")
    for submission_id in (first, second):
        execute_command(
            "INSERT INTO submission_edm (submission_id, edm_id) VALUES (:s, :e)",
            {"s": submission_id, "e": shared}, connection="WORKBENCH")
    execute_command(
        "INSERT INTO submission_edm (submission_id, edm_id) VALUES (:s, :e)",
        {"s": first, "e": other}, connection="WORKBENCH")

    context = edm_service.get_contextual_edm_detail(
        submission_id=first, edm_id=shared)

    assert context is not None
    assert context.submission.id == first
    assert context.submission.name == "First submission"
    assert context.edm.id == shared
    assert [(choice.id, choice.name) for choice in context.edm_choices] == [
        (shared, "Shared EDM"), (other, "Other EDM")]
    assert edm_service.get_contextual_edm_detail(
        submission_id=second, edm_id=other) is None


def test_contextual_sync_queues_edm_and_each_submission_rdm(monkeypatch):
    context = edm_service.ContextualEdmDetail(
        edm=edm_service.EdmDetail(
            id="edm-1", name="EDM", status="ready", as_of=None,
            source_file_path=None, irp_id=1, created_by_irp_job_irp_id=None,
            inserted_at=None, updated_at=None, portfolio_count=0,
            portfolios=[], detail_state="empty"),
        submission=SubmissionRef(id="submission-1", name="Submission"),
        edm_choices=[],
        rdms=[
            BrokerAnalysisGroup(rdm_id="rdm-1", rdm_name="RDM 1", rdm_irp_id=1),
            BrokerAnalysisGroup(rdm_id="rdm-2", rdm_name="RDM 2", rdm_irp_id=2),
        ])
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        edm_service, "get_contextual_edm_detail", lambda **kwargs: context)
    monkeypatch.setattr(
        edm_service, "sync_detail",
        lambda **kwargs: calls.append(("edm", kwargs["edm_id"])))
    monkeypatch.setattr(
        rdm_service, "sync_detail",
        lambda **kwargs: calls.append(("rdm", kwargs["rdm_id"])))

    exists = edm_service.sync_contextual_detail(
        submission_id="submission-1", edm_id="edm-1", actor_id="analyst-1")

    assert exists is True
    assert calls == [("edm", "edm-1"), ("rdm", "rdm-1"), ("rdm", "rdm-2")]


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
