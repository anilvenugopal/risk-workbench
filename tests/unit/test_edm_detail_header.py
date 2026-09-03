"""HTTP behavior for direct and submission-contextual EDM detail pages."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import analysis_service, edm_service
from app.services._common import SubmissionRef


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
    from app.templating import TEMPLATE_DIRS

    app = FastAPI()
    templates = Jinja2Templates(directory=TEMPLATE_DIRS)
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


def _edm(edm_id: str = "edm-1", name: str = "Shared EDM") -> edm_service.EdmDetail:
    return edm_service.EdmDetail(
        id=edm_id, name=name, status="ready", as_of=None,
        source_file_path="/share/shared.bak", irp_id=101,
        created_by_irp_job_irp_id=None, inserted_at="2026-01-01",
        updated_at="2026-01-01", portfolio_count=0, portfolios=[],
        detail_state="empty")


def _context() -> edm_service.ContextualEdmDetail:
    return edm_service.ContextualEdmDetail(
        edm=_edm(),
        submission=SubmissionRef(id="submission-a", name="Submission A"),
        edm_choices=[
            SubmissionRef(id="edm-1", name="Shared EDM"),
            SubmissionRef(id="edm-2", name="Other EDM"),
        ],
        rdms=[analysis_service.BrokerAnalysisGroup(
            rdm_id="rdm-1", rdm_name="Submission A RDM", rdm_irp_id=201,
            analysis_count=2)],
    )


def test_contextual_page_names_source_submission_and_preserves_it_in_picker(
        monkeypatch):
    monkeypatch.setattr(edm_service, "get_contextual_edm_detail",
                        lambda **kwargs: _context())

    response = _client().get("/submissions/submission-a/edms/edm-1")

    assert response.status_code == 200
    assert 'href="/submissions/submission-a"' in response.text
    assert "Submission A" in response.text
    assert 'value="/submissions/submission-a/edms/edm-2"' in response.text
    assert 'hx-get="/submissions/submission-a/edms/edm-1/rdms/rdm-1/analyses"' in response.text
    assert "Submission A RDM" in response.text
    assert "Analysis rows load when this RDM opens." in response.text


def test_lazy_broker_rows_land_in_an_unstyled_target(monkeypatch):
    """The swap target must not carry .dtable__groupempty: its caption font,
    tertiary color and 100cqw width would restyle every broker row HTMX drops
    into it, so broker rows would stop matching own rows."""
    monkeypatch.setattr(edm_service, "get_contextual_edm_detail",
                        lambda **kwargs: _context())

    response = _client().get("/submissions/submission-a/edms/edm-1")

    assert '<div id="rdm-analyses-rdm-1">' in response.text


def test_contextual_page_links_to_hidden_notes_between_source_and_rm_id(monkeypatch):
    context = _context()
    context.edm.notes = "Review the treaty mapping."
    monkeypatch.setattr(edm_service, "get_contextual_edm_detail",
                        lambda **kwargs: context)

    response = _client().get("/submissions/submission-a/edms/edm-1")

    source_start = response.text.index("/share/shared.bak")
    link_start = response.text.index(">View Notes</button>")
    rm_id_start = response.text.index("RM EDM #101")
    notes_start = response.text.index('<section class="entity-note"')
    portfolio_start = response.text.index(
        '<span class="sec__title">Portfolios</span>')
    assert source_start < link_start < rm_id_start
    assert notes_start < portfolio_start
    assert 'x-show="notesOpen" x-cloak' in response.text
    assert "Review the treaty mapping." in response.text


def test_contextual_page_rejects_an_unrelated_edm(monkeypatch):
    monkeypatch.setattr(edm_service, "get_contextual_edm_detail",
                        lambda **kwargs: None)

    response = _client().get("/submissions/submission-a/edms/edm-1")

    assert response.status_code == 404


def test_direct_library_page_has_no_submission_context(monkeypatch):
    monkeypatch.setattr(edm_service, "get_edm_detail", lambda edm_id: _edm())

    response = _client().get("/edms/edm-1")

    assert response.status_code == 200
    assert "Submission context" not in response.text
    assert "Broker analyses" not in response.text
    assert "/submissions/submission-a/edms/" not in response.text


def test_detail_renders_note_and_pauses_polling_while_editor_is_open(monkeypatch):
    edm = _edm()
    edm.notes = "Review the treaty mapping."
    edm.sync_running = True
    monkeypatch.setattr(edm_service, "get_edm_detail", lambda edm_id: edm)

    response = _client().get("/edms/edm-1")

    assert "Review the treaty mapping." in response.text
    assert "maxlength=\"250\"" in response.text
    assert "entity-note--editing" in response.text
    assert "!document.querySelector('#edm-detail .entity-note--editing')" in response.text
    assert "!document.querySelector('#edm-detail.edm-notes-open')" in response.text
    # FR-027: Save/Cancel clear the notesOpen gate so the 3s poll resumes.
    assert 'x-on:entity-note-saved="notesOpen = false"' in response.text
    assert ("hx-on::after-request=\"if(event.detail.successful) "
            "htmx.trigger('#entity-note', 'entity-note-saved')\"") in response.text
    assert "$dispatch('entity-note-saved')" in response.text


def test_lazy_route_returns_one_rdms_stored_analysis_rows(monkeypatch):
    analysis = analysis_service.BrokerAnalysis(
        id="analysis-1", irp_id="301", name="Stored analysis", rdm_id="rdm-1",
        rdm_name="Submission A RDM")
    monkeypatch.setattr(edm_service, "get_contextual_edm_detail",
                        lambda **kwargs: _context())
    monkeypatch.setattr(
        analysis_service, "list_submission_rdm_analyses",
        lambda **kwargs: [analysis] if kwargs["rdm_id"] == "rdm-1" else None)

    response = _client().get(
        "/submissions/submission-a/edms/edm-1/rdms/rdm-1/analyses")

    assert response.status_code == 200
    assert "Stored analysis" in response.text
    # the expansion's source line names the RDM (FR-011); nothing else does
    assert 'RDM: <b class="row-src__name">Submission A RDM</b>' in response.text


def test_lazy_route_matches_rdm_id_case_insensitively(monkeypatch):
    analysis = analysis_service.BrokerAnalysis(
        id="analysis-1", irp_id="301", name="Stored analysis", rdm_id="rdm-1",
        rdm_name="Submission A RDM")
    monkeypatch.setattr(edm_service, "get_contextual_edm_detail",
                        lambda **kwargs: _context())
    monkeypatch.setattr(
        analysis_service, "list_submission_rdm_analyses",
        lambda **kwargs: [analysis] if kwargs["rdm_id"] == "RDM-1" else None)

    response = _client().get(
        "/submissions/submission-a/edms/edm-1/rdms/RDM-1/analyses")

    assert response.status_code == 200
    assert "Stored analysis" in response.text


# ── the Analyses section's own 3s poll (spec 010, T-11) ───────────────────────


def _analyses_section(edm_id: str = "edm-1") -> edm_service.EdmAnalysesSection:
    live = analysis_service.ExecutedAnalysis(
        id="analysis-1", name="CRE_Portfolio A_Template A",
        full_name="CRE_Portfolio A_Template A", portfolio_name="Portfolio A",
        status_code="pending", failure_reason=None, template_name="Template A",
        job_status="QUEUED")
    return edm_service.EdmAnalysesSection(id=edm_id, executed_analyses=[live])


def test_analyses_poll_renders_the_section_without_the_rest_of_the_page(monkeypatch):
    monkeypatch.setattr(edm_service, "get_edm_analyses",
                        lambda **kwargs: _analyses_section())

    response = _client().get("/edms/edm-1/analyses")

    assert response.status_code == 200
    assert 'hx-get="/edms/edm-1/analyses"' in response.text
    assert "every 3s" in response.text  # a pending row keeps the poll running
    assert "CRE_Portfolio A_Template A" in response.text
    assert "Portfolios" not in response.text  # section only, never the detail body


def test_contextual_analyses_poll_keeps_the_submission_in_its_own_url(monkeypatch):
    captured = {}

    def _section(**kwargs):
        captured.update(kwargs)
        return _analyses_section()

    monkeypatch.setattr(edm_service, "get_edm_analyses", _section)

    response = _client().get("/submissions/submission-a/edms/edm-1/analyses")

    assert response.status_code == 200
    assert captured == {"edm_id": "edm-1", "submission_id": "submission-a"}
    assert 'hx-get="/submissions/submission-a/edms/edm-1/analyses"' in response.text


def test_contextual_analyses_poll_stops_when_the_edm_leaves_the_submission(monkeypatch):
    monkeypatch.setattr(edm_service, "get_edm_analyses", lambda **kwargs: None)

    response = _client().get("/submissions/submission-a/edms/edm-1/analyses")

    assert response.status_code == 200
    assert "no longer related to the submission" in response.text
    assert "hx-trigger" not in response.text  # a terminal notice ends the poll


def test_empty_analyses_poll_tracks_the_selected_execution(monkeypatch):
    empty = edm_service.EdmAnalysesSection(id="edm-1", executed_analyses=[])
    monkeypatch.setattr(edm_service, "get_edm_analyses", lambda **kwargs: empty)
    state = {"live": True}
    monkeypatch.setattr(
        analysis_service, "execution_batch_is_live",
        lambda execution_id: execution_id == "execution-1" and state["live"])

    pending = _client().get(
        "/edms/edm-1/analyses?status=ready&execution_id=execution-1")
    assert "No analyses executed" in pending.text
    assert "every 3s" in pending.text
    assert "execution_id=execution-1" in pending.text
    assert "status=ready" in pending.text

    state["live"] = False
    terminal = _client().get(
        "/edms/edm-1/analyses?status=ready&execution_id=execution-1")
    assert "every 3s" not in terminal.text
    assert "execution_id=execution-1" in terminal.text


def test_successful_execute_response_carries_execution_id(monkeypatch):
    from app.auth.csrf import generate_csrf_token
    from app.services import analysis_execution_service

    monkeypatch.setattr(
        analysis_execution_service, "request_execution",
        lambda **kwargs: "execution-1")
    response = _client().post(
        "/edms/edm-1/execute",
        data={"csrf_token": generate_csrf_token(), "kind": "template",
              "portfolio_ids": "portfolio-1", "template_ids": "template-1"},
        headers={"HX-Request": "true"})

    assert response.status_code == 204
    event = json.loads(response.headers["HX-Trigger"])["execution-submitted"]
    assert event == {"execution_id": "execution-1"}


def _stub_execute_modal(monkeypatch):
    """Enough reference data for the modal to render its form rather than one of
    _execute_context's blocking messages."""
    from app.services import (
        analysis_execution_service,
        portfolio_service,
        template_service,
        treaty_service,
    )

    monkeypatch.setattr(edm_service, "get_contextual_edm_detail",
                        lambda **kwargs: _context())
    monkeypatch.setattr(edm_service, "get_edm", lambda edm_id: _edm())
    monkeypatch.setattr(portfolio_service, "list_portfolios", lambda **kwargs: [
        portfolio_service.PortfolioRow(
            id="portfolio-1", edm_id="edm-1", name="US Wind", irp_id="9",
            exposure_detail=None, as_of=None)])
    monkeypatch.setattr(template_service, "list_templates", lambda **kwargs: [
        {"id": "template-1", "name": "US HU DLM"}])
    monkeypatch.setattr(treaty_service, "list_treaties", lambda **kwargs: [])
    monkeypatch.setattr(analysis_execution_service, "currency_defaults",
                        lambda: {"code": "USD", "scheme": "RMS", "vintage": ""})
    monkeypatch.setattr(analysis_execution_service, "currency_options",
                        lambda: [{"code": "USD", "name": "US Dollar"}])
    monkeypatch.setattr(analysis_execution_service, "currency_scheme_options",
                        lambda: [{"code": "RMS", "name": "RMS"}])
    monkeypatch.setattr(analysis_execution_service, "vintage_options",
                        lambda scheme: [])


