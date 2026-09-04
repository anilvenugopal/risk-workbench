"""HTTP behavior of the group compose dialog, its inspect step, and the Finish
fast path (spec 012, contracts/routes.md)."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import analysis_service
from app.services.irp_gateway import (
    GroupingPartitionKey,
    GroupingProblem,
    GroupingTreaty,
)
from db import execute, execute_one
from tests.unit.analysis_rows import seed_currency, seed_edm
from tests.unit.grouping_inspections import FINGERPRINT, seed_mixed_group
from tests.unit.grouping_rows import (
    link_submission_edm,
    seed_group,
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
    from app.templating import TEMPLATE_DIRS

    app = FastAPI()
    templates = Jinja2Templates(directory=TEMPLATE_DIRS)
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
    a1 = seed_own_analysis(edm_id, "CRE_P1_T1", irp_app_analysis_id="41001",
                           currency="USD")
    a2 = seed_own_analysis(edm_id, "CRE_P2_T1", irp_app_analysis_id="41002",
                           currency="USD")
    return {"submission_id": submission_id, "edm_id": edm_id, "a1": a1, "a2": a2,
            "member_ids": [a1, a2], "irp_ids": [_irp(a1), _irp(a2)]}


def _mixed_group(fake_irp) -> dict:
    """The sandbox HD + DLM + nested-group inspection (grouping_inspections)."""
    seed_currency()
    submission_id = seed_submission("Sub One")
    edm_id = seed_edm("EDM One")
    link_submission_edm(submission_id, edm_id)
    ctx = seed_mixed_group(fake_irp, submission_id, edm_id)
    return {"submission_id": submission_id, **ctx}


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
              "member_ids": member_ids or ctx["member_ids"]},
        headers={"HX-Request": "true"})


def _submit_form(client, ctx, **overrides) -> dict:
    form = {"csrf_token": _csrf(client),
            "member_ids": ctx["member_ids"],
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
    assert ">Finish<" in response.text
    assert f'hx-post="/submissions/{ctx["submission_id"]}/analyses/group/finish"' in response.text
    assert 'data-name="cre_p1_t1"' in response.text          # the search filter's key
    assert 'data-display="CRE_P1_T1"' in response.text        # the chips panel's label
    assert "Nothing selected yet" in response.text
    assert 'name="num_of_simulations"' not in response.text  # arrives with the inspection
    assert 'id="group-currency"' in response.text            # replaced by the inspection
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
    assert 'name="simulation_set_selection"' not in response.text
    assert ">Simulation set</th>" not in response.text
    assert '<div class="insp-resolved" title="Scheme 101">Scheme 101</div>' in response.text
    assert '<input type="hidden" name="num_of_simulations" value="1">' in response.text
    assert "SIMULATION PERIODS" not in response.text
    # both members ran in USD: the group currency is prefilled with it
    assert '<div id="group-currency" hx-swap-oob="true">' in response.text
    assert '<option value="USD" selected>' in response.text
    assert "All members ran in USD." in response.text
    fingerprint = f"v1:fake-{ctx['irp_ids'][0]},{ctx['irp_ids'][1]}"
    assert f'name="expected_inspection_fingerprint" value="{fingerprint}"' in response.text
    assert response.text.count('name="inspected_analysis_ids"') == 2
    assert "data-inspection-ready" in response.text
    assert "<b>0</b> treaty mismatches<" in response.text
    assert "No treaty number appears with different loss-affecting terms" in response.text
    assert "<dt>Treaties</dt><dd>No mismatches</dd>" in response.text


def _treaty(analysis_id: int, treaty_id: int | None, **overrides) -> GroupingTreaty:
    terms = {"treatyType": "CATA", "effectiveDate": "2026-01-01T00:00:00.000Z",
             "expirationDate": "2026-12-31T00:00:00.000Z",
             "attachmentPoint": 5_000_000, "occurrenceLimit": 10_000_000,
             "riskLimit": 1_000_000, "currency": "USD"}
    terms.update(overrides)
    return GroupingTreaty(analysis_id=analysis_id, treaty_id=treaty_id,
                          treaty_number="XOL-2026-01", terms=terms)


def test_inspect_tables_a_treaty_mismatch_without_blocking(iteration2_db, fake_irp):
    ctx = _seeded_submission()
    ids = ctx["irp_ids"]
    fake_irp.seed_grouping_inspection(ids, warnings=(GroupingProblem(
        code="inconsistent_treaty_terms",
        message="Treaty number XOL-2026-01 has inconsistent loss-affecting terms.",
        analysis_ids=tuple(ids), treaty_numbers=("XOL-2026-01",),
        treaty_ids=(88412, 90177),
        differing_fields=("attachmentPoint", "occurrenceLimit"),
        treaties=(_treaty(ids[0], 88412),
                  _treaty(ids[1], 90177, attachmentPoint=2_500_000,
                          occurrenceLimit=2_000_000))),))

    response = _inspect(_client(), ctx)

    assert response.status_code == 200
    assert "<b>XOL-2026-01</b>" in response.text
    assert "2 treaties · 2 analyses" in response.text
    assert "Differs on Attachment Point, Occurrence Limit" in response.text
    for label in ("Analysis ID", "Treaty ID", "Treaty Number", "Treaty Type",
                  "Effective Date", "Expiration Date", "Per Risk Limit",
                  "Currency"):
        assert f">{label}</th>" in response.text
    # the two differing terms mark their headers, the seven others do not
    assert response.text.count('insp-table__key insp-diff"') == 2
    assert "CRE_P1_T1" in response.text and "CRE_P2_T1" in response.text
    # the Analysis ID column carries the web-UI ids, not the Platform ids
    assert '<td class="n">41001</td>' in response.text
    assert '<td class="n">41002</td>' in response.text
    assert f'<td class="n">{ids[0]}</td>' not in response.text
    assert '<td class="n">88412</td>' in response.text
    assert '<td class="n insp-diff">5,000,000</td>' in response.text
    assert '<td class="n insp-diff">2,500,000</td>' in response.text
    assert '<td class="n">Catastrophe</td>' in response.text
    assert '<td class="n">2026-01-01</td>' in response.text
    assert "Treaty mismatches do not stop the grouping." in response.text
    assert "<b>1</b> treaty mismatch<" in response.text
    assert ('<dt>Treaties</dt><dd><span class="badge badge--warning badge--sm">1 mismatch</span> '
            '<span class="tag-empty">XOL-2026-01</span></dd>') in response.text
    assert "data-inspection-ready" in response.text
    assert 'name="expected_inspection_fingerprint"' in response.text


def test_inspect_shows_an_em_dash_for_a_treaty_without_an_id(iteration2_db, fake_irp):
    ctx = _seeded_submission()
    ids = ctx["irp_ids"]
    fake_irp.seed_grouping_inspection(ids, warnings=(GroupingProblem(
        code="inconsistent_treaty_terms",
        message="Treaty number XOL-2026-01 has inconsistent loss-affecting terms.",
        analysis_ids=tuple(ids), treaty_numbers=("XOL-2026-01",),
        treaty_ids=(88412,), differing_fields=("currency",),
        treaties=(_treaty(ids[0], 88412),
                  _treaty(ids[1], None, currency="CAD"))),))

    response = _inspect(_client(), ctx)

    assert response.status_code == 200
    assert '<td class="n"><span class="na">&mdash;</span></td>' in response.text
    assert '<td class="n insp-diff">CAD</td>' in response.text


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


def test_inspect_offers_a_simulation_set_per_elt_partition_of_a_plt_group(
        iteration2_db, fake_irp):
    ctx = _mixed_group(fake_irp)

    response = _inspect(_client(), ctx)

    html = response.text
    assert response.status_code == 200
    assert "These members cannot be grouped" not in html
    assert "Group output <b>PLT</b>" in html
    assert ">Simulation set</th>" in html
    assert html.count('name="simulation_set_selection"') == 2
    assert 'aria-label="Simulation set for EQ / NA / 17.0"' in html
    assert 'aria-label="Simulation set for WS / NA / 11.0"' in html
    # the HD partition keeps PET 15: its row names the PET and has no dropdown
    jp_row = next(r for r in html.split("<tr>") if "<td>WS</td><td>JP</td>" in r)
    assert "<select" not in jp_row
    assert ("RMS V2.0 Stochastic Event Rates - Typhoon Events Only "
            "(50,000 periods)") in jp_row
    eq_values = [json.loads(v) for v in _option_values(html)
                 if '"simulation_set_id"' in v and '"EQ"' in v]
    assert [v["simulation_set_id"] for v in eq_values] == [83, 84, 85, 86, 87]
    assert eq_values[-1] == {"peril_code": "EQ", "region_code": "NA",
                             "model_version": "17.0", "simulation_set_id": 87}
    assert "North America Earthquake Stochastic (100,000 periods)" in html
    assert '<option value="" selected>Choose a simulation set&hellip;</option>' in html
    # set 147's reference row names scheme 739; the option says nothing about it
    option_147 = next(chunk for chunk in html.split("<option ")
                      if '"simulation_set_id": 147' in chunk)
    assert "739" not in option_147 and "scheme" not in option_147
    assert html.count('name="event_rate_selection"') == 1  # NA/WS only
    assert '<option value="50000" selected>50,000</option>' in html
    assert f'name="expected_inspection_fingerprint" value="{FINGERPRINT}"' in html
    assert "data-inspection-ready" in html


def test_inspect_renders_a_plt_group_with_the_period_dropdown(
        iteration2_db, fake_irp):
    ctx = _seeded_submission()
    ids = ctx["irp_ids"]
    fake_irp.seed_grouping_inspection(
        ids, output_loss_table="PLT", periods={ids[0]: 10000, ids[1]: 200000})

    response = _inspect(_client(), ctx)

    assert "Group output <b>PLT</b>" in response.text
    assert "SIMULATION PERIODS" in response.text
    assert ('<select class="wf-field__input" id="group-num-simulations" '
            'name="num_of_simulations">') in response.text
    # the nine Risk Modeler options, 50,000 preselected whatever the members ran
    assert response.text.count("<option value=") >= 9 + 1
    for n in ("3,125", "6,250", "12,500", "25,000", "100,000", "200,000",
              "400,000", "800,000"):
        assert f">{n}</option>" in response.text
    assert '<option value="50000" selected>50,000</option>' in response.text
    assert '<option value="200000">200,000</option>' in response.text
    assert "Target group PLT length" in response.text
    assert "Largest member: 200,000." in response.text
    # both members are HD: the column names each PET and offers no choice
    assert ">Simulation set</th>" in response.text
    assert 'name="simulation_set_selection"' not in response.text
    assert ('<div class="insp-resolved" title="PET 900 rates">'
            "PET 900 rates (10,000 periods)</div>") in response.text
    assert "PET 901 rates (200,000 periods)" in response.text


# ── the group currency prefill (FR-004) ──────────────────────────────────────────

def test_inspect_defaults_the_currency_when_the_members_differ(
        iteration2_db, fake_irp):
    ctx = _seeded_submission()
    cad = seed_own_analysis(ctx["edm_id"], "CRE_P3_T1", currency="CAD")

    response = _inspect(_client(), ctx, member_ids=[ctx["a1"], cad])

    assert response.status_code == 200
    assert '<option value="USD" selected>' in response.text
    assert "Members ran in USD and CAD. Defaulting to USD." in response.text


def test_inspect_defaults_the_currency_when_a_member_has_none_recorded(
        iteration2_db, fake_irp):
    ctx = _seeded_submission()
    unknown = seed_own_analysis(ctx["edm_id"], "CRE_P3_T1")

    response = _inspect(_client(), ctx, member_ids=[ctx["a1"], unknown])

    assert response.status_code == 200
    assert '<option value="USD" selected>' in response.text
    assert "A member's currency is not recorded. Defaulting to USD." in response.text


def test_inspect_read_failure_leaves_the_currency_block_alone(
        iteration2_db, fake_irp):
    ctx = _seeded_submission()
    fake_irp.grouping_inspect_error = "analysis 80002 not found"

    response = _inspect(_client(), ctx)

    assert response.status_code == 422
    assert 'id="group-currency"' not in response.text


def test_inspect_blocked_names_the_problem_and_offers_no_submit(
        iteration2_db, fake_irp):
    ctx = _seeded_submission()
    ids = ctx["irp_ids"]
    fake_irp.seed_grouping_inspection(ids, output_loss_table="PLT", blocking=(
        GroupingProblem(
            code="simulation_set_mapping_missing",
            message=("No simulation set is available for peril WS, region NA, "
                     "and model version 11.0."),
            analysis_ids=tuple(ids),
            partition=GroupingPartitionKey("WS", "NA", "11.0")),))

    response = _inspect(_client(), ctx)

    assert response.status_code == 200
    assert "These members cannot be grouped" in response.text
    assert ("No simulation set is available for peril WS, region NA, "
            "and model version 11.0.") in response.text
    assert 'name="simulation_set_selection"' not in response.text
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


# ── the Finish fast path (FR-025) ────────────────────────────────────────────────

@pytest.fixture
def env_vintage(monkeypatch):
    """The env vintage default the Finish submit relies on (seed_currency's RL25)."""
    from app.config import settings
    monkeypatch.setattr(settings, "default_analysis_currency_vintage", "RL25")


def _finish(client, ctx, member_ids=None, group_name="CRE_Sub One_Group"):
    return client.post(
        f"/submissions/{ctx['submission_id']}/analyses/group/finish",
        data={"csrf_token": _csrf(client),
              "member_ids": member_ids or ctx["member_ids"],
              "group_name": group_name},
        headers={"HX-Request": "true"})


def _no_grouping_job() -> None:
    assert execute("SELECT 1 FROM rwb_job WHERE rwb_job_type = 'submit_grouping'",
                   {}, connection="WORKBENCH") == []


def test_finish_inspects_and_submits_a_clean_group_in_the_members_currency(
        iteration2_db, fake_irp, env_vintage):
    ctx = _seeded_submission()

    response = _finish(_client(), ctx)

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#group-modal"
    assert response.headers["HX-Reswap"] == "innerHTML"
    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger["rwb:toast"]["type"] == "success"
    assert fake_irp.grouping_inspects == [ctx["irp_ids"]]
    # the confirmation pane replaces the dialog
    assert "Group submitted" in response.text
    assert "Inspection passed." in response.text
    assert "<dt>Group name</dt><dd>CRE_Sub One_Group</dd>" in response.text
    assert "<dt>Output</dt><dd>ELT</dd>" in response.text
    assert "<dt>Event-rate schemes</dt><dd>No conflicts</dd>" in response.text
    assert "<dt>Treaties</dt><dd>No mismatches</dd>" in response.text
    assert "<dt>Currency</dt><dd>USD &mdash; all members</dd>" in response.text
    assert "Members (2)" in response.text
    assert "CRE_P1_T1" in response.text and "CRE_P2_T1" in response.text
    assert ">Close<" in response.text and "GROUP NAME" not in response.text
    # the plan: the members' currency, env scheme and vintage, ELT, Propagate ON
    plan = json.loads(execute(
        "SELECT input_data FROM rwb_job WHERE requestor_id = :r",
        {"r": trigger["grouping-submitted"]["grouping_request_id"]},
        connection="WORKBENCH")[0]["input_data"])
    assert plan["currency"] == {"code": "USD", "scheme": "RMS", "vintage": "RL25",
                               "asOfDate": "2025-05-28"}
    assert plan["num_of_simulations"] == 1
    assert plan["propagate_detailed_losses"] is True
    assert plan["event_rate_selections"] == []
    assert plan["simulation_set_selections"] == []
    assert plan["expected_inspection_fingerprint"] == (
        f"v1:fake-{ctx['irp_ids'][0]},{ctx['irp_ids'][1]}")
    assert [m["irp_id"] for m in plan["members"]] == ctx["irp_ids"]


def test_finish_confirmation_names_the_suffixed_group_and_the_treaty_mismatch(
        iteration2_db, fake_irp, env_vintage):
    ctx = _seeded_submission()
    ids = ctx["irp_ids"]
    seed_group(ctx["submission_id"], "CRE_Sub One_Group")
    fake_irp.seed_grouping_inspection(ids, warnings=(GroupingProblem(
        code="inconsistent_treaty_terms",
        message="Treaty number XOL-2026-01 has inconsistent loss-affecting terms.",
        analysis_ids=tuple(ids), treaty_numbers=("XOL-2026-01",),
        treaty_ids=(88412, 90177), differing_fields=("attachmentPoint",),
        treaties=(_treaty(ids[0], 88412),
                  _treaty(ids[1], 90177, attachmentPoint=2_500_000))),))

    response = _finish(_client(), ctx)

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#group-modal"
    assert "<dt>Group name</dt><dd>CRE_Sub One_Group_2</dd>" in response.text
    assert ('<dt>Treaties</dt><dd><span class="badge badge--warning badge--sm">1 mismatch</span> '
            '<span class="tag-empty">XOL-2026-01</span></dd>') in response.text


def _assert_finish_stopped(response, fake_irp, ctx) -> None:
    assert response.status_code == 200
    assert "HX-Retarget" not in response.headers
    assert "HX-Trigger" not in response.headers
    assert ("Finish could not submit this group. Review the inspection and "
            "continue with Next.") in response.text
    assert "Group submitted" not in response.text
    assert fake_irp.grouping_inspects == [ctx["irp_ids"]]
    _no_grouping_job()


def test_finish_stops_on_a_scheme_conflict_and_renders_the_inspection(
        iteration2_db, fake_irp, env_vintage):
    ctx = _seeded_submission()
    fake_irp.seed_grouping_inspection(ctx["irp_ids"], conflicting=[101, 205])

    response = _finish(_client(), ctx)

    _assert_finish_stopped(response, fake_irp, ctx)
    # screen 2 is complete: the analyst chooses and continues with Next
    assert response.text.count('name="event_rate_selection"') == 1
    assert "data-inspection-ready" in response.text
    assert '<div id="group-currency" hx-swap-oob="true">' in response.text
    assert "All members ran in USD." in response.text


def test_finish_stops_on_plt_output(iteration2_db, fake_irp, env_vintage):
    ctx = _seeded_submission()
    ids = ctx["irp_ids"]
    fake_irp.seed_grouping_inspection(
        ids, output_loss_table="PLT", periods={ids[0]: 10000, ids[1]: 200000})

    response = _finish(_client(), ctx)

    _assert_finish_stopped(response, fake_irp, ctx)
    assert "Group output <b>PLT</b>" in response.text
    assert '<option value="50000" selected>50,000</option>' in response.text


def test_finish_stops_when_the_members_currencies_differ(
        iteration2_db, fake_irp, env_vintage):
    ctx = _seeded_submission()
    cad = seed_own_analysis(ctx["edm_id"], "CRE_P3_T1", currency="CAD")
    ctx["member_ids"] = [ctx["a1"], cad]
    ctx["irp_ids"] = [_irp(ctx["a1"]), _irp(cad)]

    response = _finish(_client(), ctx)

    _assert_finish_stopped(response, fake_irp, ctx)
    assert "Members ran in USD and CAD. Defaulting to USD." in response.text


def test_finish_stops_when_the_members_cannot_be_grouped(
        iteration2_db, fake_irp, env_vintage):
    ctx = _seeded_submission()
    ids = ctx["irp_ids"]
    fake_irp.seed_grouping_inspection(ids, blocking=(GroupingProblem(
        code="simulation_set_mapping_missing",
        message="No simulation set is available for peril WS, region NA, and model version 11.0.",
        analysis_ids=tuple(ids),
        partition=GroupingPartitionKey("WS", "NA", "11.0")),))

    response = _finish(_client(), ctx)

    _assert_finish_stopped(response, fake_irp, ctx)
    assert "These members cannot be grouped" in response.text
    assert "data-inspection-ready" not in response.text


def test_finish_stops_without_an_env_vintage_default(iteration2_db, fake_irp):
    ctx = _seeded_submission()

    response = _finish(_client(), ctx)

    _assert_finish_stopped(response, fake_irp, ctx)


def test_finish_gate_failure_renders_the_error_at_422(iteration2_db, fake_irp,
                                                      env_vintage):
    ctx = _seeded_submission()

    response = _finish(_client(), ctx, member_ids=[ctx["a1"]])

    assert response.status_code == 422
    assert "Pick at least two analyses to group." in response.text
    assert ">Retry<" in response.text
    assert fake_irp.grouping_inspects == []
    _no_grouping_job()


def test_finish_platform_failure_renders_the_cause_at_422(
        iteration2_db, fake_irp, env_vintage):
    ctx = _seeded_submission()
    fake_irp.grouping_inspect_error = "analysis 80002 not found"

    response = _finish(_client(), ctx)

    assert response.status_code == 422
    assert "Inspection failed: analysis 80002 not found" in response.text
    _no_grouping_job()


def test_post_carries_the_simulation_set_choices_and_the_fingerprint(
        iteration2_db, fake_irp):
    ctx = _mixed_group(fake_irp)
    client = _client()
    scheme = json.dumps({"peril_code": "WS", "region_code": "NA",
                         "model_version": "11.0", "event_rate_scheme_id": 738})
    ws_set = json.dumps({"peril_code": "WS", "region_code": "NA",
                         "model_version": "11.0", "simulation_set_id": 147})
    eq_set = json.dumps({"peril_code": "EQ", "region_code": "NA",
                         "model_version": "17.0", "simulation_set_id": 87})

    response = client.post(
        f"/submissions/{ctx['submission_id']}/analyses/group",
        data=_submit_form(client, ctx, num_of_simulations="50000",
                          expected_inspection_fingerprint=FINGERPRINT,
                          event_rate_selection=[scheme],
                          simulation_set_selection=[ws_set, eq_set]),
        headers={"HX-Request": "true"})

    assert response.status_code == 204
    request_id = json.loads(response.headers["HX-Trigger"])[
        "grouping-submitted"]["grouping_request_id"]
    plan = json.loads(execute(
        "SELECT input_data FROM rwb_job WHERE requestor_id = :r",
        {"r": request_id}, connection="WORKBENCH")[0]["input_data"])
    assert plan["event_rate_selections"] == [json.loads(scheme)]
    assert plan["simulation_set_selections"] == [json.loads(ws_set), json.loads(eq_set)]
    assert plan["expected_inspection_fingerprint"] == FINGERPRINT
    assert plan["num_of_simulations"] == 50000


def test_post_rejects_duplicate_simulation_set_selections_at_422(iteration2_db):
    ctx = _seeded_submission()
    client = _client()
    chosen = json.dumps({"peril_code": "WS", "region_code": "NA",
                         "model_version": "11.0", "simulation_set_id": 147})

    response = client.post(
        f"/submissions/{ctx['submission_id']}/analyses/group",
        data=_submit_form(client, ctx, simulation_set_selection=[chosen, chosen]),
        headers={"HX-Request": "true"})

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#group-submit-errors"
    assert ("Choose a simulation set for every partition converted from ELT to PLT."
            in response.text)
    assert "GROUP NAME" not in response.text  # the dialog keeps its choices
