"""Unit tests for the broker-analysis read model and row rendering.

Spec 004 US3 (T035): broker analyses grouped by ``rdm_id`` (an analysis applied
across M EDMs shown ONCE), parsed ``settings_metadata`` (missing/partial →
blank, never error), and ``is_group`` surfaced. No analysis is attributed to a
portfolio (8/4 D8).

Spec 011 US2 (T024): broker rows carry the stored results extract
(``results_state`` / ``results``), a Risk Modeler link, and a Submitted value
from the payload's ``createDate``; the expanded row lists the not-returned
fields (FR-020/FR-022/FR-024/FR-025).
"""

from __future__ import annotations

import json
import uuid

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.config import settings as app_settings
from app.services import analysis_service, edm_service
from app.services._common import SubmissionRef, _utcnow
from db import execute_command

# The documented RM analysis metadata shape (search-analyses / get-analysis —
# IRP knowledge base 2026-07-23): flat camelCase fields.
SETTINGS_FULL = {
    "analysisId": 5521, "analysisName": "Meridian AEP — All Perils",
    "analysisType": "EP", "engineType": "DLM", "engineVersion": "23.0",
    "peril": "Earthquake", "subperil": "Fire Following",
    "region": "North America", "currencyCode": "USD",
    "lineOfBusiness": "Commercial",
    "exposureResourceId": 501, "exposureResourceType": "PORTFOLIO",
}
SETTINGS_PARTIAL = {"analysisType": "EP", "peril": "Wind"}

# The LIVE get-analysis-by-id payload (2026-08-26): currency arrives as an
# OBJECT keyed currencyCode/currencyName, the event-rate scheme as an
# eventRateSchemeNames LIST of {id, code, name} where code is "0" and only name
# carries the scheme, and PLA as the lossAmplification label. The curated view
# must read all three.
SETTINGS_LIVE = {
    "analysisType": "Exceedance Probability", "analysisFramework": "ELT",
    "engineType": "DLM", "engineVersion": "RL23",
    "peril": "Windstorm", "subPeril": "Surge Only",
    "region": "North Atlantic (including Hawaii)",
    "currency": {"currencyName": "US Dollar", "currencyCode": "USD"},
    "lossAmplification": "Building, Contents, BI",
    "eventRateSchemeNames": [
        {"id": 0, "code": "0",
         "name": "RMS 17.0 NA Atten Sensitivity for Lower than Avg Model"}],
    "analysisMode": "Distributed",
    "exposureResourceId": 3, "exposureResourceType": "PORTFOLIO",
}


def _mk(table: str, **cols) -> str:
    row_id = cols.pop("id", str(uuid.uuid4()))
    now = _utcnow()
    keys = ["id", *cols.keys(), "inserted_at", "updated_at"]
    execute_command(
        f"INSERT INTO {table} ({', '.join(keys)}) "
        f"VALUES ({', '.join(':' + k for k in keys)})",
        {"id": row_id, **cols, "inserted_at": now, "updated_at": now},
        connection="WORKBENCH")
    return row_id


def _edm(name: str) -> str:
    return _mk("irp_edm", name=name, status="ready")


def _rdm(name: str, irp_id: int | None = None) -> str:
    return _mk("irp_rdm", name=name, status="ready", irp_id=irp_id)


def _analysis(*, rdm_id: str, edm_id: str, irp_id: str, name: str = "A",
              settings: dict | None = None, is_group: bool = False,
              loss_results: dict | None = None,
              row_id: str | None = None) -> str:
    cols: dict = dict(rdm_id=rdm_id, edm_id=edm_id, irp_id=irp_id,
                      name=name, source_rdm_name="R", status_code="ready",
                      settings_metadata=(json.dumps(settings) if settings
                                         else None),
                      loss_results=(json.dumps(loss_results) if loss_results
                                    else None),
                      is_group=(1 if is_group else 0))
    if row_id is not None:
        cols["id"] = row_id  # pin ORDER BY a.id ties for deterministic tests
    return _mk("irp_analysis", **cols)


def test_settings_metadata_parsed_and_missing_fields_blank_not_error(
        workbench_db):
    rdm, edm = _rdm("R"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1", settings=SETTINGS_FULL)
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="2", settings=SETTINGS_PARTIAL)
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="3", settings=None)  # never backfilled

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    by_irp = {a.irp_id: a for a in g.analyses}

    full = by_irp["1"]
    assert full.settings["engineType"] == "DLM"     # raw snapshot parsed
    assert full.display.analysis_type == "EP"       # curated view model
    assert full.display.engine_type == "DLM"
    assert full.display.engine_version == "23.0"
    assert full.display.peril == "Earthquake"
    assert full.display.peril_secondary == "Fire Following"
    assert full.display.region == "North America"
    assert full.display.currency == "USD"
    assert full.display.line_of_business == "Commercial"

    partial = by_irp["2"]                            # missing fields → blank
    assert partial.display.analysis_type == "EP"
    assert partial.display.engine_type is None
    assert partial.display.currency is None
    assert partial.display.rate_vintage is None

    empty = by_irp["3"]                              # no snapshot → still renders
    assert empty.settings is None
    assert empty.display.analysis_type is None


