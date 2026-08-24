"""Route tests for the read-only analysis metadata page."""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.auth.csrf import generate_csrf_token
from app.config import settings
from app.routers import templates
from app.services import rwb_job_service
from app.services.auth_service import CurrentUser
from app.services.irp_gateway import CurrencyEntry
from app.workers import dispatch, metadata_jobs
from db import execute, execute_command

ROUTE_USER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _seed_route_user(workbench_db):
    execute_command(
        "INSERT INTO app_user (id, email, display_name, is_active, "
        "must_change_password) VALUES (:id, :email, :name, 1, 0)",
        {"id": ROUTE_USER_ID, "email": "metadata.user@example.com",
         "name": "Metadata User"},
        connection="WORKBENCH",
    )


class _InjectUser(BaseHTTPMiddleware):
    def __init__(self, app, is_admin: bool = False):
        super().__init__(app)
        self.is_admin = is_admin

    async def dispatch(self, request: Request, call_next):
        request.state.user = CurrentUser(
            id=ROUTE_USER_ID,
            email="analyst@example.com",
            display_name="Test Analyst",
            session_id="session-id",
            role_codes=["admin"] if self.is_admin else ["analyst"],
            is_admin=self.is_admin,
            must_change_password=False,
            entra_oid=None,
            is_active=True,
        )
        return await call_next(request)


def _client(*, admin: bool = False) -> TestClient:
    app = FastAPI()
    renderer = Jinja2Templates(directory="app/templates")
    renderer.env.globals["app_env"] = settings.app_env
    renderer.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    renderer.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    renderer.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = renderer
    app.add_middleware(_InjectUser, is_admin=admin)
    app.include_router(templates.router)
    return TestClient(app, follow_redirects=False)


def _flat(body: str) -> str:
    return re.sub(r"\s+", " ", body)


def test_metadata_page_renders_all_five_read_only_tabs(workbench_db):
    body = _client().get("/templates/metadata").text

    assert "Model Profiles" in body
    assert "Output Profiles" in body
    assert "Event Rate Schemes" in body
    assert "Currencies" in body
    assert "Currency Schemes" in body


def test_no_metadata_tab_has_create_or_edit_controls(workbench_db):
    client = _client()
    for tab in (
        "model-profiles", "output-profiles", "event-rate-schemes",
        "currencies", "currency-schemes",
    ):
        body = client.get(f"/templates/metadata/table?tab={tab}").text
        assert 'href="/templates/analysis-templates/new"' not in body
        assert ">Create<" not in body
        assert ">Edit<" not in body
        # The event-rate visibility checkbox is admin-only.
        assert 'type="checkbox"' not in body


