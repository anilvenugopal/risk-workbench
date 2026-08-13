from __future__ import annotations

import json
from pathlib import Path

from app.services import irp_job_service
from db import execute, execute_command
from tests.unit.test_edm_sync import _client
from tests.unit.test_geohaz_service import _edm_with_portfolios


def _form(portfolio_ids: list[str], **changes) -> dict:
    from app.auth.csrf import generate_csrf_token

    data = {
        "csrf_token": generate_csrf_token(),
        "portfolio_ids": portfolio_ids,
        "data_version": "25.0",
        "perils": ["earthquake", "windstorm"],
        "missing_locations": "overwrite",
    }
    data.update(changes)
    return data


def test_detail_renders_selectable_and_ineligible_portfolios(iteration2_db):
    edm_id, portfolio_ids = _edm_with_portfolios(2)
    irp_job_service.record_submitted_irp_job(
        package_id=None, irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_ids[1], irp_id="801")

    body = _client().get(f"/edms/{edm_id}").text

    assert 'x-data="geohazSelection"' in body
    assert f'value="{portfolio_ids[0]}"' in body
    blocked = body[body.index(f'value="{portfolio_ids[1]}"'):]
    assert "disabled" in blocked.split(">", 1)[0]
    assert 'hx-get="/edms/' + edm_id + '/geohaz/new"' in body
    assert 'id="geohaz-modal-mount"' in body
    assert "--cols:42px 230px" in body
    assert "min-width:1357px" in body


def test_launch_modal_renders_defaults_and_selected_names(iteration2_db):
    edm_id, portfolio_ids = _edm_with_portfolios(2)

    response = _client().get(
        f"/edms/{edm_id}/geohaz/new",
        params=[("portfolio_ids", pid) for pid in portfolio_ids],
    )

    assert response.status_code == 200
    body = response.text
    assert 'id="geohaz-modal"' in body
    assert "Portfolio 1" in body and "Portfolio 2" in body
    assert 'value="25.0" selected' in body
    assert 'value="earthquake"' in body and 'value="windstorm"' in body
    assert body.count("checked") == 3
    assert "DLM" in body and "HD unavailable" in body
    assert "geocod" not in body.lower()


def test_launch_modal_renders_selection_and_gate_errors(iteration2_db):
    edm_id, portfolio_ids = _edm_with_portfolios(1)
    no_selection = _client().get(f"/edms/{edm_id}/geohaz/new")
    assert "Select at least one portfolio" in no_selection.text
    assert "Run on" not in no_selection.text

    irp_job_service.record_submitted_irp_job(
        package_id=None, irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_ids[0], irp_id="802")
    conflict = _client().get(
        f"/edms/{edm_id}/geohaz/new",
        params={"portfolio_ids": portfolio_ids[0]},
    )
    assert "already in progress for: Portfolio 1" in conflict.text

    empty_edm, _ = _edm_with_portfolios(0)
    gate = _client().get(f"/edms/{empty_edm}/geohaz/new")
    assert "requires at least one portfolio" in gate.text


