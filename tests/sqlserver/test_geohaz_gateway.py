from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from app.services import irp_gateway, irp_job_service
from db import execute
from tests.fakes.fake_irp import FakeIRP
from tests.sqlserver.test_geohaz_service import _edm_with_portfolios


class _PortfolioManager:
    def __init__(self) -> None:
        self.submit_args = None

    def submit_geohaz_job(self, portfolio_name, edm_name, layers):
        self.submit_args = (portfolio_name, edm_name, layers)
        return 90210, {"resourceUri": "/platform/portfolios/44", "layers": layers}

    def get_geohaz_job(self, job_id):
        return {"jobId": job_id, "status": "RUNNING", "progress": 50}


def test_geohaz_parameter_mapping_is_hazard_only():
    portfolio = _PortfolioManager()
    gateway = irp_gateway._RealGateway()
    gateway._irp = SimpleNamespace(portfolio=portfolio)

    result = gateway.submit_geohaz(
        edm_name="EDM A",
        portfolio_name="Portfolio A",
        version="25.0",
        perils=["earthquake", "windstorm"],
        skip_prev_hazard=True,
        override_user_def=True,
    )

    assert portfolio.submit_args == (
        "Portfolio A",
        "EDM A",
        [
            {
                "type": "hazard",
                "name": "earthquake",
                "engineType": "RL",
                "version": "25.0",
                "layerOptions": {
                    "overrideUserDef": True,
                    "skipPrevHazard": True,
                },
            },
            {
                "type": "hazard",
                "name": "windstorm",
                "engineType": "RL",
                "version": "25.0",
                "layerOptions": {
                    "overrideUserDef": True,
                    "skipPrevHazard": True,
                },
            },
        ],
    )
    assert all(layer["type"] != "geocode" for layer in result.payload["layers"])
    assert result.irp_id == "90210"
    assert result.resource_uri == "/platform/portfolios/44"


def test_get_geohaz_job_is_one_status_check():
    portfolio = _PortfolioManager()
    gateway = irp_gateway._RealGateway()
    gateway._irp = SimpleNamespace(portfolio=portfolio)

    result = gateway.get_geohaz_job("90210")

    assert result.status == "RUNNING"
    assert result.result == {"jobId": 90210, "status": "RUNNING", "progress": 50}


def test_fake_irp_implements_geohaz_gateway_methods():
    fake = FakeIRP()
    assert isinstance(fake, irp_gateway.IRPGateway)

    submitted = fake.submit_geohaz(
        edm_name="EDM A",
        portfolio_name="Portfolio A",
        version="25.0",
        perils=["earthquake"],
        skip_prev_hazard=False,
        override_user_def=True,
    )
    fake.finish(submitted.irp_id, {"locations": 12})

    assert fake.get_geohaz_job(submitted.irp_id).status == "FINISHED"


def test_irp_job_writers_persist_geohaz_portfolio_and_params(workbench_db):
    _, portfolio_ids = _edm_with_portfolios(1)
    portfolio_id = portfolio_ids[0]
    params = {
        "data_version": "25.0",
        "model_family": "DLM",
        "perils": ["earthquake", "windstorm"],
        "skip_prev_hazard": False,
        "override_user_def": True,
    }

    submitted_id = irp_job_service.record_submitted_irp_job(
        irp_job_type="geohaz",
        irp_portfolio_id=portfolio_id,
        irp_id="700",
        resource_uri="/platform/portfolios/44",
        payload={"resourceUri": "/platform/portfolios/44"},
        request_params=params,
        actor_id=workbench_db.user_a,
    )
    failed_id = irp_job_service.record_submission_failure(
        irp_job_type="geohaz",
        irp_portfolio_id=portfolio_id,
        payload={"error": "submit failed"},
        request_params=params,
        actor_id=workbench_db.user_a,
    )

    rows = execute(
        "SELECT id, irp_portfolio_id, request_params, status FROM irp_job "
        "WHERE id IN (:submitted, :failed)",
        {"submitted": submitted_id, "failed": failed_id},
        connection="WORKBENCH",
    )
    by_id = {row["id"].lower(): row for row in rows}
    assert by_id[submitted_id]["irp_portfolio_id"].lower() == portfolio_id
    assert by_id[failed_id]["irp_portfolio_id"].lower() == portfolio_id
    assert json.loads(by_id[submitted_id]["request_params"]) == params
    assert json.loads(by_id[failed_id]["request_params"]) == params
    assert by_id[submitted_id]["status"] == "QUEUED"
    assert by_id[failed_id]["status"] == "SUBMISSION FAILED"

    resources = execute(
        "SELECT resource_uri FROM irp_job_resource WHERE irp_job_id = :id",
        {"id": submitted_id},
        connection="WORKBENCH",
    )
    assert resources == [{"resource_uri": "/platform/portfolios/44"}]
