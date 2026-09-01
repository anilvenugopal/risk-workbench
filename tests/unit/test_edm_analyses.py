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
    templates.env.globals["default_perspective"] = (
        analysis_service.DEFAULT_PERSPECTIVE)
    templates.env.globals["default_perspective_label"] = (
        analysis_service.DEFAULT_PERSPECTIVE_LABEL)
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
                   settings=None, submitted=None, job_status="FINISHED",
                   status_code="ready", portfolio_name=None,
                   template_name=None, inserted_by=None) -> str:
    portfolio_id = template_id = None
    if portfolio_name:
        portfolio_id = str(uuid.uuid4())
        execute_command(
            "INSERT INTO irp_portfolio (id, edm_id, name) VALUES (:id, :edm, :n)",
            {"id": portfolio_id, "edm": edm_id, "n": portfolio_name},
            connection="WORKBENCH")
    if template_name:
        template_id = str(uuid.uuid4())
        execute_command(
            "INSERT INTO analysis_template (id, name, analysis_profile_name, "
            "output_profile_name) VALUES (:id, :n, 'Profile', 'Output')",
            {"id": template_id, "n": template_name}, connection="WORKBENCH")
    analysis_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_analysis (id, edm_id, name, full_name, status_code, "
        "irp_id, execution_id, execution_item_no, settings_metadata, "
        "loss_results, submitted_settings, inserted_at, irp_portfolio_id, "
        "analysis_template_id, inserted_by) "
        "VALUES (:id, :edm, :n, :n, :sc, '9001', :x, 0, :sm, :lr, :ss, "
        "'2026-08-26T00:00:00', :p, :t, :by)",
        {"id": analysis_id, "edm": edm_id, "n": name, "x": str(uuid.uuid4()),
         "sc": status_code, "p": portfolio_id, "t": template_id,
         "by": inserted_by,
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
            # the numbers sit on the default perspective (FR-012, D9), which
            # is what the grid's AAL column and the expanded row open on
            "RL": {"aal": 4_100_000.0, "std_dev": 14_900_000.0,
                   "oep": oep, "aep": aep},
            "GR": None, "WX": None, "QS": None, "GU": None,
        },
    }


def test_expanded_row_renders_metadata_results_and_perspective_toggle(
        client, iteration2_db):
    edm_id = _seed_edm()
    _seed_executed(
        edm_id=edm_id, name="CRE_HO_FL_v25 DLM HU", loss_results=_extract(),
        inserted_by=iteration2_db.user_a,
        settings={"analysisType": "Exceedance Probability",
                  "analysisFramework": "ELT", "currencyCode": "USD",
                  "eventRateSchemeNames": [
                      {"id": 0, "code": "0", "name": _LONG_SCHEME}]},
        submitted={"currency": {"code": "USD", "scheme": "RMS",
                               "vintage": "RL25"},
                   "min_loss_threshold": 1.0, "franchise_deductible": False,
                   "treat_construction_occupancy_as_unknown": True})

    html = client.get(f"/edms/{edm_id}/analyses").text

    # the metadata fields carry no group heading, and engine version is left to
    # the table's Engine column (8/26 design session D3)
    assert "Metadata" not in html
    assert "Analysis settings" not in html
    assert "Engine version" not in html
    assert "Condensed results" in html
    assert ">AAL<" in html
    assert "Std dev" in html
    assert "4.1M" in html          # AAL formatted in millions
    # the full analysis name moved out of the condensed grid into the source line
    assert '<b class="row-src__name">CRE_HO_FL_v25 DLM HU</b>' in html
    # a field the origin does not supply is listed, never hidden (FR-022):
    # subperil is absent from the settings payload above
    assert "Subperil" in html
    assert "not returned" in html
    # who submitted the run (D4)
    assert "<dt>Run by</dt>" in html
    assert "Analyst A" in html
    # the fields the condensed grid or the template now says are gone (O-11)
    for label in ("<dt>Peril</dt>", "Analysis template", "<dt>Currency</dt>",
                  "Min loss threshold", "Franchise deductible"):
        assert label not in html
    assert "USD · RMS · RL25" not in html
    # the perspective toggle defaults to Pre-Cat Net (FR-012, D9) and lists
    # all five; the EP-type toggle sits beside it and starts on OEP (D11)
    assert "x-data=\"{ p: 'RL', ep: 'OEP' }\"" in html
    for label in ("Gross", "Pre-Cat Net", "Working Excess",
                  "Quota Share", "Ground Up"):
        assert label in html
    # a long value wraps in CSS; the cell carries the full text as its tooltip
    assert f'title="{_LONG_SCHEME}"' in html
    assert "Treat as unknown" in html


def test_expanded_row_shows_results_pending_while_retrieval_runs(client):
    edm_id = _seed_edm()
    _seed_executed(edm_id=edm_id, name="CRE_HO_GA_v25 DLM HU")

    html = client.get(f"/edms/{edm_id}/analyses").text

    assert "Results pending" in html
    assert "Condensed results" in html  # the block is present, with the state inside


# ── the merged analyses section (spec 011 US3, T031) ──────────────────────────