def test_currencies_tab_renders_synced_currencies(workbench_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    body = _client().get("/templates/metadata/table?tab=currencies").text

    assert "USD" in body
    assert "US Dollar" in body
    assert "United States" in body


def test_currencies_tab_filters_by_code_name_or_country(workbench_db, fake_irp):
    fake_irp.currencies = [
        *fake_irp.currencies,
        CurrencyEntry("EUR", "Euro", "France", "€"),
    ]
    metadata_jobs._sync_irp_metadata_body()

    body = _client().get("/templates/metadata/table?tab=currencies&q=euro").text

    assert "EUR" in body
    assert "USD" not in body


def test_currency_schemes_tab_renders_schemes_with_their_vintages(
    workbench_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()

    body = _client().get(
        "/templates/metadata/table?tab=currency-schemes"
    ).text

    assert "RMS Scheme" in body
    assert "Deterministic Scheme" in body
    assert "RL25" in body
    assert "RL24" in body
    assert "2025-05-28" in body
    assert "2025-05-28 00:00:00" not in body
    assert "USD" in body
    assert "30 days" in body


def test_currency_schemes_tab_filters_by_name_or_code(workbench_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    body = _client().get(
        "/templates/metadata/table?tab=currency-schemes&q=dt"
    ).text

    assert "Deterministic Scheme" in body
    assert "RMS Scheme" not in body


def test_currency_scheme_with_no_vintages_shows_empty_marker(workbench_db):
    with workbench_db.engine.begin() as conn:
        conn.exec_driver_sql(
            """
            INSERT INTO irp_currency_scheme
              (id, irp_id, name, code, inserted_at, updated_at)
            VALUES
              (NEWID(), 99, 'Empty Scheme', 'EMPTY',
               '2026-08-19 10:00:00', '2026-08-19 10:00:00')
            """
        )

    body = _client().get(
        "/templates/metadata/table?tab=currency-schemes&q=empty"
    ).text

    assert "Empty Scheme" in body


def test_metadata_fragment_filters_and_uses_wheel_marker_derivation(
    workbench_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()

    body = _client().get(
        "/templates/metadata/table?tab=model-profiles&q=default"
    ).text

    assert "RMS Default RL25" in body
    assert "RMS Default HD" in body
    assert "Open profile" not in body
    assert ">DLM<" in body
    assert ">HD<" in body
    assert "RL25" in body
    assert "HDv3.0" in body


def test_accumulation_marker_takes_precedence_over_version_classification(
    workbench_db,
):
    with workbench_db.engine.begin() as conn:
        conn.exec_driver_sql(
            """
            INSERT INTO irp_model_profile
              (id, irp_id, name, is_accumulation, software_version_code,
               inserted_at, updated_at)
            VALUES
              (NEWID(), 99, 'Global Accumulation', 1, 'HDv3.0',
               '2026-08-18 10:00:00', '2026-08-18 10:00:00')
            """
        )

    body = _client().get(
        "/templates/metadata/table?tab=model-profiles&q=global"
    ).text

    assert "Global Accumulation" in body
    assert ">Accumulation<" in body
    assert ">HD<" not in body


def test_sync_enqueues_and_dispatches_once_then_refuses_second_request(
    workbench_db,
):
    sent: list[dict] = []
    dispatch.configure(lambda **message: sent.append(message))
    try:
        client = _client()
        first = client.post(
            "/templates/metadata/sync",
            data={"csrf_token": generate_csrf_token()},
        )
        second = client.post(
            "/templates/metadata/sync",
            data={"csrf_token": generate_csrf_token()},
        )
    finally:
        dispatch.reset()

    assert first.status_code == 303
    assert first.headers["location"] == "/templates/metadata?sync=queued"
    assert second.status_code == 303
    assert second.headers["location"] == (
        "/templates/metadata?sync=already-running"
    )
    assert len(sent) == 1
    jobs = execute(
        "SELECT status_code FROM rwb_job WHERE rwb_job_type='sync_irp_metadata'",
        connection="WORKBENCH",
    )
    assert [job["status_code"] for job in jobs] == ["pending"]
    banner = client.get(second.headers["location"])
    assert "Sync already in progress." in banner.text


def test_failed_sync_reason_and_prior_snapshot_time_are_displayed(
    workbench_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request",
        requestor_id=templates._METADATA_SYNC_REQUESTOR_ID,
        rwb_job_type="sync_irp_metadata",
    )
    rwb_job_service.complete_rwb_job(
        rwb_job_id=job_id,
        status="failed",
        error_detail="Risk Modeler authentication failed.",
    )

    body = _flat(_client().get("/templates/metadata").text)

    assert "Metadata sync failed." in body
    assert "Risk Modeler authentication failed." in body
    assert "Last synced" in body


def test_bad_csrf_token_does_not_enqueue_sync(workbench_db):
    response = _client().post(
        "/templates/metadata/sync", data={"csrf_token": "wrong"}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/templates/metadata"
    assert execute("SELECT id FROM rwb_job", connection="WORKBENCH") == []


# ── Risk Modeler deep links on each metadata tab ──────────────────────────────

def test_each_tab_links_out_to_its_risk_modeler_settings_screen(
    workbench_db, monkeypatch,
):
    monkeypatch.setattr(settings, "risk_modeler_base_url",
                        "https://api-euw1.rms-ppe.com/")
    monkeypatch.setattr(settings, "risk_modeler_tenant_name", "prodmgmt")

    body = _client().get(
        "/templates/metadata/table?tab=model-profiles"
    ).text
    assert (
        'href="https://prodmgmt.rms-ppe.com/riskmodeler/datasources/'
        'model-settings/profiles"' in body
    )

    body = _client().get(
        "/templates/metadata/table?tab=output-profiles"
    ).text
    assert (
        'href="https://prodmgmt.rms-ppe.com/riskmodeler/datasources/'
        'model-settings/output"' in body
    )

    body = _client().get(
        "/templates/metadata/table?tab=currencies"
    ).text
    assert (
        'href="https://prodmgmt.rms-ppe.com/home/reference-data/'
        'currencies/currency"' in body
    )

    body = _client().get(
        "/templates/metadata/table?tab=currency-schemes"
    ).text
    assert (
        'href="https://prodmgmt.rms-ppe.com/home/reference-data/'
        'currencies/currency-schemes"' in body
    )

    body = _client().get(
        "/templates/metadata/table?tab=event-rate-schemes"
    ).text
    assert (
        'href="https://prodmgmt.rms-ppe.com/riskmodeler/'
        'modelcomposer#event-rate-schemes"' in body
    )


def test_tab_rm_links_hidden_when_tenant_not_configured(workbench_db, monkeypatch):
    monkeypatch.setattr(settings, "risk_modeler_tenant_name", "")

    body = _client().get(
        "/templates/metadata/table?tab=model-profiles"
    ).text

    assert "Open in Risk Modeler" not in body


# ── Event-rate-scheme visibility toggle (workbench_is_active) ──────────────────

_VISIBILITY_URL = "/templates/metadata/event-rate-schemes/20/visibility"


def _scheme_active(irp_id: int) -> int:
    rows = execute(
        "SELECT workbench_is_active FROM irp_event_rate_scheme"
        " WHERE irp_id = :id",
        {"id": irp_id}, connection="WORKBENCH")
    return rows[0]["workbench_is_active"]


def test_admin_can_hide_and_show_a_scheme(workbench_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    client = _client(admin=True)

    # Unchecked checkbox: the is_active field is absent from the POST body.
    hide = client.post(
        f"{_VISIBILITY_URL}?tab=event-rate-schemes",
        data={"csrf_token": generate_csrf_token()},
        headers={"HX-Request": "true"},
    )

    assert hide.status_code == 200
    assert 'id="metadata-content"' in hide.text
    assert "checked" not in hide.text
    assert _scheme_active(20) == 0

    show = client.post(
        _VISIBILITY_URL,
        data={"csrf_token": generate_csrf_token(), "is_active": "1"},
    )

    assert show.status_code == 303
    assert show.headers["location"] == (
        "/templates/metadata?tab=event-rate-schemes")
    assert _scheme_active(20) == 1


def test_admin_sees_visibility_checkbox_in_fragment(workbench_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    body = _client(admin=True).get(
        "/templates/metadata/table?tab=event-rate-schemes").text

    assert 'type="checkbox"' in body
    assert "checked" in body


def test_non_admin_cannot_toggle_scheme_visibility(workbench_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    response = _client().post(
        _VISIBILITY_URL, data={"csrf_token": generate_csrf_token()})

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert _scheme_active(20) == 1


def test_bad_csrf_token_does_not_toggle_scheme_visibility(workbench_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    response = _client(admin=True).post(
        _VISIBILITY_URL, data={"csrf_token": "wrong"})

    assert response.status_code == 303
    assert _scheme_active(20) == 1
