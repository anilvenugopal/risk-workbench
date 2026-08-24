"""Route and template tests for the manual per-EDM Sync action (spec 004
Addendum A, T056): ``POST /edms/{edm_id}/sync`` (CSRF; HTMX swap of the live
``#edm-detail`` body, PRG fallback) + ``GET /edms/{edm_id}/body`` (the
self-terminating poll target), with service behavior
monkeypatched. The SQL-bearing sync flows live in
``tests/sqlserver/test_edm_sync.py``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import edm_service

# ── route: POST /edms/{edm_id}/sync + GET /edms/{edm_id}/body (live UX) ───────────
#
# These tests are the database-free tier: edm_service is monkeypatched to pin
# service behavior, and the assertions own the route/template contract only.
# The SQL-bearing sync flows live in tests/sqlserver. The Sync UX mirrors the
# An HTMX POST swaps the ``#edm-detail`` body partial,
# which self-polls ``GET /edms/{id}/body`` every 3s while the backfill head is in
# flight and stops emitting the trigger once it lands. The no-JS fallback is
# Post/Redirect/Get, so a refresh never re-prompts the form.

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
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(edms.router)
    return TestClient(app, follow_redirects=False)


def _detail_obj(**over) -> edm_service.EdmDetail:
    base = dict(
        id="edm-1", name="legacy_edm", status="ready", as_of=None,
        source_file_path="/share/legacy.bak", irp_id=77001,
        created_by_irp_job_irp_id=None,
        inserted_at="2026-01-01", updated_at="2026-01-01",
        portfolio_count=0, portfolios=[], detail_state="unavailable",
        sync_running=False)
    base.update(over)
    return edm_service.EdmDetail(**base)


def test_sync_route_bad_csrf_redirects_without_service_call(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(edm_service, "sync_detail",
                        lambda **kw: calls.append(kw))
    r = _client().post("/edms/edm-1/sync", data={"csrf_token": "garbage"})
    assert r.status_code == 303
    assert r.headers["location"] == "/edms/edm-1"
    assert calls == []


def test_sync_route_nonhtmx_post_redirects_prg(monkeypatch):
    # No-JS fallback: Post/Redirect/Get back to the canonical URL — the browser
    # never parks on /sync, so refreshing never re-prompts a form re-submission.
    calls: list[dict] = []
    monkeypatch.setattr(edm_service, "sync_detail",
                        lambda **kw: calls.append(kw) or "job-1")
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/edms/edm-1/sync",
                       data={"csrf_token": generate_csrf_token()})
    assert r.status_code == 303
    assert r.headers["location"] == "/edms/edm-1"
    assert calls == [{"edm_id": "edm-1", "actor_id": "analyst-1"}]
    # The EDM page shows RDM-sourced analyses too — its Sync refreshes both.


def test_sync_route_htmx_returns_live_body_partial(monkeypatch):
    # HTMX path: the POST swaps the #edm-detail wrapper in place (URL untouched);
    # the swapped-in render shows the disabled Syncing… button AND carries the
    # self-poll trigger because the head is now in flight.
    calls: list[dict] = []
    monkeypatch.setattr(edm_service, "sync_detail",
                        lambda **kw: calls.append(kw) or "job-1")
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(detail_state="pending",
                                                   sync_running=True))
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/edms/edm-1/sync",
                       data={"csrf_token": generate_csrf_token()},
                       headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert calls == [{"edm_id": "edm-1", "actor_id": "analyst-1"}]
    assert 'id="edm-detail"' in r.text
    assert 'hx-get="/edms/edm-1/body"' in r.text and "every 3s" in r.text
    assert "Syncing" in r.text and "disabled" in r.text
    assert "</html>" not in r.text  # a partial — no shell around it


def test_sync_route_htmx_bad_csrf_forces_full_refresh(monkeypatch):
    # A stale/garbage token on the HTMX path must not swap a nested full page
    # into the wrapper — HX-Refresh reloads the page (and its fresh tokens).
    calls: list[dict] = []
    monkeypatch.setattr(edm_service, "sync_detail",
                        lambda **kw: calls.append(kw))
    r = _client().post("/edms/edm-1/sync", data={"csrf_token": "garbage"},
                       headers={"HX-Request": "true"})
    assert r.status_code == 204
    assert r.headers["hx-refresh"] == "true"
    assert calls == []


def test_body_poll_partial_polls_while_running_then_stops(monkeypatch):
    # In flight → the wrapper emits its own every-3s poll; landed → the trigger
    # disappears (polling self-terminates) and the fresh synced stamp is shown.
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(detail_state="pending",
                                                   sync_running=True))
    html = _client().get("/edms/edm-1/body").text
    assert 'hx-get="/edms/edm-1/body"' in html and "every 3s" in html

    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(detail_state="populated",
                                                   as_of="2026-07-24 10:00:00"))
    html = _client().get("/edms/edm-1/body").text
    assert "every 3s" not in html
    assert "synced" in html
    # The stamp is a <time data-utc> element: app.js rewrites it to the
    # browser's local timezone (h:mm:ss AM/PM), re-run after every HTMX swap.
    assert '<time data-utc="2026-07-24 10:00:00"' in html
    assert ">Sync</button>" in html  # the button is offered again, enabled


def test_body_poll_populated_mid_sync_returns_204_no_swap(monkeypatch):
    # Re-syncing an already-populated page: a 3s outerHTML swap would collapse
    # every <details> the analyst opened, so the poll target answers 204 (htmx
    # swaps nothing, the poll keeps ticking) until the sync lands — then the
    # fresh body renders exactly once (trigger gone, poll ends).
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(detail_state="populated",
                                                   sync_running=True,
                                                   as_of="2026-07-24 10:00:00"))
    r = _client().get("/edms/edm-1/body")
    assert r.status_code == 204

    # The sync POST itself still renders the body — it wires the poll up.
    monkeypatch.setattr(edm_service, "sync_detail", lambda **kw: "job-1")
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/edms/edm-1/sync",
                       data={"csrf_token": generate_csrf_token()},
                       headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "every 3s" in r.text


def test_body_poll_partial_live_while_importing(monkeypatch):
    # The same wrapper keeps the page fresh through an in-flight import too —
    # the import statuses remain live while the worker runs.
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(status="pending_import",
                                                   detail_state="importing"))
    html = _client().get("/edms/edm-1/body").text
    assert "every 3s" in html


def test_body_poll_partial_when_edm_gone(monkeypatch):
    # EDM hard-gone mid-poll: swap in a terminal notice with no trigger, so
    # polling ends instead of 404-looping.
    monkeypatch.setattr(edm_service, "get_edm_detail", lambda edm_id: None)
    r = _client().get("/edms/edm-1/body")
    assert r.status_code == 200
    assert "no longer exists" in r.text
    assert "every 3s" not in r.text


def test_treaties_header_holds_export_and_rm_link(monkeypatch):
    # Treaties polish (2026-07-24): the Export button sits IN the header row
    # (no sec__action block below it any more) and the old read-only note is a
    # real deep link into Risk Modeler's treaties screen, opening a new tab.
    from app.services.treaty_service import TreatyRow
    t = TreatyRow(id="t1", edm_id="edm-1", name="Cat XoL", irp_id="1042",
                  attributes={"treatyType": "CATA"}, as_of="2026-07-24")
    rm_url = "https://rm.example.com/riskmodeler/datasources/legacy_edm/treaties"
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="populated",
                            as_of="2026-07-24 10:00:00", treaties=[t],
                            rm_treaties_url=rm_url))
    html = _client().get("/edms/edm-1").text
    assert 'href="/edms/edm-1/treaties.xlsx"' in html
    assert "sec__action" not in html          # in-line with the header now
    assert f'href="{rm_url}"' in html
    assert 'target="_blank"' in html


def test_treaties_header_without_treaties_or_rm_url(monkeypatch):
    # No treaties → no Export offer; no configured base URL → the plain
    # read-only note falls back in place of the link.
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="empty", as_of="2026-07-24 10:00:00"))
    html = _client().get("/edms/edm-1").text
    assert "treaties.xlsx" not in html
    assert "edit in Risk Modeler" in html


def test_treaties_rm_link_hidden_until_import_finishes(monkeypatch):
    # The RM datasource URL is name-based and 404s until the import job
    # finishes, so the deep link only appears once the EDM is ready — the
    # plain read-only note holds its place while the import is in flight.
    rm_url = "https://rm.example.com/riskmodeler/datasources/legacy_edm/treaties"
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            status="importing", detail_state="importing",
                            rm_treaties_url=rm_url))
    html = _client().get("/edms/edm-1").text
    assert rm_url not in html
    assert "edit in Risk Modeler" in html


def test_sync_button_rendered_by_state(monkeypatch):
    # ready + unavailable → the Sync form is offered (header + state box),
    # wired as an HTMX swap of the #edm-detail wrapper
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj())
    html = _client().get("/edms/edm-1").text
    assert 'hx-post="/edms/edm-1/sync"' in html
    assert 'hx-target="#edm-detail"' in html
    assert "Sync now" in html  # the unavailable state box offers the action
    # importing → no Sync form (the import is still in flight)
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(status="pending_import",
                                                   detail_state="importing"))
    html = _client().get("/edms/edm-1").text
    assert "/edms/edm-1/sync" not in html
