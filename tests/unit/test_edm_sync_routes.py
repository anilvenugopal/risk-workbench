"""Unit tests for the "sync existing EDMs" page — ``GET`` / ``POST /edms/sync``.

The list is a live Risk Modeler read per render, so the page degrades to an
unavailable notice rather than a 500 when the gateway fails. Both paths are
literal and must be declared before ``/edms/{edm_id}``, which would otherwise
swallow ``/edms/sync`` as an EDM id. ``POST`` is Post/Redirect/Get back to this
page — the only page that can report an EDM another analyst claimed first.
"""

from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import edm_service
from app.workers import dispatch
from db import execute


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


def _csrf() -> str:
    from app.auth.csrf import generate_csrf_token
    return generate_csrf_token()


def _flat(body: str) -> str:
    """Rendered HTML with runs of whitespace collapsed, so a sentence Jinja wrapped
    across source lines can be asserted as one string."""
    return re.sub(r"\s+", " ", body)


# ── GET /edms/sync ───────────────────────────────────────────────────────────────

def test_the_page_lists_the_adoptable_edms(iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="Coastal_Wind_Study", irp_id=501,
                             portfolio_count=4, treaty_count=1)

    body = _client().get("/edms/sync").text

    assert "Coastal_Wind_Study" in body
    assert 'value="501"' in body
    assert "1 EDM in Risk Modeler is not in the workbench" in _flat(body)


def test_the_literal_path_renders_and_activates_its_own_nav_node(
        iteration2_db, fake_irp):
    # /edms/{edm_id} would match "sync" as an id and 404 on the lookup, and the
    # sidebar must read "Moody's IRP › Sync from Risk Modeler", not EDM Library.
    response = _client().get("/edms/sync")

    assert response.status_code == 200
    assert "Sync from Risk Modeler" in response.text
    assert 'href="/edms/sync"' in response.text