def _failed_retrieval(analysis_id: str, edm_id: str,
                      detail="RM returned 500 on EP curve"):
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, link_type, "
        "link_id, context_type, context_id, rwb_job_type, "
        "status_code, error_detail) VALUES (:id, 'irp_analysis', :rid, "
        "'edm', :edm, 'irp_analysis', :rid, "
        "'retrieve_analysis_results', 'failed', :detail)",
        {"id": str(uuid.uuid4()), "rid": analysis_id, "edm": edm_id,
         "detail": detail},
        connection="WORKBENCH")


def test_merged_section_columns_and_the_four_aal_states(client):
    edm_id = _seed_edm()
    _seed_executed(edm_id=edm_id, name="With results", loss_results=_extract(),
                   portfolio_name="US Southeast Wind", template_name="HU DLM",
                   settings={"perilCode": "WS", "peril": "Windstorm",
                             "regionCode": "NA", "region": "North America"})
    _seed_executed(edm_id=edm_id, name="Awaiting retrieval")
    failed = _seed_executed(edm_id=edm_id, name="Retrieval failed")
    _failed_retrieval(failed, edm_id)
    _seed_executed(edm_id=edm_id, name="Still running", job_status="RUNNING", status_code="running")

    html = client.get(f"/edms/{edm_id}/analyses").text

    # one column set (FR-010) — no EDM column on the EDM page
    for header in (">Portfolio</span>", ">Template</span>", ">Peril</span>",
                   ">Region</span>", ">Engine</span>", ">Currency</span>",
                   ">AAL &middot; Pre-Cat Net</span>", ">Status</span>",
                   ">Submitted</span>", ">Risk Modeler</span>"):
        assert header in html
    assert ">EDM</span>" not in html
    assert ">Type</span>" not in html            # analysis type moved to the expansion
    # the split name (D4) and the abbreviated peril/region (D2)
    assert 'data-value="US Southeast Wind"' in html
    assert 'data-value="HU DLM"' in html
    assert ">WS<" in html and "Windstorm" not in html
    assert ">NA<" in html and "North America" not in html
    # the AAL cell carries all four results states
    assert 'data-value="4100000.0"' in html and "4.1M" in html   # ready
    assert "retrieving&hellip;" in html                          # pending, run done
    assert "retrieval failed" in html                            # failed + reason
    assert "RM returned 500 on EP curve" in html
    assert html.count('class="aal-state"') == 1  # the running row reads — instead
    # Submitted is UTC in <time data-utc> for the browser sliver (FR-024, T-10)
    assert '<time data-utc="2026-08-26T00:00:00"' in html
    # the copy sliver's hooks (FR-018): the button and the data-value attributes
    assert "data-copy-table" in html
    assert "data-analyses-section" in html


def test_merged_section_status_filter_rides_the_poll_url(client):
    edm_id = _seed_edm()
    _seed_executed(edm_id=edm_id, name="Ready one", loss_results=_extract(),
                   portfolio_name="Ready portfolio")
    _seed_executed(edm_id=edm_id, name="Running one", job_status="RUNNING",
                   status_code="running", portfolio_name="Running portfolio")

    html = client.get(f"/edms/{edm_id}/analyses?status=ready").text

    assert f'hx-get="/edms/{edm_id}/analyses?status=ready"' in html
    assert "Ready portfolio" in html
    assert "Running portfolio" not in html


def _seed_contextual(db) -> tuple[str, str, str]:
    """A submission with one attached EDM and one related RDM."""
    submission_id = _submission(db, "Context A")
    edm_id = _seed_edm("Coastal HO 2026")
    execute_command(
        "INSERT INTO submission_edm (submission_id, edm_id) VALUES (:s, :e)",
        {"s": submission_id, "e": edm_id}, connection="WORKBENCH")
    rdm_id = _rdm("Acme Broker RDM", 4821)
    execute_command(
        "INSERT INTO submission_rdm (submission_id, rdm_id) VALUES (:s, :r)",
        {"s": submission_id, "r": rdm_id}, connection="WORKBENCH")
    return submission_id, edm_id, rdm_id


def test_contextual_merged_section_holds_both_origins(client, iteration2_db):
    submission_id, edm_id, rdm_id = _seed_contextual(iteration2_db)
    _seed_executed(edm_id=edm_id, name="CRE_HO_FL_v25", loss_results=_extract(),
                   portfolio_name="HO FL", template_name="CRE v25")
    _analysis(rdm_id, "88215", "FL HU Gross 2026")

    html = client.get(f"/submissions/{submission_id}/edms/{edm_id}/analyses").text

    # one section: the own row plus the RDM group row, lazy-loading as before
    assert 'id="edm-executed-analyses"' in html
    assert "HO FL" in html and "CRE v25" in html
    assert "Acme Broker RDM" in html
    assert (f'hx-get="/submissions/{submission_id}/edms/{edm_id}'
            f'/rdms/{rdm_id}/analyses"') in html
    assert "Broker analyses" not in html  # the separate section is gone (FR-009)


def test_contextual_rdm_lazy_rows_use_the_merged_columns(client, iteration2_db):
    submission_id, edm_id, rdm_id = _seed_contextual(iteration2_db)
    _analysis(rdm_id, "88215", "FL HU Gross 2026")

    html = client.get(
        f"/submissions/{submission_id}/edms/{edm_id}/rdms/{rdm_id}/analyses").text

    # broker rows tick with data-broker (Delete disables on them) and read
    # Finished; no broker row names a portfolio (FR-020)
    assert 'name="analysis_ids"' in html and "data-broker" in html
    assert ">Finished</span>" in html
    assert "Portfolio" not in html
