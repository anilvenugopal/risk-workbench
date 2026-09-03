"""HTTP behavior of the group compose dialog and its inspect step (spec 012,
contracts/routes.md)."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import analysis_service
from app.services.irp_gateway import GroupingPartitionKey, GroupingProblem
from db import execute, execute_one
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
    a1 = seed_own_analysis(edm_id, "CRE_P1_T1")
    a2 = seed_own_analysis(edm_id, "CRE_P2_T1")
    return {"submission_id": submission_id, "edm_id": edm_id, "a1": a1, "a2": a2,
            "irp_ids": [_irp(a1), _irp(a2)]}


def _irp(analysis_id: str) -> int:
    return int(execute_one("SELECT irp_id FROM irp_analysis WHERE id = :id",
                           {"id": analysis_id}, connection="WORKBENCH")["irp_id"])


def _csrf(client: TestClient) -> str:
    from app.auth.csrf import generate_csrf_token
    return generate_csrf_token()


def _inspect(client, ctx, member_ids=None):
    return client.post(
        f"/submissions/{ctx['submission_id']}/analyses/group/inspect",
        data={"csrf_token": _csrf(client),
              "member_ids": member_ids or [ctx["a1"], ctx["a2"]]},
        headers={"HX-Request": "true"})


def _submit_form(client, ctx, **overrides) -> dict:
    form = {"csrf_token": _csrf(client),
            "member_ids": [ctx["a1"], ctx["a2"]],
            "group_name": "CRE_Sub One_Group", "currency_code": "USD",
            "currency_scheme": "RMS", "currency_vintage": "RL25",
            "propagate_detailed_output": "on",
            "num_of_simulations": "1",
            "expected_inspection_fingerprint": "v1:" + "a" * 64,
            "inspected_analysis_ids": [str(i) for i in ctx["irp_ids"]]}
    form.update(overrides)
    return form


# ── the dialog ───────────────────────────────────────────────────────────────────

def test_get_renders_the_dialog_with_prechecked_members(iteration2_db):
    ctx = _seeded_submission()

    response = _client().get(
        f"/submissions/{ctx['submission_id']}/analyses/group",
        params=[("analysis_ids", ctx["a1"]), ("analysis_ids", ctx["a2"])])

    assert response.status_code == 200
    assert 'value="CRE_Sub One_Group"' in response.text  # prefilled, editable
    assert response.text.count(" checked") >= 2          # both rows pre-checked
    assert "Propagate detailed output" in response.text
    assert ">Next<" in response.text and "Inspect members" not in response.text
    assert 'data-name="cre_p1_t1"' in response.text          # the search filter's key
    assert 'name="num_of_simulations"' not in response.text  # arrives with the inspection
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


# ── the inspect step ─────────────────────────────────────────────────────────────

def test_inspect_renders_an_elt_group_ready_to_submit(iteration2_db, fake_irp):
    ctx = _seeded_submission()

    response = _inspect(_client(), ctx)

    assert response.status_code == 200
    assert "Group output <b>ELT</b>" in response.text
    assert "<td>WS</td><td>NA</td>" in response.text
    assert "RL25 · 11.0" in response.text
    assert "CRE_P1_T1" in response.text and "CRE_P2_T1" in response.text
    assert 'name="event_rate_selection"' not in response.text
    assert '<div class="insp-resolved" title="Scheme 101">Scheme 101</div>' in response.text
    assert '<input type="hidden" name="num_of_simulations" value="1">' in response.text
    assert "SIMULATION COUNT" not in response.text
    fingerprint = f"v1:fake-{ctx['irp_ids'][0]},{ctx['irp_ids'][1]}"
    assert f'name="expected_inspection_fingerprint" value="{fingerprint}"' in response.text
    assert response.text.count('name="inspected_analysis_ids"') == 2
    assert "data-inspection-ready" in response.text


def test_inspect_offers_only_the_members_schemes_for_a_conflict(
        iteration2_db, fake_irp):
    ctx = _seeded_submission()
    fake_irp.seed_grouping_inspection(ctx["irp_ids"], conflicting=[101, 205])

    response = _inspect(_client(), ctx)

    assert response.status_code == 200
    assert response.text.count('name="event_rate_selection"') == 1
    assert 'data-partition="WS / NA / 11.0"' in response.text
    assert "Scheme 101 (1 member)" in response.text
    assert "Scheme 205 (1 member)" in response.text
    assert "<b>1</b> scheme mismatch<" in response.text
    assert " required" not in response.text  # the Alpine gate enforces the choice
    option_values = [
        json.loads(v) for v in _option_values(response.text)]
    assert option_values == [
        {"peril_code": "WS", "region_code": "NA", "model_version": "11.0",
         "event_rate_scheme_id": 101},
        {"peril_code": "WS", "region_code": "NA", "model_version": "11.0",
         "event_rate_scheme_id": 205}]
    assert "data-inspection-ready" in response.text


def _option_values(html: str) -> list[str]:
    values = []
    for chunk in html.split("<option value='")[1:]:
        values.append(chunk.split("'", 1)[0])
    return values


def test_inspect_renders_a_plt_group_with_the_suggested_length(
        iteration2_db, fake_irp):
    ctx = _seeded_submission()
    ids = ctx["irp_ids"]
    fake_irp.seed_grouping_inspection(
        ids, output_loss_table="PLT", periods={ids[0]: 10000, ids[1]: 50000})

    response = _inspect(_client(), ctx)

    assert "Group output <b>PLT</b>" in response.text
    assert 'name="num_of_simulations" min="1" step="1" value="50000"' in response.text
    assert "Target group PLT length" in response.text
    assert "Largest member: 50,000." in response.text


def test_inspect_blocked_names_the_problem_and_offers_no_submit(
        iteration2_db, fake_irp):
    ctx = _seeded_submission()
    ids = ctx["irp_ids"]
    fake_irp.seed_grouping_inspection(ids, blocking=(GroupingProblem(
        code="simulation_set_mapping_missing",
        message="No simulation set maps WS/NA/11.0 under scheme 101.",
        analysis_ids=tuple(ids),
        partition=GroupingPartitionKey("WS", "NA", "11.0")),))

    response = _inspect(_client(), ctx)

    assert response.status_code == 200
    assert "These members cannot be grouped" in response.text
    assert "No simulation set maps WS/NA/11.0 under scheme 101." in response.text
    assert "<li>CRE_P1_T1</li>" in response.text and "<li>CRE_P2_T1</li>" in response.text
    assert "data-inspection-ready" not in response.text
    assert 'name="expected_inspection_fingerprint"' not in response.text
    assert 'name="num_of_simulations"' not in response.text
    assert "Group name" not in response.text  # screen 3's summary stays empty


def test_inspect_gate_failure_renders_the_error_at_422(iteration2_db, fake_irp):
    ctx = _seeded_submission()

    response = _inspect(_client(), ctx, member_ids=[ctx["a1"]])

    assert response.status_code == 422
    assert "Pick at least two analyses to group." in response.text
    assert ">Retry<" in response.text
    assert fake_irp.grouping_inspects == []


def test_inspect_platform_failure_renders_the_cause_at_422(iteration2_db, fake_irp):
    ctx = _seeded_submission()
    fake_irp.grouping_inspect_error = "analysis 80002 not found"

    response = _inspect(_client(), ctx)

    assert response.status_code == 422
    assert "Inspection failed: analysis 80002 not found" in response.text


# ── the submit ───────────────────────────────────────────────────────────────────

def test_post_gate_failure_re_renders_at_422(iteration2_db):
    ctx = _seeded_submission()
    client = _client()

    response = client.post(
        f"/submissions/{ctx['submission_id']}/analyses/group",
        data=_submit_form(client, ctx, member_ids=[ctx["a1"]],
                          inspected_analysis_ids=[str(ctx["irp_ids"][0])]),
        headers={"HX-Request": "true"})

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#group-submit-errors"
    assert response.headers["HX-Reswap"] == "innerHTML"
    assert "Pick at least two analyses to group." in response.text
    assert "GROUP NAME" not in response.text  # the dialog is not re-rendered


def test_post_without_an_inspection_re_renders_at_422(iteration2_db):
    ctx = _seeded_submission()
    client = _client()

    response = client.post(
        f"/submissions/{ctx['submission_id']}/analyses/group",
        data=_submit_form(client, ctx, expected_inspection_fingerprint="",
                          inspected_analysis_ids=[]),
        headers={"HX-Request": "true"})

    assert response.status_code == 422
    assert "Inspect the members before grouping." in response.text
    assert "Members changed since inspection. Inspect again." in response.text


def test_post_success_returns_204_with_the_triggers(iteration2_db):
    ctx = _seeded_submission()
    client = _client()
    selection = json.dumps({"peril_code": "WS", "region_code": "NA",
                            "model_version": "11.0", "event_rate_scheme_id": 205})

    response = client.post(
        f"/submissions/{ctx['submission_id']}/analyses/group",
        data=_submit_form(client, ctx, num_of_simulations="50000",
                          event_rate_selection=[selection]),
        headers={"HX-Request": "true"})

    assert response.status_code == 204
    trigger = json.loads(response.headers["HX-Trigger"])
    request_id = trigger["grouping-submitted"]["grouping_request_id"]
    assert trigger["rwb:toast"]["type"] == "success"
    job = execute(
        "SELECT input_data FROM rwb_job WHERE requestor_id = :r",
        {"r": request_id}, connection="WORKBENCH")
    plan = json.loads(job[0]["input_data"])
    assert plan["propagate_detailed_losses"] is True
    assert plan["num_of_simulations"] == 50000
    assert plan["event_rate_selections"] == [json.loads(selection)]
    assert plan["expected_inspection_fingerprint"] == "v1:" + "a" * 64
    assert [m["irp_id"] for m in plan["members"]] == ctx["irp_ids"]
