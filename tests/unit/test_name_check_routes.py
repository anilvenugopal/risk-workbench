"""Route tests for the blocking name-collision UX (issues #17 + #11).

Owns only the HTTP surface (the service behavior is covered in
``test_name_check.py`` / ``test_edm_service.py`` / ``test_rdm_service.py``):

  • ``GET /edms/name-check`` + ``GET /rdms/name-check`` render the blocking-error /
    fail-open-warning / clean states of ``partials/name_collision.html``;
  • ``POST /edms/import`` maps ``NameCollisionError`` to a 422 re-render whose
    ``.form-banner--error`` carries the message (the toast scraper contract);
  • a fail-open import redirects with ``?nc=unchecked`` and the detail page then
    shows the one-off warning banner;
  • ``GET /packages/member-name-check`` dispatches on the row's ``member_kind``
    query param; modal save / package re-sync map ``NameCollisionError`` to 422
    with the banner, and fail-open saves attach the ``rwb:toast`` HX-Trigger.

Harness: TestClient + monkeypatched services (``test_edm_sync.py`` pattern) — the
fixture SQLite engine is thread-local and TestClient dispatches on a worker thread.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import edm_service, name_check, package_service, rdm_service
from app.services import package_sync_service as psync
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


def _client(include_packages: bool = False) -> TestClient:
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import edms, rdms

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(edms.router)
    app.include_router(rdms.router)
    if include_packages:
        from app.routers import packages
        app.include_router(packages.router)
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


# ── Package modal + sync paths (issue #17 Slice 2) ────────────────────────────────

def _pkg_client(monkeypatch) -> TestClient:
    """Client with the packages router; DB-touching gates and the card read are
    stubbed out (the service behavior lives in test_package_sync_service.py)."""
    from app.routers import packages
    monkeypatch.setattr(packages, "_submission_active", lambda sid: True)
    monkeypatch.setattr(packages, "_package_actionable", lambda pid: True)
    monkeypatch.setattr(package_service, "attach_to_submission", lambda **kw: None)
    monkeypatch.setattr(
        psync, "get_package_card",
        lambda pid, with_counts=False: psync.PackageCard(id=str(pid), name="Pkg"))
    return _client(include_packages=True)


def _modal_form(action: str = "save") -> dict:
    return {"name": "Pkg", "member_kind": ["edm"], "member_name": ["Dupe"],
            "member_path": ["/share/a.bak"], "action": action,
            "csrf_token": _csrf()}


def test_member_name_check_dispatches_on_kind_param(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake(kind, name):
        calls.append((kind, name))
        return CollisionCheck(names=(name,))
    monkeypatch.setattr(name_check, "check_member_name", fake)
    r = _pkg_client(monkeypatch).get(
        "/packages/member-name-check?member_kind=rdm&member_name=Dupe")
    assert r.status_code == 200
    assert calls == [("rdm", "Dupe")]
    assert "name-collision__error" in r.text
    assert "RDM" in r.text  # kind label reaches the fragment copy


def test_modal_save_collision_maps_to_422_modal(monkeypatch):
    def _raise(**kw):
        raise NameCollisionError("Name taken: Dupe (EDM) already exist(s) in "
                                 "Risk Modeler. Choose a different name — "
                                 "nothing was saved.")
    monkeypatch.setattr(psync, "save_package", _raise)
    r = _pkg_client(monkeypatch).post("/submissions/s1/packages",
                                      data=_modal_form())
    assert r.status_code == 422
    assert 'id="package-modal"' in r.text      # modal re-render, not the card
    assert "form-banner--error" in r.text      # the toast scraper's hook
    assert "Dupe (EDM)" in r.text


def test_modal_save_fail_open_sets_toast_header(monkeypatch):
    monkeypatch.setattr(
        psync, "save_package",
        lambda **kw: psync.SaveResult(package_id="p1", unchecked_names=["E1"]))
    r = _pkg_client(monkeypatch).post("/submissions/s1/packages",
                                      data=_modal_form())
    assert r.status_code == 200
    toast = json.loads(r.headers["HX-Trigger"])["rwb:toast"]
    assert "E1" in toast["message"] and toast["type"] == "warning"


def test_modal_save_clean_has_no_toast_header(monkeypatch):
    monkeypatch.setattr(psync, "save_package",
                        lambda **kw: psync.SaveResult(package_id="p1"))
    r = _pkg_client(monkeypatch).post("/submissions/s1/packages",
                                      data=_modal_form())
    assert r.status_code == 200
    assert "HX-Trigger" not in r.headers


def test_modal_save_sync_leg_collision_returns_card_200(monkeypatch):
    # The sync leg runs AFTER the package is saved+attached. A 422 here would
    # be dropped by htmx — the card never appended to #package-list, the modal
    # left open over stale members (a re-submit then duplicates the package).
    # The card must land with 200: its banner names the reason and the modal's
    # `< 300` after-request guard closes it.
    monkeypatch.setattr(psync, "save_package",
                        lambda **kw: psync.SaveResult(package_id="p1"))

    def _raise(**kw):
        raise NameCollisionError("Sync blocked — name taken: Dupe (EDM) now "
                                 "exist(s) in Risk Modeler.")
    monkeypatch.setattr(psync, "save_and_sync", _raise)
    r = _pkg_client(monkeypatch).post("/submissions/s1/packages",
                                      data=_modal_form(action="sync"))
    assert r.status_code == 200
    assert 'id="package-modal"' not in r.text   # the card, not the modal
    assert "form-banner--error" in r.text
    assert "Dupe (EDM)" in r.text


def test_resync_collision_renders_422_card_with_banner(monkeypatch):
    def _raise(**kw):
        raise NameCollisionError("Sync blocked — name taken: E1 (EDM) now "
                                 "exist(s) in Risk Modeler.")
    monkeypatch.setattr(psync, "save_and_sync", _raise)
    r = _pkg_client(monkeypatch).post("/packages/p1/sync",
                                      data={"csrf_token": _csrf()})
    assert r.status_code == 422
    assert "form-banner--error" in r.text
    assert "E1 (EDM)" in r.text


def test_resync_fail_open_sets_toast_header(monkeypatch):
    monkeypatch.setattr(psync, "save_and_sync", lambda **kw: ["R9"])
    r = _pkg_client(monkeypatch).post("/packages/p1/sync",
                                      data={"csrf_token": _csrf()})
    assert r.status_code == 200
    assert "R9" in json.loads(r.headers["HX-Trigger"])["rwb:toast"]["message"]
