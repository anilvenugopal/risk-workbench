from __future__ import annotations

import json
import uuid

import pytest

from app.services import geohaz_service, irp_job_service, rwb_job_service
from app.services.errors import GeohazLaunchConflict, InvalidGeohazLaunch
from app.workers import dispatch
from db import execute, execute_command, execute_scalar


def _edm_with_portfolios(count: int = 2) -> tuple[str, list[str]]:
    edm_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
        "VALUES (:id, 'GeoHaz EDM', 'ready', '2026-08-13', '2026-08-13')",
        {"id": edm_id}, connection="WORKBENCH")
    portfolio_ids: list[str] = []
    for number in range(1, count + 1):
        portfolio_id = str(uuid.uuid4())
        portfolio_ids.append(portfolio_id)
        execute_command(
            "INSERT INTO irp_portfolio "
            "(id, edm_id, name, irp_id, inserted_at, updated_at) "
            "VALUES (:id, :edm, :name, :irp, '2026-08-13', '2026-08-13')",
            {
                "id": portfolio_id,
                "edm": edm_id,
                "name": f"Portfolio {number}",
                "irp": str(100 + number),
            },
            connection="WORKBENCH",
        )
    return edm_id, portfolio_ids


def _job_count() -> int:
    return int(execute_scalar(
        "SELECT COUNT(*) FROM rwb_job WHERE rwb_job_type = 'run_geohaz'",
        {}, connection="WORKBENCH") or 0)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("no_selection", InvalidGeohazLaunch),
        ("wrong_edm", InvalidGeohazLaunch),
    ],
)
def test_launch_validation_rejects_the_whole_selection(
    iteration2_db, change, error,
):
    edm_id, portfolio_ids = _edm_with_portfolios()
    selected = list(portfolio_ids)

    if change == "no_selection":
        selected = []
    elif change == "wrong_edm":
        _, foreign_ids = _edm_with_portfolios(1)
        selected.append(foreign_ids[0])

    with pytest.raises(error):
        geohaz_service.launch(
            edm_id=edm_id,
            portfolio_ids=selected,
            actor_id=iteration2_db.user_a,
        )

    assert _job_count() == 0


def test_gate_rejects_missing_edm_and_edm_without_portfolios(iteration2_db):
    with pytest.raises(InvalidGeohazLaunch, match="no longer exists"):
        geohaz_service.launch(
            edm_id=str(uuid.uuid4()), portfolio_ids=[],
            actor_id=iteration2_db.user_a)

    edm_id, _ = _edm_with_portfolios(0)
    with pytest.raises(InvalidGeohazLaunch, match="at least one portfolio"):
        geohaz_service.launch(
            edm_id=edm_id, portfolio_ids=[], actor_id=iteration2_db.user_a)
    assert _job_count() == 0


@pytest.mark.parametrize("blocker", ["irp_job", "rwb_job"])
def test_ineligible_portfolio_rejects_all_jobs(iteration2_db, blocker):
    edm_id, portfolio_ids = _edm_with_portfolios()
    blocked = portfolio_ids[1]
    if blocker == "irp_job":
        irp_job_service.record_submitted_irp_job(
            irp_job_type="geohaz", irp_edm_id=edm_id,
            irp_portfolio_id=blocked, irp_id="901")
    else:
        rwb_job_service.ensure_pending_rwb_job(
            requestor_type="analyst_request", requestor_id=blocked,
            rwb_job_type="run_geohaz", input_data={})

    before = _job_count()
    with pytest.raises(GeohazLaunchConflict, match="Portfolio 2"):
        geohaz_service.launch(
            edm_id=edm_id, portfolio_ids=portfolio_ids,
            actor_id=iteration2_db.user_a)
    assert _job_count() == before


def test_valid_launch_enqueues_one_job_per_portfolio_with_shared_params(
    iteration2_db, monkeypatch,
):
    edm_id, portfolio_ids = _edm_with_portfolios()
    monkeypatch.setattr(geohaz_service.settings, "hazard_data_version", "25.0")
    sent: list[tuple[str, str]] = []
    dispatch.configure(lambda *, rwb_job_id, rwb_job_type: sent.append(
        (rwb_job_id, rwb_job_type)))
    try:
        result = geohaz_service.launch(
            edm_id=edm_id,
            portfolio_ids=portfolio_ids,
            actor_id=iteration2_db.user_a,
        )
    finally:
        dispatch.reset()

    rows = execute(
        "SELECT id, requestor_id, status_code, input_data, inserted_by "
        "FROM rwb_job WHERE rwb_job_type = 'run_geohaz' ORDER BY requestor_id",
        {}, connection="WORKBENCH")
    assert len(rows) == 2
    assert set(result.rwb_job_ids) == {str(row["id"]) for row in rows}
    assert set(result.portfolio_ids) == set(portfolio_ids)
    expected_params = {
        "data_version": "25.0",
        "model_family": "DLM",
        "perils": ["earthquake", "windstorm"],
        "skip_prev_hazard": False,
        "override_user_def": True,
    }
    assert result.request_params == expected_params
    for row in rows:
        input_data = json.loads(row["input_data"])
        assert input_data["params"] == expected_params
        assert input_data["irp_edm_id"] == edm_id
        assert input_data["irp_portfolio_id"] == str(row["requestor_id"])
        assert input_data["requested_by_user_id"] == iteration2_db.user_a
        assert str(row["inserted_by"]) == iteration2_db.user_a
        assert row["status_code"] == "pending"
    assert set(sent) == {(job_id, "run_geohaz") for job_id in result.rwb_job_ids}