def test_launch_post_rejects_bad_csrf_without_enqueuing(iteration2_db):
    edm_id, portfolio_ids = _edm_with_portfolios(1)
    data = _form(portfolio_ids)
    data["csrf_token"] = "wrong"

    response = _client().post(
        f"/edms/{edm_id}/geohaz", data=data,
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"
    assert execute(
        "SELECT id FROM rwb_job WHERE rwb_job_type='run_geohaz'",
        {}, connection="WORKBENCH") == []


def test_launch_post_rerenders_form_validation_and_conflict(iteration2_db):
    edm_id, portfolio_ids = _edm_with_portfolios(1)
    no_perils = _client().post(
        f"/edms/{edm_id}/geohaz",
        data=_form(portfolio_ids, perils=[]),
        headers={"HX-Request": "true"},
    )
    assert no_perils.status_code == 422
    assert "Select at least one peril" in no_perils.text
    assert "Run on 1 portfolio" in no_perils.text
    assert execute(
        "SELECT id FROM rwb_job WHERE rwb_job_type='run_geohaz'",
        {}, connection="WORKBENCH") == []

    irp_job_service.record_submitted_irp_job(
        package_id=None, irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_ids[0], irp_id="803")
    conflict = _client().post(
        f"/edms/{edm_id}/geohaz", data=_form(portfolio_ids),
        headers={"HX-Request": "true"},
    )
    assert conflict.status_code == 409
    assert "already in progress for: Portfolio 1" in conflict.text


def test_launch_post_enqueues_each_portfolio_and_returns_confirmation(
    iteration2_db,
):
    edm_id, portfolio_ids = _edm_with_portfolios(2)

    response = _client().post(
        f"/edms/{edm_id}/geohaz",
        data=_form(
            portfolio_ids,
            perils=["windstorm"],
            missing_locations="skip",
        ),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert 'id="edm-detail"' in response.text
    assert "</html>" not in response.text
    heads = execute(
        "SELECT requestor_id, input_data FROM rwb_job "
        "WHERE rwb_job_type='run_geohaz'",
        {}, connection="WORKBENCH")
    assert {str(row["requestor_id"]) for row in heads} == set(portfolio_ids)
    for row in heads:
        params = json.loads(row["input_data"])["params"]
        assert params["perils"] == ["windstorm"]
        assert params["missing_locations"] == "skip"
    toast = json.loads(response.headers["HX-Trigger"])["rwb:toast"]
    assert toast == {
        "message": "Hazard lookup queued for 2 portfolios.",
        "type": "success",
    }


def test_launch_post_without_htmx_uses_prg_and_confirmation_banner(iteration2_db):
    edm_id, portfolio_ids = _edm_with_portfolios(1)

    response = _client().post(
        f"/edms/{edm_id}/geohaz", data=_form(portfolio_ids))

    assert response.status_code == 303
    assert response.headers["location"] == f"/edms/{edm_id}?geohaz=queued"
    page = _client().get(response.headers["location"])
    assert "Hazard lookup queued" in page.text


def test_geohaz_alpine_components_are_registered_in_app_js():
    source = Path("app/static/js/app.js").read_text(encoding="utf-8")
    assert "Alpine.data('geohazSelection'" in source
    assert "Alpine.data('geohazModal'" in source
    assert "replaceFromError" in source


def test_detail_and_cell_render_live_state_then_stop_polling(iteration2_db):
    edm_id, [portfolio_id] = _edm_with_portfolios(1)
    job_id = irp_job_service.record_submitted_irp_job(
        package_id=None, irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_id, irp_id="940",
        request_params={
            "data_version": "25.0", "model_family": "DLM",
            "perils": ["earthquake"], "missing_locations": "overwrite",
        }, actor_id=iteration2_db.user_a)
    execute_command(
        "UPDATE irp_job SET status = 'RUNNING' WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")

    page = _client().get(f"/edms/{edm_id}").text
    cell_url = f"/edms/{edm_id}/portfolios/{portfolio_id}/geohaz-cell"
    assert "Hazard looked up?" in page
    assert f'hx-get="{cell_url}"' in page
    assert "Analyst A" in page
    assert "Earthquake" in page

    live = _client().get(cell_url)
    assert live.status_code == 200
    assert "RUNNING" in live.text
    assert 'hx-trigger="every 3s"' in live.text

    execute_command(
        "UPDATE irp_job SET status = 'FINISHED' WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")
    terminal = _client().get(cell_url)
    assert terminal.status_code == 200
    assert "Yes" in terminal.text
    assert "hx-trigger" not in terminal.text


def test_missing_portfolio_cell_is_terminal_empty_fragment(iteration2_db):
    edm_id, _ = _edm_with_portfolios(1)

    response = _client().get(
        f"/edms/{edm_id}/portfolios/not-a-portfolio/geohaz-cell")

    assert response.status_code == 404
    assert "geohaz-cell" in response.text
    assert "hx-trigger" not in response.text


def test_history_renders_layer_counts_zero_and_unavailable(iteration2_db):
    edm_id, [portfolio_id] = _edm_with_portfolios(1)
    with_counts = irp_job_service.record_submitted_irp_job(
        package_id=None, irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_id, irp_id="950",
        request_params={"perils": ["earthquake", "windstorm"]},
        actor_id=iteration2_db.user_a)
    without_counts = irp_job_service.record_submitted_irp_job(
        package_id=None, irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_id, irp_id="951",
        request_params={"perils": ["earthquake"]},
        actor_id=iteration2_db.user_a)
    execute_command(
        "UPDATE irp_job SET status = 'FINISHED', "
        "last_completion_result = :result WHERE id = :id",
        {"id": with_counts, "result": json.dumps({
            "status": "FINISHED",
            "details": {"summary": {"layers": [
                {"name": "earthquake", "locationsLookedUp": 14},
                {"name": "windstorm", "locationsLookedUp": 0},
            ]}},
        })},
        connection="WORKBENCH")
    execute_command(
        "UPDATE irp_job SET status = 'FAILED' WHERE id = :id",
        {"id": without_counts}, connection="WORKBENCH")

    body = _client().get(f"/edms/{edm_id}").text

    assert "Locations looked up" in body
    assert "Earthquake" in body and ">14<" in body
    assert "Windstorm" in body and ">0<" in body
    assert "Count unavailable" in body
