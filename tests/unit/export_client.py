"""The TestClient and the seeded submission the spec-014 export route tests run
against: the real submissions router over the SQLite WORKBENCH and LOSS mirrors
(the test_submission_routes.py pattern). Each test module wraps these in its own
``client`` / ``deal`` / ``export`` fixtures."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.config import settings
from db import execute_one
from tests.unit.export_rows import (
    seed_analysis,
    seed_client,
    seed_edm_for,
    seed_lookup_versions,
    seed_submission,
)


def make_client(iteration2_db, loss_db) -> TestClient:
    from fastapi import FastAPI, Request
    from fastapi.templating import Jinja2Templates

    from app.auth.csrf import generate_csrf_token
    from app.routers import submissions
    from app.services import analysis_service
    from app.services.auth_service import CurrentUser
    from app.templating import TEMPLATE_DIRS

    user = CurrentUser(
        id=iteration2_db.user_a, email="analyst.a@example.com",
        display_name="Analyst A", session_id="s", role_codes=["analyst"],
        is_admin=False, must_change_password=False, entra_oid=None, is_active=True)

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request.state.user = user
            return await call_next(request)

    app = FastAPI()
    templates = Jinja2Templates(directory=TEMPLATE_DIRS)
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    templates.env.globals["default_perspective"] = analysis_service.DEFAULT_PERSPECTIVE
    templates.env.globals["default_perspective_label"] = (
        analysis_service.DEFAULT_PERSPECTIVE_LABEL)
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(submissions.router)
    test_client = TestClient(app, follow_redirects=False)
    test_client.db = iteration2_db
    test_client.templates = templates
    return test_client


def make_deal(test_client) -> dict:
    """One submission with two exportable analyses (``a``, ``b``) and one that
    cannot be exported (``bad``: its application analysis ID is not a number)."""
    submission_id = seed_submission(test_client.db.user_a, crm_ids=("CRM-1", "CRM-2"))
    edm_id = seed_edm_for(submission_id)
    a = seed_analysis(edm_id=edm_id, name="A", full_name="A long", irp_id="41958",
                      irp_app_analysis_id="41958", perspectives=("GU", "GR", "RL"))
    b = seed_analysis(edm_id=edm_id, name="B", full_name="B long", irp_id="41959",
                      irp_app_analysis_id="41959", perspectives=("GR", "RL", "RP"),
                      inserted_at="2026-09-10 07:00:00")
    bad = seed_analysis(edm_id=edm_id, name="Bad", full_name="Bad long",
                        irp_app_analysis_id="A-388", inserted_at="2026-09-10 06:00:00")
    seed_client(1, "Example Re")
    seed_client(2, "Retired", "N")
    seed_lookup_versions("25.0", "23.0")
    return {"submission_id": submission_id, "edm_id": edm_id, "a": a, "b": b, "bad": bad}


def make_export(test_client, deal) -> dict:
    """An accepted export of both exportable analyses at GR. ``url`` is the
    Retry and Close prefix; ``section`` is the exports table."""
    post_export(test_client, deal, [deal["a"], deal["b"]])
    export_id = execute_one(
        "SELECT export_id FROM stage.rwb_loss_result_manifest "
        "WHERE requested_from_submission_id = :s",
        {"s": deal["submission_id"]}, connection="LOSS")["export_id"]
    return {**deal, "export_id": export_id,
            "url": f"/submissions/{deal['submission_id']}/exports/{export_id}",
            "section": f"/submissions/{deal['submission_id']}/exports"}


def csrf() -> str:
    from app.auth.csrf import generate_csrf_token
    return generate_csrf_token()


def post_export(test_client, deal, analysis_ids, perspective="GR", htmx=False, **fields):
    data = {"csrf_token": csrf(), "perspective": perspective, "client_id": "1",
            "treaty_incept": "2026-04-01", "crm_id": "CRM-1",
            "data_vintage": "2025-12-31", "model_version": "25.0",
            "analysis_ids": list(analysis_ids), **fields}
    headers = {"HX-Request": "true"} if htmx else {}
    return test_client.post(f"/submissions/{deal['submission_id']}/exports", data=data,
                            headers=headers)


__all__ = ["make_client", "make_deal", "make_export", "csrf", "post_export"]