def test_contextual_execute_modal_posts_back_to_its_submission_url(monkeypatch):
    _stub_execute_modal(monkeypatch)

    response = _client().get(
        "/submissions/submission-a/edms/edm-1/execute"
        "?kind=template&portfolio_ids=portfolio-1")

    assert response.status_code == 200
    assert ('action="/submissions/submission-a/edms/edm-1/execute"'
            in response.text)
    assert 'action="/edms/edm-1/execute"' not in response.text


def test_direct_execute_modal_posts_back_to_the_library_url(monkeypatch):
    _stub_execute_modal(monkeypatch)

    response = _client().get(
        "/edms/edm-1/execute?kind=template&portfolio_ids=portfolio-1")

    assert response.status_code == 200
    assert 'action="/edms/edm-1/execute"' in response.text
    assert "/submissions/" not in response.text


def test_execute_gate_failure_re_renders_the_modal_on_the_same_url(monkeypatch):
    from app.auth.csrf import generate_csrf_token
    from app.services import analysis_execution_service

    _stub_execute_modal(monkeypatch)

    def _refuse(**kwargs):
        raise analysis_execution_service.ExecutionGateError(
            ["Choose at least one portfolio."])

    monkeypatch.setattr(analysis_execution_service, "request_execution", _refuse)

    response = _client().post(
        "/submissions/submission-a/edms/edm-1/execute",
        data={"csrf_token": generate_csrf_token(), "kind": "template",
              "portfolio_ids": "portfolio-1"},
        headers={"HX-Request": "true"})

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#execute-modal"
    assert "Choose at least one portfolio." in response.text
    assert ('action="/submissions/submission-a/edms/edm-1/execute"'
            in response.text)
