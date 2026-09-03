"""Route tests for the blocking name-collision UX (issues #17 + #11).

Owns only the HTTP surface (the service behavior is covered in
``test_name_check.py`` / ``test_edm_service.py`` / ``test_rdm_service.py``):

  • ``GET /edms/name-check`` + ``GET /rdms/name-check`` render the blocking-error /
    fail-open-warning / clean states of ``partials/name_collision.html``;
  • ``POST /edms/import`` maps ``NameCollisionError`` to a 422 re-render whose
    ``.form-banner--error`` carries the message (the toast scraper contract);
  • a fail-open import redirects with ``?nc=unchecked`` and the detail page then
    shows the one-off warning banner;

Harness: TestClient + monkeypatched services (``test_edm_sync.py`` pattern) — the
fixture SQLite engine is thread-local and TestClient dispatches on a worker thread.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import edm_service, name_check, rdm_service
from app.services.edm_service import ImportResult
from app.services.errors import NameCollisionError
from app.services.name_check import CollisionCheck
from tests.unit.test_edm_sync import _detail_obj


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
    from app.routers import edms, rdms
    from app.templating import TEMPLATE_DIRS

    app = FastAPI()
    templates = Jinja2Templates(directory=TEMPLATE_DIRS)
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(edms.router)
    app.include_router(rdms.router)
    return TestClient(app, follow_redirects=False)


def _csrf() -> str:
    from app.auth.csrf import generate_csrf_token
    return generate_csrf_token()


# ── GET /edms/name-check — the as-you-type fragment states ────────────────────────

def test_name_check_renders_blocking_error_on_hit(monkeypatch):
    monkeypatch.setattr(edm_service, "check_name_collision",
                        lambda name: CollisionCheck(names=(name,)))
    r = _client().get("/edms/name-check?name=Dupe")
    assert r.status_code == 200
    assert "name-collision__error" in r.text
    assert "Saving is blocked" in r.text


def test_name_check_renders_fail_open_warning(monkeypatch):
    monkeypatch.setattr(edm_service, "check_name_collision",
                        lambda name: CollisionCheck(checked=False))
    r = _client().get("/edms/name-check?name=Whatever")
    assert "name-collision__warn" in r.text
    assert "Couldn" in r.text  # "Couldn't reach Risk Modeler…"


def test_name_check_clean_renders_success(monkeypatch):
    monkeypatch.setattr(edm_service, "check_name_collision",
                        lambda name: CollisionCheck())
    r = _client().get("/edms/name-check?name=Fresh")
    assert 'class="name-collision"' in r.text  # swap target survives
    assert 'data-nc="ok"' in r.text            # what enables Import in app.js
    assert "name-collision__ok" in r.text
    assert "Name available" in r.text
    assert "name-collision__error" not in r.text
    assert "name-collision__warn" not in r.text


def test_name_check_empty_name_stays_pending(monkeypatch):
    # A blank name comes back "clear" from the service but must not claim the
    # name is available — no verdict, so Import stays disabled.
    monkeypatch.setattr(edm_service, "check_name_collision",
                        lambda name: CollisionCheck())
    r = _client().get("/edms/name-check?name=%20")
    assert "data-nc" not in r.text
    assert "name-collision__ok" not in r.text


def test_name_check_states_carry_machine_readable_verdict(monkeypatch):
    monkeypatch.setattr(edm_service, "check_name_collision",
                        lambda name: CollisionCheck(names=(name,)))
    assert 'data-nc="blocked"' in _client().get("/edms/name-check?name=Dupe").text
    monkeypatch.setattr(edm_service, "check_name_collision",
                        lambda name: CollisionCheck(checked=False))
    assert 'data-nc="unchecked"' in _client().get("/edms/name-check?name=X").text


def test_rdm_name_check_mirrors(monkeypatch):
    monkeypatch.setattr(rdm_service, "check_name_collision",
                        lambda name: CollisionCheck(names=(name,)))
    r = _client().get("/rdms/name-check?name=Dupe")
    assert "name-collision__error" in r.text


# ── POST /edms/import — blocking 422 + fail-open redirect ─────────────────────────

def test_import_collision_maps_to_422_banner(monkeypatch):
    def _raise(**kw):
        raise NameCollisionError("An EDM named 'Dupe' already exists in Risk "
                                 "Modeler. Choose a different name.")
    monkeypatch.setattr(edm_service, "import_edm", _raise)
    r = _client().post("/edms/import",
                       data={"name": "Dupe", "source_paths": ["/share/a.bak"],
                             "csrf_token": _csrf()})
    assert r.status_code == 422
    assert "form-banner--error" in r.text  # the toast scraper's hook
    assert "already exists in Risk Modeler" in r.text


def test_import_fail_open_redirects_with_marker(monkeypatch):
    monkeypatch.setattr(
        edm_service, "import_edm",
        lambda **kw: ImportResult(entity_id="edm-1", collision_unchecked=True))
    r = _client().post("/edms/import",
                       data={"name": "Fresh", "source_paths": ["/share/a.bak"],
                             "csrf_token": _csrf()})
    assert r.status_code == 303
    assert r.headers["location"] == "/edms/edm-1?nc=unchecked"


def test_import_checked_redirects_without_marker(monkeypatch):
    monkeypatch.setattr(
        edm_service, "import_edm",
        lambda **kw: ImportResult(entity_id="edm-1"))
    r = _client().post("/edms/import",
                       data={"name": "Fresh", "source_paths": ["/share/a.bak"],
                             "csrf_token": _csrf()})
    assert r.status_code == 303
    assert r.headers["location"] == "/edms/edm-1"


# ── GET /edms/{id}?nc=unchecked — the one-off fail-open banner ────────────────────

def test_detail_shows_unchecked_banner_only_with_flag(monkeypatch):
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj())
    flagged = _client().get("/edms/edm-1?nc=unchecked")
    assert "state-box--warn" in flagged.text
    assert "check this name for duplicates" in flagged.text
    plain = _client().get("/edms/edm-1")
    assert "check this name for duplicates" not in plain.text
