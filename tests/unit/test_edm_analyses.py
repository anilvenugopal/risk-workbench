"""Stored broker-analysis reads for a submission-contextual EDM page, and the
EDM detail page's Analyses-section fragment (spec 011 US1: the two-column
expanded row with inline condensed results)."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

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
        "(id, rdm_id, irp_id, name, status_code, settings_metadata) "
        "VALUES (:id, :rdm, :irp, :name, 'ready', :settings)",
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


# ── the Analyses section fragment: the spec-011 expanded row (T017) ───────────


@pytest.fixture()
def client(iteration2_db) -> TestClient:
    """The edms router with the fixture's Analyst A logged in
    (test_submission_routes.py pattern)."""
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import edms
    from app.services.auth_service import CurrentUser

    user = CurrentUser(
        id=iteration2_db.user_a, email="analyst.a@example.com",
        display_name="Analyst A", session_id="s", role_codes=["analyst"],
        is_admin=False, must_change_password=False, entra_oid=None,
        is_active=True)

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request.state.user = user
            return await call_next(request)

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(edms.router)
    return TestClient(app, follow_redirects=False)


_LONG_SCHEME = "RMS North Atlantic Hurricane Medium-Term Rates 2025 (LTR blend)"

_ELEVEN = (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000)


def _seed_edm(name="EDM One") -> str:
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status) VALUES (:id, :name, 'ready')",
        {"id": edm_id, "name": name}, connection="WORKBENCH")
    return edm_id


def _seed_executed(*, edm_id: str, name: str, loss_results=None,
                   settings=None, submitted=None, job_status="FINISHED") -> str:
    analysis_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_analysis (id, edm_id, name, full_name, status_code, "
        "irp_id, execution_id, execution_item_no, settings_metadata, "
        "loss_results, submitted_settings, inserted_at) "
        "VALUES (:id, :edm, :n, :n, 'ready', '9001', :x, 0, :sm, :lr, :ss, "
        "'2026-08-26T00:00:00')",
        {"id": analysis_id, "edm": edm_id, "n": name, "x": str(uuid.uuid4()),
         "sm": (json.dumps(settings) if settings else None),
         "lr": (json.dumps(loss_results) if loss_results else None),
         "ss": (json.dumps(submitted) if submitted else None)},
        connection="WORKBENCH")
    execute_command(
        "INSERT INTO irp_job (id, irp_analysis_id, irp_job_type, status, "
        "submission_attempt_count) VALUES (:id, :aid, 'analysis', :s, 0)",
        {"id": str(uuid.uuid4()), "aid": analysis_id, "s": job_status},
        connection="WORKBENCH")
    return analysis_id


def _extract():
    oep = {str(rp): float(rp) * 1_000_000 for rp in _ELEVEN}
    aep = {str(rp): float(rp) * 2_000_000 for rp in _ELEVEN}
    return {
        "engine_type": "DLM", "engine_version": "25.0",
        "retrieved_at": "2026-08-26T00:00:00Z",
        "perspectives": {
            "GR": {"aal": 4_100_000.0, "std_dev": 14_900_000.0,
                   "oep": oep, "aep": aep},
            "RL": None, "WX": None, "QS": None, "GU": None,
        },
    }


def test_expanded_row_renders_groups_results_and_perspective_toggle(client):
    edm_id = _seed_edm()
    _seed_executed(
        edm_id=edm_id, name="CRE_HO_FL_v25 DLM HU", loss_results=_extract(),
        settings={"analysisType": "Exceedance Probability",
                  "analysisFramework": "ELT", "currencyCode": "USD",
                  "eventRateSchemeNames": [_LONG_SCHEME]},
        submitted={"currency": {"code": "USD", "scheme": "RMS",
                               "vintage": "RL25"},
                   "min_loss_threshold": 1.0, "franchise_deductible": False,
                   "treat_construction_occupancy_as_unknown": True})

    html = client.get(f"/edms/{edm_id}/analyses").text

    # both named groups (O-11) and the condensed results block
    assert "Metadata" in html
    assert "Analysis settings" in html
    assert "Condensed results" in html
    assert ">AAL<" in html
    assert "Std dev" in html
    assert "4.1M" in html          # AAL formatted in millions
    # a field the origin does not supply is listed, never hidden (FR-022):
    # subperil is absent from the settings payload above
    assert "Subperil" in html
    assert "not returned" in html
    # the perspective toggle defaults to Gross (FR-012) and lists all five
    assert "x-data=\"{ p: 'GR' }\"" in html
    for label in ("Gross", "Reinsurance Layer", "Working Excess",
                  "Quota Share", "Ground Up"):
        assert label in html
    # a long value wraps in CSS; the cell carries the full text as its tooltip
    assert f'title="{_LONG_SCHEME}"' in html
    # submitted settings render from the snapshot
    assert "USD · RMS · RL25" in html
    assert "Treat as unknown" in html


def test_expanded_row_shows_results_pending_while_retrieval_runs(client):
    edm_id = _seed_edm()
    _seed_executed(edm_id=edm_id, name="CRE_HO_GA_v25 DLM HU")

    html = client.get(f"/edms/{edm_id}/analyses").text

    assert "Results pending" in html
    assert "Condensed results" in html  # the block is present, with the state inside
