from __future__ import annotations

import json

from app.services import analysis_service, edm_service, irp_job_service
from app.services._common import SubmissionRef
from db import execute, execute_command
from tests.unit.conftest import edm_with_portfolios as _edm_with_portfolios
from tests.unit.test_edm_sync import _client


def _form(portfolio_ids: list[str]) -> dict:
    from app.auth.csrf import generate_csrf_token

    data = {
        "csrf_token": generate_csrf_token(),
        "portfolio_ids": portfolio_ids,
    }
    return data


def _context(edm_id: str) -> edm_service.ContextualEdmDetail:
    edm = edm_service.get_edm_detail(edm_id)
    assert edm is not None
    return edm_service.ContextualEdmDetail(
        edm=edm,
        submission=SubmissionRef(id="submission-a", name="Submission A"),
        edm_choices=[SubmissionRef(id=edm_id, name=edm.name)],
        rdms=[analysis_service.BrokerAnalysisGroup(
            rdm_id="rdm-1", rdm_name="Submission RDM", rdm_irp_id=201,
            analysis_count=2)],
    )


def test_detail_renders_selectable_and_ineligible_portfolios(iteration2_db):
    edm_id, portfolio_ids = _edm_with_portfolios(2)
    irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_ids[1], irp_id="801")

    body = _client().get(f"/edms/{edm_id}").text

    assert "checkPicks({ name: 'portfolio_ids', observe: true })" in body
    assert 'x-ref="selectAll"' in body
    assert 'aria-label="Select all available portfolios"' in body
    assert '@click.stop="all($event.target.checked)"' in body
    assert f'value="{portfolio_ids[0]}"' in body
    blocked = body[body.index(f'value="{portfolio_ids[1]}"'):]
    assert "disabled" in blocked.split(">", 1)[0]
    assert f'action="/edms/{edm_id}/geohaz"' in body
    assert f'hx-post="/edms/{edm_id}/geohaz"' in body
    assert 'name="submission_id"' not in body
    portfolio_head = body[body.index('class="dtable__head"'):]
    portfolio_head = portfolio_head[:portfolio_head.index("</div>")]
    assert portfolio_head.index("Currency") < portfolio_head.index("Hazard Version")


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


def test_launch_post_reports_conflict_without_enqueuing(iteration2_db):
    edm_id, portfolio_ids = _edm_with_portfolios(1)
    irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_ids[0], irp_id="803")
    conflict = _client().post(
        f"/edms/{edm_id}/geohaz", data=_form(portfolio_ids),
        headers={"HX-Request": "true"},
    )
    assert conflict.status_code == 200
    toast = json.loads(conflict.headers["HX-Trigger"])["rwb:toast"]
    assert toast == {
        "message": "Hazard lookup is already in progress for: Portfolio 1.",
        "type": "error",
    }


def test_launch_post_without_selection_uses_prg_and_error_banner(iteration2_db):
    edm_id, _ = _edm_with_portfolios(1)

    response = _client().post(f"/edms/{edm_id}/geohaz", data=_form([]))

    assert response.status_code == 303
    page = _client().get(response.headers["location"])
    assert "Select at least one portfolio." in page.text