def test_live_payload_shape_currency_object_rate_list_pla_label(workbench_db):
    rdm, edm = _rdm("R"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1", settings=SETTINGS_LIVE)
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="2",
              settings=dict(SETTINGS_LIVE, eventRateSchemeNames=[]))

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    by_irp = {a.irp_id: a for a in g.analyses}

    a = by_irp["1"]
    assert a.display.currency == "USD"                # object → its code
    assert a.display.pla == "Building, Contents, BI"  # the real label field
    # the scheme NAME, never the "0" code beside it (design session 20 O20-10c)
    assert a.display.event_rate_scheme == (
        "RMS 17.0 NA Atten Sensitivity for Lower than Avg Model")
    assert a.display.peril_secondary == "Surge Only"
    assert a.display.engine == "DLM · RL23"
    assert a.display.analysis_mode == "Distributed"
    assert by_irp["2"].display.event_rate_scheme is None  # empty list → blank


def test_group_analysis_surfaced_as_group(workbench_db):
    rdm, edm = _rdm("R"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="9", is_group=True)

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    assert g.analyses[0].is_group is True


def test_only_broker_rows_of_this_rdm_and_no_deleted(workbench_db):
    rdm, other, edm = _rdm("R"), _rdm("R2"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1")
    _analysis(rdm_id=other, edm_id=edm, irp_id="2")
    deleted = _analysis(rdm_id=rdm, edm_id=edm, irp_id="3")
    execute_command("UPDATE irp_analysis SET deleted_at=:n WHERE id=:i",
                    {"n": _utcnow(), "i": deleted}, connection="WORKBENCH")

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    assert {a.irp_id for a in g.analyses} == {"1"}


# ── spec 011 US2: results fields, RM link, createDate (T024) ────────────────────

_STORED_RPS = (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000)

LOSS_RESULTS = {
    "engine_type": "DLM", "engine_version": "23.0",
    "retrieved_at": "2026-08-26T00:00:00Z",
    "perspectives": {
        "GR": {"aal": 38270.59, "std_dev": 2645726.19,
               "oep": {str(rp): float(rp) for rp in _STORED_RPS},
               "aep": {str(rp): 2.0 * rp for rp in _STORED_RPS}},
        "RL": None, "WX": None, "QS": None, "GU": None,
    },
}


def test_broker_results_read_from_the_stored_extract(workbench_db):
    rdm, edm = _rdm("R"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="1", loss_results=LOSS_RESULTS)

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    [a] = g.analyses

    assert a.results_state == "ready"
    assert [p.code for p in a.results] == ["GR", "RL", "WX", "QS", "GU"]
    gr = a.results[0]
    assert gr.produced and gr.aal == 38270.59 and gr.std_dev == 2645726.19
    assert [r["rp"] for r in gr.rows] == [
        "10,000", "1,000", "500", "250", "100", "50"]  # condensed, largest first
    assert not a.results[1].produced  # explicitly empty → absent, never an error


def test_broker_results_pending_then_failed_with_reason(workbench_db):
    rdm, edm = _rdm("R"), _edm("E")
    pending_id = _analysis(rdm_id=rdm, edm_id=edm, irp_id="1")
    failed_id = _analysis(rdm_id=rdm, edm_id=edm, irp_id="2")
    execute_command(
        "INSERT INTO rwb_job (id, requestor_type, requestor_id, rwb_job_type, "
        "status_code, error_detail) VALUES (:id, 'irp_analysis', :rid, "
        "'retrieve_analysis_results', 'failed', 'results read failed for WX')",
        {"id": str(uuid.uuid4()), "rid": failed_id}, connection="WORKBENCH")

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    by_id = {a.id: a for a in g.analyses}

    assert by_id[pending_id].results_state == "pending"
    assert by_id[pending_id].results_error is None
    assert by_id[failed_id].results_state == "failed"
    assert by_id[failed_id].results_error == "results read failed for WX"


def test_broker_rm_url_and_created_at(workbench_db, monkeypatch):
    monkeypatch.setattr(app_settings, "risk_modeler_base_url",
                        "https://api-euw1.rms-ppe.com/")
    monkeypatch.setattr(app_settings, "risk_modeler_tenant_name", "acme")
    rdm, edm = _rdm("R"), _edm("E")
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="5521",
              settings=dict(SETTINGS_LIVE, appAnalysisId=41867,
                            createDate="2026-08-20T14:02:11.000Z"))
    _analysis(rdm_id=rdm, edm_id=edm, irp_id="5522", settings=None)

    [g] = analysis_service.list_broker_analyses(rdm_id=rdm)
    by_irp = {a.irp_id: a for a in g.analyses}

    # built the same way own rows build theirs (FR-025) — off the snapshot's
    # appAnalysisId, which is what the RM UI route takes
    assert by_irp["5521"].rm_url == (
        "https://acme.rms-ppe.com/riskmodeler/datasources/analysis/41867/0")
    assert by_irp["5521"].created_at == "2026-08-20T14:02:11.000Z"
    assert by_irp["5522"].rm_url is None       # no snapshot → no link
    assert by_irp["5522"].created_at is None   # no snapshot → no Submitted value


