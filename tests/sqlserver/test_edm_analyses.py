"""Stored broker-analysis reads for a submission-contextual EDM page."""

from __future__ import annotations

import json
import uuid

from app.services import analysis_service, irp_gateway, submission_service
from db import execute_command


def _submission(db, name: str) -> str:
    return submission_service.create_submission(
        name=name, cedant_name=name, treaty_type_code="cat_xol",
        inception_date="2026-01-01", treaty_year=2026,
        actor_id=db.user_a, confirmed=True).submission_id


def _rdm(name: str, irp_id: int) -> str:
    rdm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_rdm (id, name, status, irp_id) "
        "VALUES (:id, :name, 'ready', :irp_id)",
        {"id": rdm_id, "name": name, "irp_id": irp_id},
        connection="WORKBENCH")
    return rdm_id


def _analysis(rdm_id: str, irp_id: str, name: str) -> None:
    execute_command(
        "INSERT INTO irp_analysis "
        "(id, rdm_id, irp_id, name, source_rdm_name, status_code, "
        "settings_metadata) "
        "VALUES (:id, :rdm, :irp, :name, 'source', 'ready', :settings)",
        {"id": str(uuid.uuid4()), "rdm": rdm_id, "irp": irp_id,
         "name": name, "settings": json.dumps({"analysisType": "EP"})},
        connection="WORKBENCH")


def test_submission_rdm_list_has_counts_but_not_analysis_rows(
        iteration2_db, monkeypatch):
    submission_id = _submission(iteration2_db, "Context A")
    included = _rdm("Included RDM", 1001)
    excluded = _rdm("Excluded RDM", 1002)
    execute_command(
        "INSERT INTO submission_rdm (submission_id, rdm_id) VALUES (:s, :r)",
        {"s": submission_id, "r": included}, connection="WORKBENCH")
    _analysis(included, "2001", "Included analysis")
    _analysis(excluded, "2002", "Excluded analysis")
    monkeypatch.setattr(
        irp_gateway, "search_analyses",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Risk Modeler read")))

    groups = analysis_service.list_submission_rdms(submission_id=submission_id)

    assert [(group.rdm_id, group.analysis_count, group.analyses)
            for group in groups] == [(included, 1, [])]


def test_one_submission_rdm_loads_only_its_stored_analyses(iteration2_db):
    first = _submission(iteration2_db, "Context A")
    second = _submission(iteration2_db, "Context B")
    first_rdm = _rdm("First RDM", 1001)
    second_rdm = _rdm("Second RDM", 1002)
    for submission_id, rdm_id in ((first, first_rdm), (second, second_rdm)):
        execute_command(
            "INSERT INTO submission_rdm (submission_id, rdm_id) VALUES (:s, :r)",
            {"s": submission_id, "r": rdm_id}, connection="WORKBENCH")
    _analysis(first_rdm, "2001", "First analysis")
    _analysis(second_rdm, "2002", "Second analysis")

    analyses = analysis_service.list_submission_rdm_analyses(
        submission_id=first, rdm_id=first_rdm)

    assert analyses is not None
    assert [analysis.name for analysis in analyses] == ["First analysis"]
    assert analysis_service.list_submission_rdm_analyses(
        submission_id=first, rdm_id=second_rdm) is None