def test_a_gateway_failure_degrades_the_page_rather_than_500ing(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    fake_irp.raise_on_list_edms = True

    response = _client().get("/edms/sync")

    assert response.status_code == 200
    assert "Risk Modeler is not answering" in response.text
    # An empty list must never be presented as "everything is already synced".
    assert "Nothing to sync" not in response.text


def test_an_empty_diff_says_nothing_to_sync(iteration2_db, fake_irp):
    assert "Nothing to sync" in _client().get("/edms/sync").text


def test_a_search_with_no_match_is_distinguished_from_an_empty_diff(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)

    body = _client().get("/edms/sync?q=nomatch").text

    assert "No match" in body
    assert "Nothing to sync" not in body


def test_the_pager_appears_only_once_there_is_a_second_page(
        iteration2_db, fake_irp):
    for i in range(edm_service.ADOPTABLE_PAGE_SIZE + 1):
        fake_irp.add_catalog_edm(name=f"edm_{i:03d}", irp_id=1000 + i)

    body = _client().get("/edms/sync").text

    assert "/edms/sync?page=2" in body
    assert "Page 1" in body


def test_the_pager_carries_the_search_term(iteration2_db, fake_irp):
    for i in range(edm_service.ADOPTABLE_PAGE_SIZE + 1):
        fake_irp.add_catalog_edm(name=f"renewal_{i:03d}", irp_id=1000 + i)

    body = _client().get("/edms/sync?q=renewal").text

    # Next must stay inside the filtered list rather than dropping back to all.
    assert "/edms/sync?q=renewal&amp;page=2" in body


def test_the_form_posts_back_into_the_filtered_list(iteration2_db, fake_irp):
    # The POST reads the search term off its own query string, so the action must
    # carry it — otherwise syncing from a filtered list lands on the unfiltered one.
    fake_irp.add_catalog_edm(name="renewal_alpha", irp_id=501)

    body = _client().get("/edms/sync?q=renewal").text

    assert 'action="/edms/sync?q=renewal"' in body


def test_a_zero_count_renders_as_zero_not_a_dash(iteration2_db, fake_irp):
    # An EDM with no portfolios is what an analyst most wants to spot before
    # syncing, so it must not look like a figure Risk Modeler did not return.
    fake_irp.add_catalog_edm(name="alpha", irp_id=501, portfolio_count=0,
                             treaty_count=0)

    body = _client().get("/edms/sync").text

    assert body.count('<td class="sync-num">0</td>') == 2


def test_a_mangled_page_param_reads_page_one(iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)

    response = _client().get("/edms/sync?page=notanumber")

    assert response.status_code == 200
    assert "alpha" in response.text


# ── POST /edms/sync ──────────────────────────────────────────────────────────────

def test_posting_ticked_ids_adopts_them_and_redirects_with_the_counts(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    fake_irp.add_catalog_edm(name="beta", irp_id=502)
    dispatch.configure(lambda **kw: None)
    try:
        response = _client().post(
            "/edms/sync", data={"irp_ids": ["501", "502"], "csrf_token": _csrf()})
    finally:
        dispatch.reset()

    assert response.status_code == 303
    assert response.headers["location"] == "/edms/sync?synced=2&skipped=0"
    rows = execute("SELECT name FROM irp_edm ORDER BY name", connection="WORKBENCH")
    assert [r["name"] for r in rows] == ["alpha", "beta"]


def test_the_redirect_reports_an_edm_another_analyst_already_took(
        iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    dispatch.configure(lambda **kw: None)
    try:
        client = _client()
        client.post("/edms/sync", data={"irp_ids": ["501"], "csrf_token": _csrf()})
        second = client.post("/edms/sync",
                             data={"irp_ids": ["501"], "csrf_token": _csrf()})
    finally:
        dispatch.reset()

    assert second.headers["location"] == "/edms/sync?synced=0&skipped=1"


def test_the_redirect_keeps_the_search_term(iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)
    dispatch.configure(lambda **kw: None)
    try:
        response = _client().post("/edms/sync?q=alp",
                                  data={"irp_ids": ["501"], "csrf_token": _csrf()})
    finally:
        dispatch.reset()

    assert response.headers["location"] == "/edms/sync?synced=1&skipped=0&q=alp"


def test_the_banner_is_rendered_from_the_redirect_counts(iteration2_db, fake_irp):
    body = _client().get("/edms/sync?synced=2&skipped=1").text

    assert "Syncing 2 EDMs" in _flat(body)
    assert ("1 EDM was skipped — already tracked in the workbench, or no longer in "
            "Risk Modeler." in _flat(body))


def test_the_banner_still_reports_the_sync_when_the_reread_fails(
        iteration2_db, fake_irp):
    # The redirect's list read is a second Risk Modeler call and can fail on its
    # own. The rows were still created, so the page must not claim otherwise.
    fake_irp.raise_on_list_edms = True

    body = _flat(_client().get("/edms/sync?synced=2&skipped=0").text)

    assert "Syncing 2 EDMs" in body
    assert "Risk Modeler is not answering" in body


def test_posting_nothing_is_a_no_op_redirect_not_a_500(iteration2_db, fake_irp):
    response = _client().post("/edms/sync", data={"csrf_token": _csrf()})

    assert response.status_code == 303
    assert response.headers["location"] == "/edms/sync?synced=0&skipped=0"
    assert execute("SELECT id FROM irp_edm", connection="WORKBENCH") == []


def test_a_bad_csrf_token_adopts_nothing(iteration2_db, fake_irp):
    fake_irp.add_catalog_edm(name="alpha", irp_id=501)

    response = _client().post("/edms/sync",
                              data={"irp_ids": ["501"], "csrf_token": "wrong"})

    assert response.status_code == 303
    assert response.headers["location"] == "/edms/sync"
    assert execute("SELECT id FROM irp_edm", connection="WORKBENCH") == []


def test_a_bad_csrf_token_on_the_htmx_path_forces_a_reload(iteration2_db, fake_irp):
    response = _client().post("/edms/sync",
                              data={"irp_ids": ["501"], "csrf_token": "wrong"},
                              headers={"HX-Request": "true"})

    # Never swap a redirect-followed page into the DOM — reload to mint tokens.
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"
