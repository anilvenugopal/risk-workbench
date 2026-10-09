"""
Tests for configurable GeoHaz layer submission.

The GeoHaz endpoint accepts geocode and hazard layers in any combination. The
package validates the documented fields and options, then sends the selected
layers without inserting a geocode request or changing their order.
"""

from unittest.mock import Mock

import pytest

from irp_integration.exceptions import IRPValidationError


EDM_NAME = "example_edm"
PORTFOLIO_NAME = "example_portfolio"
PORTFOLIO_URI = "/platform/riskdata/v1/exposures/42/portfolios/7"


def hazard_layer(name, *, engine_type="RL", version="25", **extra):
    """Return a valid hazard layer with risk-workbench option values."""
    layer = {
        "type": "hazard",
        "name": name,
        "engineType": engine_type,
        "version": version,
        "layerOptions": {
            "overrideUserDef": False,
            "skipPrevHazard": True,
        },
    }
    layer.update(extra)
    return layer


def geocode_layer():
    """Return a valid geocode layer."""
    return {
        "type": "geocode",
        "name": "geocode",
        "engineType": "RL",
        "version": "25",
        "layerOptions": {
            "geoLicenseType": "0",
            "aggregateTriggerEnabled": True,
            "skipPrevGeocoded": False,
        },
    }


def make_submission_manager(make_portfolio_manager, response):
    """Build a manager with responses for portfolio lookup, accounts, and POST."""
    return make_portfolio_manager(
        responses=[
            response(200, json_body=[{
                "uri": PORTFOLIO_URI,
                "portfolioId": 7,
            }]),
            response(200, json_body=[{"accountId": 8, "locationsCount": 3}]),
            response(201, headers={
                "location": "/platform/geohaz/v1/jobs/901"
            }),
        ],
        edms=[{"exposureId": 42}],
    )


def test_submits_risk_workbench_hazards_without_geocode(
    make_portfolio_manager, response
):
    manager, client, _ = make_submission_manager(make_portfolio_manager, response)
    layers = [hazard_layer("earthquake"), hazard_layer("windstorm")]

    job_id, request_body = manager.submit_geohaz_job(
        portfolio_name=PORTFOLIO_NAME,
        edm_name=EDM_NAME,
        layers=layers,
    )

    expected_body = {
        "resourceUri": PORTFOLIO_URI,
        "resourceType": "portfolio",
        "settings": {"layers": layers},
    }
    assert job_id == 901
    assert request_body == expected_body
    assert client.calls[-1] == {
        "method": "POST",
        "path": "/platform/geohaz/v1/jobs",
        "params": None,
        "json": expected_body,
    }
    assert [layer["name"] for layer in request_body["settings"]["layers"]] == [
        "earthquake",
        "windstorm",
    ]


def test_preserves_mixed_layers_order_and_additional_fields(
    make_portfolio_manager, response
):
    manager, _, _ = make_submission_manager(make_portfolio_manager, response)
    distance_layer = hazard_layer(
        "distance_to_fault",
        engine_type="NEXT_ENGINE",
        version="future",
        apiAddition={"enabled": True},
    )
    distance_layer["layerOptions"]["futureOption"] = "kept"
    layers = [distance_layer, geocode_layer()]

    _, request_body = manager.submit_geohaz_job(
        PORTFOLIO_NAME,
        EDM_NAME,
        layers,
    )

    assert request_body["settings"]["layers"] == layers


@pytest.mark.parametrize(
    ("layers", "message"),
    [
        ([], "layers cannot be empty"),
        (["hazard"], "layers\\[0\\] must be a dictionary"),
        ([{"type": "hazard"}], "missing required field 'name'"),
        ([{
            "type": "data",
            "name": "future",
            "engineType": "RL",
            "version": "25",
            "layerOptions": {},
        }], "type must be 'geocode' or 'hazard'"),
        ([{
            "type": "hazard",
            "name": "earthquake",
            "engineType": "RL",
            "version": "25",
            "layerOptions": {"overrideUserDef": False},
        }], "skipPrevHazard is required"),
        ([{
            "type": "geocode",
            "name": "geocode",
            "engineType": "RL",
            "version": "25",
            "layerOptions": {
                "geoLicenseType": "0",
                "aggregateTriggerEnabled": "true",
                "skipPrevGeocoded": False,
            },
        }], "aggregateTriggerEnabled must be a bool"),
    ],
)
def test_invalid_layers_raise_before_edm_lookup(
    make_portfolio_manager, layers, message
):
    manager, client, edm_manager = make_portfolio_manager([], edms=[])

    with pytest.raises(IRPValidationError, match=message):
        manager.submit_geohaz_job(PORTFOLIO_NAME, EDM_NAME, layers)

    assert edm_manager.filters == []
    assert client.calls == []


def test_submit_geohaz_jobs_forwards_layers_and_returns_job_ids(
    make_portfolio_manager
):
    manager, _, _ = make_portfolio_manager([])
    manager.submit_geohaz_job = Mock(side_effect=[(101, {}), (102, {})])
    first_layers = [hazard_layer("earthquake")]
    second_layers = [geocode_layer(), hazard_layer("windstorm")]

    job_ids = manager.submit_geohaz_jobs([
        {
            "edm_name": "edm_one",
            "portfolio_name": "portfolio_one",
            "layers": first_layers,
        },
        {
            "edm_name": "edm_two",
            "portfolio_name": "portfolio_two",
            "layers": second_layers,
        },
    ])

    assert job_ids == [101, 102]
    assert manager.submit_geohaz_job.call_args_list[0].kwargs == {
        "portfolio_name": "portfolio_one",
        "edm_name": "edm_one",
        "layers": first_layers,
    }
    assert manager.submit_geohaz_job.call_args_list[1].kwargs == {
        "portfolio_name": "portfolio_two",
        "edm_name": "edm_two",
        "layers": second_layers,
    }


def test_submit_geohaz_jobs_rejects_missing_layers(make_portfolio_manager):
    manager, _, _ = make_portfolio_manager([])

    with pytest.raises(IRPValidationError, match="missing required field 'layers'"):
        manager.submit_geohaz_jobs([{
            "edm_name": EDM_NAME,
            "portfolio_name": PORTFOLIO_NAME,
        }])
