"""Route tests for the read-only analysis metadata page."""

from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.auth.csrf import generate_csrf_token
from app.config import settings
from app.routers import templates
from app.services import rwb_job_service
from app.services.auth_service import CurrentUser
from app.workers import dispatch, metadata_jobs
from db import execute


class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = CurrentUser(
            id="00000000-0000-0000-0000-000000000001",
            email="analyst@example.com",
            display_name="Test Analyst",
            session_id="session-id",
            role_codes=["analyst"],
            is_admin=False,
            must_change_password=False,
            entra_oid=None,
            is_active=True,
        )
        return await call_next(request)


def _client() -> TestClient:
    app = FastAPI()
    renderer = Jinja2Templates(directory="app/templates")
    renderer.env.globals["app_env"] = settings.app_env
    renderer.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    renderer.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    renderer.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = renderer
    app.add_middleware(_InjectUser)
    app.include_router(templates.router)
    return TestClient(app, follow_redirects=False)


def _flat(body: str) -> str:
    return re.sub(r"\s+", " ", body)


def test_metadata_page_renders_all_four_read_only_tabs(iteration2_db):
    body = _client().get("/templates/metadata").text

    assert "Model Profiles" in body
    assert "Output Profiles" in body
    assert "Event Rate Schemes" in body
    assert "Currencies" in body


def test_no_metadata_tab_has_create_or_edit_controls(iteration2_db):
    client = _client()
    for tab in (
        "model-profiles", "output-profiles", "event-rate-schemes", "currencies"
    ):
        body = client.get(f"/templates/metadata/table?tab={tab}").text
        assert 'href="/templates/analysis-templates/new"' not in body
        assert ">Create<" not in body
        assert ">Edit<" not in body


def test_metadata_fragment_filters_and_uses_wheel_marker_derivation(
    iteration2_db, fake_irp,
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
    iteration2_db,
):
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql(
            """
            INSERT INTO irp_model_profile
              (id, irp_id, name, is_accumulation, software_version_code,
               rms_default, inserted_at, updated_at)
            VALUES
              ('accumulation-profile', 99, 'Global Accumulation', 1, 'HDv3.0',
               0, '2026-08-18 10:00:00', '2026-08-18 10:00:00')
            """
        )

    body = _client().get(
        "/templates/metadata/table?tab=model-profiles&q=global"
    ).text

    assert "Global Accumulation" in body
    assert ">Accumulation<" in body
    assert ">HD<" not in body


def test_sync_enqueues_and_dispatches_once_then_refuses_second_request(
    iteration2_db,
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
    iteration2_db, fake_irp,
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


def test_bad_csrf_token_does_not_enqueue_sync(iteration2_db):
    response = _client().post(
        "/templates/metadata/sync", data={"csrf_token": "wrong"}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/templates/metadata"
    assert execute("SELECT id FROM rwb_job", connection="WORKBENCH") == []