# ── row rendering via the contextual lazy route (T024) ──────────────────────────

class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.services.auth_service import CurrentUser
        request.state.user = CurrentUser(
            id="analyst-1", email="analyst@example.com", display_name="Analyst",
            session_id="s", role_codes=["analyst"], is_admin=False,
            must_change_password=False, entra_oid=None, is_active=True)
        return await call_next(request)


def _client() -> TestClient:
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import edms

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    templates.env.globals["default_perspective"] = (
        analysis_service.DEFAULT_PERSPECTIVE)
    templates.env.globals["default_perspective_label"] = (
        analysis_service.DEFAULT_PERSPECTIVE_LABEL)
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(edms.router)
    return TestClient(app, follow_redirects=False)


def _context() -> edm_service.ContextualEdmDetail:
    return edm_service.ContextualEdmDetail(
        edm=edm_service.EdmDetail(
            id="edm-1", name="Shared EDM", status="ready", as_of=None,
            source_file_path="/share/shared.bak", irp_id=101,
            created_by_irp_job_irp_id=None, inserted_at="2026-01-01",
            updated_at="2026-01-01", portfolio_count=0, portfolios=[],
            detail_state="empty"),
        submission=SubmissionRef(id="submission-a", name="Submission A"),
        edm_choices=[SubmissionRef(id="edm-1", name="Shared EDM")],
        rdms=[analysis_service.BrokerAnalysisGroup(
            rdm_id="rdm-1", rdm_name="Acme Broker RDM", rdm_irp_id=201,
            analysis_count=1)],
    )


def _broker_row(**over) -> analysis_service.BrokerAnalysis:
    base = dict(
        id="analysis-1", irp_id="88215", name="Broker AEP", rdm_id="rdm-1",
        rdm_name="Acme Broker RDM",
        rm_url="https://acme.rms-ppe.com/riskmodeler/analyses/88215",
        created_at="2026-08-20T14:02:11.000Z")
    base.update(over)
    return analysis_service.BrokerAnalysis(**base)


def _render_rows(monkeypatch, analyses) -> str:
    monkeypatch.setattr(edm_service, "get_contextual_edm_detail",
                        lambda **kwargs: _context())
    monkeypatch.setattr(analysis_service, "list_submission_rdm_analyses",
                        lambda **kwargs: analyses)
    r = _client().get("/submissions/submission-a/edms/edm-1/rdms/rdm-1/analyses")
    assert r.status_code == 200
    return r.text


def test_broker_row_renders_link_date_and_not_returned_fields(monkeypatch):
    html = _render_rows(monkeypatch, [_broker_row()])

    # Risk Modeler link (its own column since the US3 merged table) and the
    # broker's own run date (FR-024/FR-025)
    assert 'href="https://acme.rms-ppe.com/riskmodeler/analyses/88215"' in html
    assert ">Open ↗</a>" in html
    assert '<time data-utc="2026-08-20T14:02:11.000Z"' in html
    # results still pending
    assert "Results pending — retrieval is queued or running." in html
    # the fields Risk Modeler never returns are listed, not hidden (FR-022) —
    # Run by included: a broker analysis was not run by a workbench analyst
    assert "Unrecognized construction / occupancy" in html
    assert "<dt>Run by</dt>" in html
    assert html.count("not returned") >= 5
    # no broker row names a portfolio (FR-020) — its one name takes both the
    # Portfolio and Template tracks, and the hidden sibling keeps the copied
    # row rectangular against the own rows (D5)
    assert "Portfolio" not in html
    assert 'class="l dt-span2"' in html
    assert "<span hidden></span>" in html


def test_broker_row_renders_ready_results_and_failed_reason(monkeypatch):
    ready = _broker_row(results_state="ready", results=[
        analysis_service.PerspectiveResults(
            code="GR", label="Gross", produced=False),
        analysis_service.PerspectiveResults(
            code="RL", label="Pre-Cat Net", produced=True, aal=1234.0,
            std_dev=99.0,
            rows=[{"rp": "10,000", "oep": 4.0, "aep": 8.0,
                   "oep_display": "4", "aep_display": "8"}]),
    ])
    failed = _broker_row(id="analysis-2", irp_id="88216", name="Broker NT",
                         results_state="failed",
                         results_error="results read failed for WX")

    html = _render_rows(monkeypatch, [ready, failed])

    # one EP type at a time (D11): the header names the selected one and the
    # AEP cells sit behind the toggle, not in a second column
    assert 'x-text="ep">OEP</th>' in html
    assert "x-show=\"ep === 'AEP'\"" in html
    assert "colspan" not in html
    assert ">AAL</td>" in html and ">Std dev</td>" in html
    # the grid's AAL cell reads the default perspective, not the first one (D9)
    assert 'data-value="1234.0"' in html
    assert "The analysis did not produce this perspective." in html
    assert "Results retrieval failed." in html
    assert "results read failed for WX" in html
    assert "Portfolio" not in html