def test_launch_post_enqueues_each_portfolio_and_returns_confirmation(
    iteration2_db,
):
    edm_id, portfolio_ids = _edm_with_portfolios(2)

    response = _client().post(
        f"/edms/{edm_id}/geohaz",
        data=_form(portfolio_ids),
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
        assert params["data_version"] == "25.0"
        assert params["model_family"] == "DLM"
        assert params["perils"] == ["earthquake", "windstorm"]
        assert params["skip_prev_hazard"] is False
        assert params["override_user_def"] is True
    toast = json.loads(response.headers["HX-Trigger"])["rwb:toast"]
    assert toast == {
        "message": "Hazard lookup queued for 2 portfolios.",
        "type": "success",
    }


def test_contextual_launch_preserves_submission_content(
    iteration2_db, monkeypatch,
):
    edm_id, portfolio_ids = _edm_with_portfolios(2)
    monkeypatch.setattr(
        edm_service, "get_contextual_edm_detail",
        lambda **kwargs: _context(edm_id),
    )
    client = _client()
    page = client.get(f"/submissions/submission-a/edms/{edm_id}")

    assert f'action="/edms/{edm_id}/geohaz"' in page.text
    assert f'hx-post="/edms/{edm_id}/geohaz"' in page.text
    assert 'name="submission_id" value="submission-a"' in page.text

    data = _form(portfolio_ids)
    data["submission_id"] = "submission-a"
    response = client.post(
        f"/edms/{edm_id}/geohaz", data=data,
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert 'href="/submissions/submission-a"' in response.text
    assert "EDM in Submission A" in response.text
    assert "Broker analyses" in response.text
    assert "Submission RDM" in response.text
    toast = json.loads(response.headers["HX-Trigger"])["rwb:toast"]
    assert toast == {
        "message": "Hazard lookup queued for 2 portfolios.",
        "type": "success",
    }

    conflict = client.post(
        f"/edms/{edm_id}/geohaz", data=data,
        headers={"HX-Request": "true"},
    )
    assert 'href="/submissions/submission-a"' in conflict.text
    assert "Broker analyses" in conflict.text
    toast = json.loads(conflict.headers["HX-Trigger"])["rwb:toast"]
    assert toast["type"] == "error"
    assert "already in progress" in toast["message"]

    plain_conflict = client.post(f"/edms/{edm_id}/geohaz", data=data)
    assert plain_conflict.status_code == 303
    assert plain_conflict.headers["location"].startswith(
        f"/submissions/submission-a/edms/{edm_id}?geohaz_error=")
    error_page = client.get(plain_conflict.headers["location"])
    assert "already in progress" in error_page.text
    assert 'href="/submissions/submission-a"' in error_page.text


def test_contextual_launch_plain_post_redirects_to_contextual_page(
    iteration2_db, monkeypatch,
):
    edm_id, portfolio_ids = _edm_with_portfolios(1)
    monkeypatch.setattr(
        edm_service, "get_contextual_edm_detail",
        lambda **kwargs: _context(edm_id),
    )
    data = _form(portfolio_ids)
    data["submission_id"] = "submission-a"

    response = _client().post(f"/edms/{edm_id}/geohaz", data=data)

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/submissions/submission-a/edms/{edm_id}?geohaz=queued")
    page = _client().get(response.headers["location"])
    assert "Hazard lookup queued" in page.text
    assert 'href="/submissions/submission-a"' in page.text


def test_contextual_launch_rejects_an_unrelated_edm(
    iteration2_db, monkeypatch,
):
    edm_id, portfolio_ids = _edm_with_portfolios(1)
    monkeypatch.setattr(
        edm_service, "get_contextual_edm_detail", lambda **kwargs: None)
    data = _form(portfolio_ids)
    data["submission_id"] = "submission-a"

    response = _client().post(f"/edms/{edm_id}/geohaz", data=data)

    assert response.status_code == 404
    assert execute(
        "SELECT id FROM rwb_job WHERE rwb_job_type='run_geohaz'",
        {}, connection="WORKBENCH") == []


def test_launch_post_without_htmx_uses_prg_and_confirmation_banner(iteration2_db):
    edm_id, portfolio_ids = _edm_with_portfolios(1)

    response = _client().post(
        f"/edms/{edm_id}/geohaz", data=_form(portfolio_ids))

    assert response.status_code == 303
    assert response.headers["location"] == f"/edms/{edm_id}?geohaz=queued"
    page = _client().get(response.headers["location"])
    assert "Hazard lookup queued" in page.text


def test_detail_and_cell_render_live_state_then_stop_polling(iteration2_db):
    edm_id, [portfolio_id] = _edm_with_portfolios(1)
    job_id = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_id, irp_id="940",
        request_params={
            "data_version": "25.0", "model_family": "DLM",
            "perils": ["earthquake"], "skip_prev_hazard": False,
            "override_user_def": False,
        }, actor_id=iteration2_db.user_a)
    execute_command(
        "UPDATE irp_job SET status = 'RUNNING' WHERE id = :id",
        {"id": job_id}, connection="WORKBENCH")

    page = _client().get(f"/edms/{edm_id}").text
    cell_url = f"/edms/{edm_id}/portfolios/{portfolio_id}/geohaz-cell"
    assert "Hazard Version" in page
    assert f'hx-get="{cell_url}"' in page
    assert "Earthquake" in page

    live = _client().get(cell_url)
    assert live.status_code == 200
    assert "RUNNING" in live.text
    assert 'hx-trigger="every 3s"' in live.text
    assert f'id="geohaz-details-{portfolio_id}"' in live.text
    assert 'hx-swap-oob="outerHTML"' in live.text
    assert f'id="geohaz-pick-{portfolio_id}"' not in live.text

    execute_command(
        "UPDATE irp_job SET status = 'FINISHED', "
        "completion_summary = :summary WHERE id = :id",
        {"id": job_id, "summary": "EARTHQUAKE processed 14 Locations."},
        connection="WORKBENCH")
    execute_command(
        "UPDATE irp_portfolio SET exposure_detail = :detail WHERE id = :id",
        {"id": portfolio_id,
         "detail": '{"metrics":{"hazardVersion":"23.0,25.0"}}'},
        connection="WORKBENCH")
    terminal = _client().get(cell_url)
    assert terminal.status_code == 200
    assert "23.0,25.0" in terminal.text
    assert "hx-trigger" not in terminal.text
    assert 'hx-swap-oob="outerHTML"' in terminal.text
    assert "EARTHQUAKE processed 14 Locations." in terminal.text
    terminal_checkbox = terminal.text[
        terminal.text.index(f'id="geohaz-pick-{portfolio_id}"'):]
    assert "disabled" not in terminal_checkbox.split("</span>", 1)[0]


def test_missing_portfolio_cell_is_terminal_empty_fragment(iteration2_db):
    edm_id, _ = _edm_with_portfolios(1)

    response = _client().get(
        f"/edms/{edm_id}/portfolios/not-a-portfolio/geohaz-cell")

    assert response.status_code == 200
    assert "&mdash;" in response.text
    assert "hx-trigger" not in response.text


def test_latest_lookup_renders_requested_details_and_result(iteration2_db):
    edm_id, [portfolio_id] = _edm_with_portfolios(1)
    with_summary = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_id, irp_id="950",
        request_params={"perils": ["earthquake", "windstorm"]},
        actor_id=iteration2_db.user_a)
    without_summary = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_id, irp_id="951",
        request_params={
            "data_version": "25.0", "model_family": "DLM",
            "perils": ["earthquake", "windstorm"],
            "skip_prev_hazard": True,
            "override_user_def": False,
        },
        actor_id=iteration2_db.user_a)
    execute_command(
        "UPDATE irp_job SET status = 'FINISHED', "
        "completion_summary = :summary WHERE id = :id",
        {"id": with_summary, "summary": (
            "EARTHQUAKE processed 14 Locations. WINDSTORM processed 0 Locations.")},
        connection="WORKBENCH")
    execute_command(
        "UPDATE irp_job SET status = 'FAILED' WHERE id = :id",
        {"id": without_summary}, connection="WORKBENCH")

    body = _client().get(f"/edms/{edm_id}").text
    details = body[body.index(f'id="geohaz-details-{portfolio_id}"'):]
    details = details[:details.index("</section>")]

    assert "Most recent hazard lookup" in details
    assert "<dd>25.0</dd>" in details
    assert "<dd>DLM</dd>" in details
    assert "Earthquake, Windstorm" in details
    assert "<dt>Skip locations with previous hazard lookup</dt><dd>Yes</dd>" in details
    assert "<dt>Overwrite user-defined hazard values</dt><dd>No</dd>" in details
    assert "EARTHQUAKE processed 14 Locations." not in details
    assert '<dd class="geohaz-details__failed">Failed</dd>' in details