def test_launch_normalizes_sql_server_uuid_casing(iteration2_db, monkeypatch):
    edm_id, portfolio_ids = _edm_with_portfolios(1)
    original_execute = geohaz_service.execute

    def execute_with_uppercase_ids(*args, **kwargs):
        rows = original_execute(*args, **kwargs)
        for row in rows:
            row["id"] = str(row["id"]).upper()
        return rows

    monkeypatch.setattr(geohaz_service, "execute", execute_with_uppercase_ids)

    result = geohaz_service.launch(
        edm_id=edm_id,
        portfolio_ids=portfolio_ids,
        actor_id=iteration2_db.user_a,
    )

    assert result.portfolio_ids == portfolio_ids


def test_lookup_states_show_live_status_then_stored_hazard_version(
    iteration2_db,
):
    edm_id, portfolio_ids = _edm_with_portfolios(6)
    submitting, submitted, live, succeeded, failed, never = portfolio_ids
    rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=submitting,
        rwb_job_type="run_geohaz", input_data={})
    irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=submitted, irp_id="909", status="SUBMITTED")
    live_job = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=live, irp_id="910")
    execute_command(
        "UPDATE irp_job SET status = 'RUNNING' WHERE id = :id",
        {"id": live_job}, connection="WORKBENCH")
    finished_job = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=succeeded, irp_id="911")
    execute_command(
        "UPDATE irp_job SET status = 'FINISHED' WHERE id = :id",
        {"id": finished_job}, connection="WORKBENCH")
    failed_job = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=failed, irp_id="912")
    execute_command(
        "UPDATE irp_job SET status = 'FAILED' WHERE id = :id",
        {"id": failed_job}, connection="WORKBENCH")
    for portfolio_id, version in (
        (succeeded, "23.0,25.0"), (failed, "23.0"), (never, ""),
    ):
        execute_command(
            "UPDATE irp_portfolio SET exposure_detail = :detail WHERE id = :id",
            {"id": portfolio_id, "detail": json.dumps({
                "metrics": {"hazardVersion": version}, "summary": None})},
            connection="WORKBENCH")

    states = geohaz_service.lookup_states(edm_id)

    assert states[submitting].label == "SUBMITTING"
    assert states[submitting].live is True
    assert states[submitted].label == "SUBMITTED"
    assert states[submitted].live is True
    assert states[live].label == "RUNNING" and states[live].live is True
    assert states[succeeded].label == "23.0,25.0"
    assert states[succeeded].live is False
    assert states[failed].label == "23.0" and states[failed].live is False
    assert states[never].label == "" and states[never].live is False


def test_lookup_state_uses_stored_version_after_a_later_failure(iteration2_db):
    edm_id, [portfolio_id] = _edm_with_portfolios(1)
    for irp_id, status in (("920", "FINISHED"), ("921", "FAILED")):
        job_id = irp_job_service.record_submitted_irp_job(
            irp_job_type="geohaz", irp_edm_id=edm_id,
            irp_portfolio_id=portfolio_id, irp_id=irp_id)
        execute_command(
            "UPDATE irp_job SET status = :status WHERE id = :id",
            {"status": status, "id": job_id}, connection="WORKBENCH")
    execute_command(
        "UPDATE irp_portfolio SET exposure_detail = :detail WHERE id = :id",
        {"id": portfolio_id,
         "detail": json.dumps({"metrics": {"hazardVersion": "25.0"}})},
        connection="WORKBENCH")

    state = geohaz_service.cell_state(portfolio_id)

    assert state is not None
    assert state.label == "25.0"
    assert state.live is False


def test_latest_lookup_returns_only_newest_run(iteration2_db):
    edm_id, [portfolio_id] = _edm_with_portfolios(1)
    first = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_id, irp_id="930",
        request_params={
            "data_version": "24.0", "model_family": "DLM",
            "perils": ["earthquake"], "skip_prev_hazard": True,
            "override_user_def": False,
        }, actor_id=iteration2_db.user_a)
    second = irp_job_service.record_submission_failure(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=portfolio_id,
        request_params={
            "data_version": "25.0", "model_family": "DLM",
            "perils": ["windstorm"], "skip_prev_hazard": False,
            "override_user_def": True,
        }, actor_id=iteration2_db.user_b)
    execute_command(
        "UPDATE irp_job SET inserted_at = '2026-08-12', "
        "submitted_at = '2026-08-12' WHERE id = :id",
        {"id": first}, connection="WORKBENCH")
    execute_command(
        "UPDATE irp_job SET inserted_at = '2026-08-13', "
        "submitted_at = '2026-08-13', completed_at = '2026-08-13' "
        "WHERE id = :id",
        {"id": second}, connection="WORKBENCH")

    latest = geohaz_service.latest_lookup(portfolio_id)

    assert latest is not None
    assert latest.id == second
    assert latest.request_params["perils"] == ["windstorm"]
    assert latest.status == "SUBMISSION FAILED"
    assert latest.failed is True


def test_latest_lookups_returns_newest_run_per_portfolio(iteration2_db):
    edm_id, [single_run, two_runs] = _edm_with_portfolios(2)
    other_edm_id, [foreign] = _edm_with_portfolios(1)
    irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=single_run, irp_id="960")
    older = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=two_runs, irp_id="961")
    newer = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=edm_id,
        irp_portfolio_id=two_runs, irp_id="962")
    irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz", irp_edm_id=other_edm_id,
        irp_portfolio_id=foreign, irp_id="963")
    execute_command(
        "UPDATE irp_job SET inserted_at = '2026-08-12' WHERE id = :id",
        {"id": older}, connection="WORKBENCH")
    execute_command(
        "UPDATE irp_job SET inserted_at = '2026-08-13' WHERE id = :id",
        {"id": newer}, connection="WORKBENCH")

    latest = geohaz_service.latest_lookups(edm_id)

    assert set(latest) == {single_run, two_runs}
    assert latest[two_runs].id == newer
