"""Route and template contract for the manual RDM sync UX.

``POST /rdms/{rdm_id}/sync`` (CSRF; HTMX swap of the live ``#rdm-detail`` body,
PRG fallback) and ``GET /rdms/{rdm_id}/body`` (the self-terminating poll
target), with the services monkeypatched.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import analysis_service, rdm_service

# ── routes: POST /rdms/{rdm_id}/sync + GET /rdms/{rdm_id}/body (live UX) ──────────
#
# These are the database-free tier: the services are monkeypatched to pin their
# behavior, and the tests assert the route/template contract only. The SQL-bearing
# sync flows live in tests/sqlserver/test_rdm_sync.py. Mirrors the EDM Sync route
# tests one-for-one.

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
    from app.routers import rdms

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(rdms.router)
    return TestClient(app, follow_redirects=False)


def _rdm_obj(**over) -> rdm_service.RdmRow:
    base = dict(
        id="rdm-1", name="legacy_rdm", status="ready",
        source_file_path="/share/legacy.mdf", irp_id=88001,
        inserted_at="2026-01-01", updated_at="2026-01-01")
    base.update(over)
    return rdm_service.RdmRow(**base)


def _stub_reads(monkeypatch, *, rdm=..., sync_status=None, analyses=None):
    if rdm is ...:
        rdm = _rdm_obj()
    monkeypatch.setattr(rdm_service, "get_rdm", lambda rdm_id: rdm)
    monkeypatch.setattr(rdm_service, "latest_backfill_status",
                        lambda rdm_id: sync_status)
    monkeypatch.setattr(analysis_service, "list_broker_analyses",
                        lambda *, rdm_id: analyses or [])


def test_sync_route_bad_csrf_redirects_without_service_call(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(rdm_service, "sync_detail",
                        lambda **kw: calls.append(kw))
    r = _client().post("/rdms/rdm-1/sync", data={"csrf_token": "garbage"})
    assert r.status_code == 303
    assert r.headers["location"] == "/rdms/rdm-1"
    assert calls == []


def test_sync_route_nonhtmx_post_redirects_prg(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(rdm_service, "sync_detail",
                        lambda **kw: calls.append(kw) or "job-1")
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/rdms/rdm-1/sync",
                       data={"csrf_token": generate_csrf_token()})
    assert r.status_code == 303
    assert r.headers["location"] == "/rdms/rdm-1"
    assert calls == [{"rdm_id": "rdm-1", "actor_id": "analyst-1"}]


def test_sync_route_htmx_returns_live_body_partial(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(rdm_service, "sync_detail",
                        lambda **kw: calls.append(kw) or "job-1")
    _stub_reads(monkeypatch, sync_status="pending")
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/rdms/rdm-1/sync",
                       data={"csrf_token": generate_csrf_token()},
                       headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert calls == [{"rdm_id": "rdm-1", "actor_id": "analyst-1"}]
    assert 'id="rdm-detail"' in r.text
    assert 'hx-get="/rdms/rdm-1/body"' in r.text and "every 3s" in r.text
    assert "Syncing" in r.text and "disabled" in r.text
    assert "</html>" not in r.text  # a partial — no shell around it


def test_sync_route_htmx_bad_csrf_forces_full_refresh(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(rdm_service, "sync_detail",
                        lambda **kw: calls.append(kw))
    r = _client().post("/rdms/rdm-1/sync", data={"csrf_token": "garbage"},
                       headers={"HX-Request": "true"})
    assert r.status_code == 204
    assert r.headers["hx-refresh"] == "true"
    assert calls == []


def test_body_poll_partial_polls_while_running_then_stops(monkeypatch):
    _stub_reads(monkeypatch, sync_status="running")
    html = _client().get("/rdms/rdm-1/body").text
    assert 'hx-get="/rdms/rdm-1/body"' in html and "every 3s" in html

    _stub_reads(monkeypatch, rdm=_rdm_obj(as_of="2026-07-24 10:00:00"),
                sync_status="succeeded")
    html = _client().get("/rdms/rdm-1/body").text
    assert "every 3s" not in html
    assert "synced" in html
    assert '<time data-utc="2026-07-24 10:00:00"' in html
    assert ">Sync</button>" in html  # the button is offered again, enabled


def test_body_poll_populated_mid_sync_returns_204_no_swap(monkeypatch):
    # Re-syncing an already-populated page: a 3s outerHTML swap would collapse
    # every open <details> (analysis drills), so the poll target answers 204
    # until the sync lands — then the fresh body renders exactly once.
    grp = analysis_service.BrokerAnalysisGroup(
        rdm_id="rdm-1", rdm_name="R", rdm_irp_id=88,
        analyses=[analysis_service.BrokerAnalysis(
            id="a1", irp_id="5521", name="AEP", rdm_id="rdm-1", rdm_name="R",
            edm_name="E1")])
    _stub_reads(monkeypatch, sync_status="running", analyses=[grp])
    r = _client().get("/rdms/rdm-1/body")
    assert r.status_code == 204


def test_body_poll_partial_live_while_importing(monkeypatch):
    _stub_reads(monkeypatch, rdm=_rdm_obj(status="pending_import"))
    html = _client().get("/rdms/rdm-1/body").text
    assert "every 3s" in html


def test_body_poll_partial_when_rdm_gone(monkeypatch):
    monkeypatch.setattr(rdm_service, "get_rdm", lambda rdm_id: None)
    r = _client().get("/rdms/rdm-1/body")
    assert r.status_code == 200
    assert "no longer exists" in r.text
    assert "every 3s" not in r.text


def test_sync_button_rendered_by_state(monkeypatch):
    # ready + never-captured → the Sync form is offered (header + state box)
    _stub_reads(monkeypatch)
    html = _client().get("/rdms/rdm-1").text
    assert 'hx-post="/rdms/rdm-1/sync"' in html
    assert 'hx-target="#rdm-detail"' in html
    assert "Sync now" in html  # the settings-unavailable box offers the action
    # importing → no Sync form (the import is still in flight)
    _stub_reads(monkeypatch, rdm=_rdm_obj(status="pending_import"))
    html = _client().get("/rdms/rdm-1").text
    assert "/rdms/rdm-1/sync" not in html


def test_sync_failed_state_shows_warn_and_recovery(monkeypatch):
    _stub_reads(monkeypatch, sync_status="failed")
    html = _client().get("/rdms/rdm-1/body").text
    assert "last sync failed" in html
    assert "Sync now" in html
    assert "every 3s" not in html  # terminal — no poll
