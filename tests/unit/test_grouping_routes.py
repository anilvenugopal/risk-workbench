"""HTTP behavior of the group compose dialog (spec 012, contracts/routes.md)."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import analysis_service
from db import execute
from tests.unit.analysis_rows import seed_currency, seed_edm
from tests.unit.grouping_rows import (
    link_submission_edm,
    seed_own_analysis,
    seed_submission,
)


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
    from app.routers import submissions

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    templates.env.globals["default_perspective"] = (
        analysis_service.DEFAULT_PERSPECTIVE)
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(submissions.router)
    return TestClient(app, follow_redirects=False)


def _seeded_submission() -> dict:
    seed_currency()
    submission_id = seed_submission("Sub One")
    edm_id = seed_edm("EDM One")
    link_submission_edm(submission_id, edm_id)
    return {"submission_id": submission_id, "edm_id": edm_id,
            "a1": seed_own_analysis(edm_id, "CRE_P1_T1"),
            "a2": seed_own_analysis(edm_id, "CRE_P2_T1")}


def _csrf(client: TestClient) -> str:
    from app.auth.csrf import generate_csrf_token
    return generate_csrf_token()


def test_get_renders_the_dialog_with_prechecked_members(iteration2_db):
    ctx = _seeded_submission()

    response = _client().get(
        f"/submissions/{ctx['submission_id']}/analyses/group",
        params=[("analysis_ids", ctx["a1"]), ("analysis_ids", ctx["a2"])])

    assert response.status_code == 200
    assert 'value="CRE_Sub One_Group"' in response.text  # prefilled, editable
    assert response.text.count(" checked") >= 2          # both rows pre-checked
    assert "Propagate detailed output" in response.text
    assert "Create independent groups" not in response.text  # FR-006 / O-08


def test_get_blocks_with_fewer_than_two_eligible_members(iteration2_db):
    seed_currency()
    submission_id = seed_submission("Sub One")
    edm_id = seed_edm("EDM One")
    link_submission_edm(submission_id, edm_id)
    seed_own_analysis(edm_id, "CRE_P1_T1")

    response = _client().get(f"/submissions/{submission_id}/analyses/group")

    assert response.status_code == 200
    assert "at least two finished analyses" in response.text
    assert "GROUP NAME" not in response.text


def test_post_gate_failure_re_renders_at_422(iteration2_db):
    ctx = _seeded_submission()
    client = _client()

    response = client.post(
        f"/submissions/{ctx['submission_id']}/analyses/group",
        data={"csrf_token": _csrf(client), "member_ids": [ctx["a1"]],
              "group_name": "CRE_Sub One_Group", "currency_code": "USD",
              "currency_scheme": "RMS", "currency_vintage": "RL25"},
        headers={"HX-Request": "true"})

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#group-modal"
    assert "Pick at least two analyses to group." in response.text


def test_post_success_returns_204_with_the_triggers(iteration2_db):
    ctx = _seeded_submission()
    client = _client()

    response = client.post(
        f"/submissions/{ctx['submission_id']}/analyses/group",
        data={"csrf_token": _csrf(client),
              "member_ids": [ctx["a1"], ctx["a2"]],
              "group_name": "CRE_Sub One_Group", "currency_code": "USD",
              "currency_scheme": "RMS", "currency_vintage": "RL25",
              "propagate_detailed_output": "on"},
        headers={"HX-Request": "true"})

    assert response.status_code == 204
    trigger = json.loads(response.headers["HX-Trigger"])
    request_id = trigger["grouping-submitted"]["grouping_request_id"]
    assert trigger["rwb:toast"]["type"] == "success"
    job = execute(
        "SELECT input_data FROM rwb_job WHERE requestor_id = :r",
        {"r": request_id}, connection="WORKBENCH")
    assert json.loads(job[0]["input_data"])["propagate_detailed_losses"] is True
