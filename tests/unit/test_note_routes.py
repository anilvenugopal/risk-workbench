"""HTTP contracts for EDM and RDM note updates."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.auth.csrf import generate_csrf_token
from app.config import settings
from app.routers import edms, rdms
from app.services import edm_service, entity_note_service, rdm_service
from app.services.errors import NoteConflict


class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.services.auth_service import CurrentUser
        request.state.user = CurrentUser(
            id="analyst-1", email="analyst@example.com", display_name="Analyst",
            session_id="s", role_codes=["analyst"], is_admin=False,
            must_change_password=False, entra_oid=None, is_active=True)
        return await call_next(request)


def _client() -> TestClient:
    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals.update(
        app_env=settings.app_env,
        password_auth_enabled=settings.password_auth_enabled,
        oidc_auth_enabled=settings.oidc_auth_enabled,
        generate_csrf_token=generate_csrf_token,
    )
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(edms.router)
    app.include_router(rdms.router)
    return TestClient(app, follow_redirects=False)


def _edm(notes: str | None = None) -> edm_service.EdmDetail:
    return edm_service.EdmDetail(
        id="edm-1", name="EDM", status="ready", as_of=None,
        source_file_path=None, irp_id=1, created_by_irp_job_irp_id=None,
        inserted_at="2026-01-01", updated_at="2026-01-01",
        portfolio_count=0, portfolios=[], detail_state="empty", notes=notes)


def _rdm(notes: str | None = None) -> rdm_service.RdmRow:
    return rdm_service.RdmRow(
        id="rdm-1", name="RDM", status="ready", source_file_path=None,
        irp_id=2, inserted_at="2026-01-01", updated_at="2026-01-01",
        notes=notes)


def test_edm_note_route_rejects_csrf(monkeypatch):
    monkeypatch.setattr(entity_note_service, "update_notes", lambda **kwargs: None)
    response = _client().post("/edms/edm-1/notes", data={
        "csrf_token": "bad", "notes": "Text", "original_notes": ""})
    assert response.status_code == 403


def test_edm_note_route_preserves_over_limit_input(monkeypatch):
    monkeypatch.setattr(entity_note_service, "update_notes", lambda **kwargs: (_ for _ in ()).throw(
        ValueError("Notes must be 250 characters or fewer.")))
    monkeypatch.setattr(edm_service, "get_edm_detail", lambda edm_id: _edm())
    response = _client().post("/edms/edm-1/notes", headers={"HX-Request": "true"}, data={
        "csrf_token": generate_csrf_token(), "notes": "x" * 251,
        "original_notes": ""})
    assert response.status_code == 422
    assert "x" * 251 in response.text
    assert "250 characters or fewer" in response.text


def test_edm_note_route_returns_conflict_with_both_notes(monkeypatch):
    monkeypatch.setattr(entity_note_service, "update_notes", lambda **kwargs: (_ for _ in ()).throw(
        NoteConflict("Newer note")))
    monkeypatch.setattr(edm_service, "get_edm_detail", lambda edm_id: _edm("Newer note"))
    response = _client().post("/edms/edm-1/notes", headers={"HX-Request": "true"}, data={
        "csrf_token": generate_csrf_token(), "notes": "My note",
        "original_notes": "Older note"})
    assert response.status_code == 409
    assert "My note" in response.text
    assert "Newer note" in response.text
    assert 'name="original_notes" value="Newer note"' in response.text


def test_contextual_edm_note_route_validates_association(monkeypatch):
    monkeypatch.setattr(edm_service, "get_contextual_edm_detail", lambda **kwargs: None)
    response = _client().post(
        "/submissions/sub-1/edms/edm-1/notes",
        data={"csrf_token": generate_csrf_token(), "notes": "Text",
              "original_notes": ""})
    assert response.status_code == 404


def test_rdm_note_route_saves_and_returns_read_mode(monkeypatch):
    saved = {}
    monkeypatch.setattr(entity_note_service, "update_notes", lambda **kwargs: saved.update(kwargs))
    monkeypatch.setattr(rdm_service, "get_rdm", lambda rdm_id: _rdm("Saved note"))
    monkeypatch.setattr(rdm_service, "get_rdm_detail", lambda rdm_id: {
        "rdm": _rdm("Saved note"), "analyses": [], "sync_status": None,
        "sync_running": False, "import_error": None})
    response = _client().post("/rdms/rdm-1/notes", headers={"HX-Request": "true"}, data={
        "csrf_token": generate_csrf_token(), "notes": "Saved note",
        "original_notes": ""})
    assert response.status_code == 200
    assert saved["notes"] == "Saved note"
    assert "Saved note" in response.text
    assert 'x-data="{ editing: false }"' in response.text
