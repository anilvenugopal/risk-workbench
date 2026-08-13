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
        ("no_perils", InvalidGeohazLaunch),
        ("bad_peril", InvalidGeohazLaunch),
        ("bad_version", InvalidGeohazLaunch),
        ("bad_missing", InvalidGeohazLaunch),
        ("wrong_edm", InvalidGeohazLaunch),
    ],
)
def test_launch_validation_rejects_the_whole_selection(
    iteration2_db, monkeypatch, change, error,
):
    edm_id, portfolio_ids = _edm_with_portfolios()
    selected = list(portfolio_ids)
    perils = ["earthquake", "windstorm"]
    version = "25.0"
    missing = "overwrite"
    monkeypatch.setattr(geohaz_service.settings, "geohaz_data_versions", ["25.0"])

    if change == "no_selection":
        selected = []
    elif change == "no_perils":
        perils = []
    elif change == "bad_peril":
        perils = ["flood"]
    elif change == "bad_version":
        version = "23.0"
    elif change == "bad_missing":
        missing = "keep"
    elif change == "wrong_edm":
        _, foreign_ids = _edm_with_portfolios(1)
        selected.append(foreign_ids[0])

    with pytest.raises(error):
        geohaz_service.launch(
            edm_id=edm_id,
            portfolio_ids=selected,
            data_version=version,
            perils=perils,
            missing_locations=missing,
            actor_id=iteration2_db.user_a,
        )

    assert _job_count() == 0


def test_gate_rejects_missing_edm_and_edm_without_portfolios(iteration2_db):
    with pytest.raises(InvalidGeohazLaunch, match="no longer exists"):
        geohaz_service.launch(
            edm_id=str(uuid.uuid4()), portfolio_ids=[], data_version="25.0",
            perils=["earthquake"], missing_locations="overwrite",
            actor_id=iteration2_db.user_a)

    edm_id, _ = _edm_with_portfolios(0)
    with pytest.raises(InvalidGeohazLaunch, match="at least one portfolio"):
        geohaz_service.launch(
            edm_id=edm_id, portfolio_ids=[], data_version="25.0",
            perils=["earthquake"], missing_locations="overwrite",
            actor_id=iteration2_db.user_a)
    assert _job_count() == 0


@pytest.mark.parametrize("blocker", ["irp_job", "rwb_job"])
def test_ineligible_portfolio_rejects_all_jobs(iteration2_db, blocker):
    edm_id, portfolio_ids = _edm_with_portfolios()
    blocked = portfolio_ids[1]
    if blocker == "irp_job":
        irp_job_service.record_submitted_irp_job(
            package_id=None, irp_job_type="geohaz", irp_edm_id=edm_id,
            irp_portfolio_id=blocked, irp_id="901")
    else:
        rwb_job_service.ensure_pending_rwb_job(
            requestor_type="analyst_request", requestor_id=blocked,
            rwb_job_type="run_geohaz", input_data={})

    before = _job_count()
    with pytest.raises(GeohazLaunchConflict, match="Portfolio 2"):
        geohaz_service.launch(
            edm_id=edm_id, portfolio_ids=portfolio_ids, data_version="25.0",
            perils=["earthquake"], missing_locations="overwrite",
            actor_id=iteration2_db.user_a)
    assert _job_count() == before
    assert geohaz_service.eligible(blocked) is False
    assert geohaz_service.eligible(portfolio_ids[0]) is True


def test_valid_launch_enqueues_one_job_per_portfolio_with_shared_params(
    iteration2_db, monkeypatch,
):
    edm_id, portfolio_ids = _edm_with_portfolios()
    monkeypatch.setattr(geohaz_service.settings, "geohaz_data_versions", ["25.0"])
    sent: list[tuple[str, str]] = []
    dispatch.configure(lambda *, rwb_job_id, rwb_job_type: sent.append(
        (rwb_job_id, rwb_job_type)))
    try:
        result = geohaz_service.launch(
            edm_id=edm_id,
            portfolio_ids=portfolio_ids,
            data_version="25.0",
            perils=["windstorm", "earthquake"],
            missing_locations="skip",
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
        "perils": ["windstorm", "earthquake"],
        "missing_locations": "skip",
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
        data_version="25.0",
        perils=["earthquake"],
        missing_locations="overwrite",
        actor_id=iteration2_db.user_a,
    )

    assert result.portfolio_ids == portfolio_ids
