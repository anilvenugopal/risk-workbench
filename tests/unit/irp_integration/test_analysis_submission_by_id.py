"""
Tests for the id path of ``submit_portfolio_analysis_job``.

A caller that already holds the Risk Modeler ids of the model profile, output
profile and event rate scheme passes ``model_profile_id``, ``output_profile_id``,
``event_rate_scheme_id`` and ``analysis_type`` instead of the three names. The
method then posts the ids as given and makes no reference-data request. The
name path is the only source of ``softwareVersionCode``, so on the id path the
job ``type`` has to be stated as ``analysis_type``. A ``'DLM'`` job needs
``event_rate_scheme_id``, as the name path needs ``event_rate_scheme_name``.

Names and ids are all-or-nothing: mixing them raises before any request is
made. The name-path rules are covered in ``test_analysis_submission.py`` and
``test_analysis_tags.py``.
"""

from unittest.mock import Mock

import pytest

from irp_integration.constants import CREATE_ANALYSIS_JOB, SEARCH_ANALYSIS_RESULTS
from irp_integration.exceptions import IRPAPIError, IRPAuthenticationError, IRPValidationError


# Invented names. Nothing here may name a real EDM, portfolio or tenant: this
# file ships in the sdist, so a name used here is a name published to PyPI.
EDM_NAME = "example_edm"
PORTFOLIO_NAME = "example_portfolio"
JOB_NAME = "example_analysis"
PORTFOLIO_URI = "/platform/riskdata/v1/exposures/42/portfolios/7"
ANALYSIS_PROFILE_NAME = "example_model_profile"
CURRENCY = {"code": "USD", "scheme": "RMS", "vintage": "RL25", "asOfDate": "2026-01-01"}

MODEL_PROFILE_ID = 11
OUTPUT_PROFILE_ID = 22
EVENT_RATE_SCHEME_ID = 33


def make_manager(make_analysis_manager, response):
    """
    Build a manager wired for one id-path submit_portfolio_analysis_job call.

    The queue holds the duplicate check, the portfolio search and the POST.
    No model profile, output profile or event rate scheme response is queued:
    a lookup on the id path fails the test on FakeClient's exhausted queue.
    """
    responses = [
        response(200, json_body=[]),
        response(200, json_body=[{"uri": PORTFOLIO_URI, "portfolioId": 7}]),
        response(201, headers={"location": "/platform/model/v1/jobs/901"}),
    ]
    return make_analysis_manager(responses=responses, edms=[{"exposureId": 42}])


def test_id_path_posts_ids_without_reference_lookups(make_analysis_manager, response):
    manager, client, _ = make_manager(make_analysis_manager, response)

    job_id, request_body = manager.submit_portfolio_analysis_job(
        edm_name=EDM_NAME,
        portfolio_name=PORTFOLIO_NAME,
        job_name=JOB_NAME,
        treaty_names=[],
        tag_names=[],
        currency=CURRENCY,
        model_profile_id=MODEL_PROFILE_ID,
        output_profile_id=OUTPUT_PROFILE_ID,
        event_rate_scheme_id=EVENT_RATE_SCHEME_ID,
        analysis_type="DLM",
    )

    expected_body = {
        "resourceUri": PORTFOLIO_URI,
        "resourceType": "portfolio",
        "type": "DLM",
        "settings": {
            "name": JOB_NAME,
            "modelProfileId": MODEL_PROFILE_ID,
            "outputProfileId": OUTPUT_PROFILE_ID,
            "treatyIds": [],
            "tagIds": [],
            "currency": CURRENCY,
            "franchiseDeductible": False,
            "minLossThreshold": 1.0,
            "treatConstructionOccupancyAsUnknown": True,
            "numMaxLossEvent": 1,
            "eventRateSchemeId": EVENT_RATE_SCHEME_ID,
        },
    }
    assert job_id == 901, "the ID comes from the location header"
    assert request_body == expected_body
    assert client.calls[-1] == {
        "method": "POST",
        "path": CREATE_ANALYSIS_JOB,
        "params": None,
        "json": expected_body,
    }
    assert [call['path'] for call in client.calls] == [
        SEARCH_ANALYSIS_RESULTS,
        '/platform/riskdata/v1/exposures/42/portfolios',
        CREATE_ANALYSIS_JOB,
    ], "the id path must not request model profiles, output profiles or schemes"


def test_a_rejected_post_keeps_its_http_status(make_analysis_manager, response):
    rejected = IRPAPIError("HTTP request failed (status 400)", status_code=400)
    manager, _, _ = make_analysis_manager(
        responses=[
            response(200, json_body=[]),
            response(200, json_body=[{"uri": PORTFOLIO_URI, "portfolioId": 7}]),
            rejected,
        ],
        edms=[{"exposureId": 42}],
    )

    with pytest.raises(IRPAPIError) as excinfo:
        manager.submit_portfolio_analysis_job(
            edm_name=EDM_NAME,
            portfolio_name=PORTFOLIO_NAME,
            job_name=JOB_NAME,
            currency=CURRENCY,
            model_profile_id=MODEL_PROFILE_ID,
            output_profile_id=OUTPUT_PROFILE_ID,
            event_rate_scheme_id=EVENT_RATE_SCHEME_ID,
            analysis_type="DLM",
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.__cause__ is rejected


def test_a_post_rejected_by_bearer_auth_keeps_the_401(make_analysis_manager, response):
    manager, _, _ = make_analysis_manager(
        responses=[
            response(200, json_body=[]),
            response(200, json_body=[{"uri": PORTFOLIO_URI, "portfolioId": 7}]),
            IRPAuthenticationError("Bearer authentication failed after re-login", status_code=401),
        ],
        edms=[{"exposureId": 42}],
    )

    with pytest.raises(IRPAPIError) as excinfo:
        manager.submit_portfolio_analysis_job(
            edm_name=EDM_NAME,
            portfolio_name=PORTFOLIO_NAME,
            job_name=JOB_NAME,
            currency=CURRENCY,
            model_profile_id=MODEL_PROFILE_ID,
            output_profile_id=OUTPUT_PROFILE_ID,
            event_rate_scheme_id=EVENT_RATE_SCHEME_ID,
            analysis_type="DLM",
        )

    assert excinfo.value.status_code == 401


def test_model_profile_id_without_analysis_type_raises(make_analysis_manager, response):
    manager, client, _ = make_manager(make_analysis_manager, response)

    with pytest.raises(IRPValidationError, match="analysis_type"):
        manager.submit_portfolio_analysis_job(
            edm_name=EDM_NAME,
            portfolio_name=PORTFOLIO_NAME,
            job_name=JOB_NAME,
            model_profile_id=MODEL_PROFILE_ID,
            output_profile_id=OUTPUT_PROFILE_ID,
        )

    assert client.calls == [], "validation must fail before any request is made"


def test_dlm_without_event_rate_scheme_id_raises(make_analysis_manager, response):
    manager, client, _ = make_manager(make_analysis_manager, response)

    with pytest.raises(IRPValidationError, match="event_rate_scheme_id.*DLM"):
        manager.submit_portfolio_analysis_job(
            edm_name=EDM_NAME,
            portfolio_name=PORTFOLIO_NAME,
            job_name=JOB_NAME,
            model_profile_id=MODEL_PROFILE_ID,
            output_profile_id=OUTPUT_PROFILE_ID,
            analysis_type="DLM",
        )

    assert client.calls == [], "validation must fail before any request is made"


def test_hd_without_event_rate_scheme_id_posts_no_scheme(make_analysis_manager, response):
    manager, client, _ = make_manager(make_analysis_manager, response)

    _, request_body = manager.submit_portfolio_analysis_job(
        edm_name=EDM_NAME,
        portfolio_name=PORTFOLIO_NAME,
        job_name=JOB_NAME,
        currency=CURRENCY,
        model_profile_id=MODEL_PROFILE_ID,
        output_profile_id=OUTPUT_PROFILE_ID,
        analysis_type="HD",
    )

    assert request_body["type"] == "HD"
    assert "eventRateSchemeId" not in request_body["settings"]
    assert client.calls[-1]["path"] == CREATE_ANALYSIS_JOB


def test_mixing_names_and_ids_raises(make_analysis_manager, response):
    manager, client, _ = make_manager(make_analysis_manager, response)

    with pytest.raises(IRPValidationError, match="analysis_profile_name.*model_profile_id"):
        manager.submit_portfolio_analysis_job(
            edm_name=EDM_NAME,
            portfolio_name=PORTFOLIO_NAME,
            job_name=JOB_NAME,
            analysis_profile_name=ANALYSIS_PROFILE_NAME,
            model_profile_id=MODEL_PROFILE_ID,
            output_profile_id=OUTPUT_PROFILE_ID,
            analysis_type="DLM",
        )

    assert client.calls == [], "validation must fail before any request is made"


def test_batch_passes_ids_through(make_analysis_manager, response):
    manager, _, _ = make_analysis_manager(
        responses=[response(200, json_body=[])]
    )
    manager.submit_portfolio_analysis_job = Mock(side_effect=[(101, {})])

    job_ids = manager.submit_portfolio_analysis_jobs([{
        "edm_name": EDM_NAME,
        "portfolio_name": PORTFOLIO_NAME,
        "job_name": JOB_NAME,
        "model_profile_id": MODEL_PROFILE_ID,
        "output_profile_id": OUTPUT_PROFILE_ID,
        "event_rate_scheme_id": EVENT_RATE_SCHEME_ID,
        "analysis_type": "HD",
    }])

    assert job_ids == [101]
    assert manager.submit_portfolio_analysis_job.call_args_list[0].kwargs == {
        "edm_name": EDM_NAME,
        "portfolio_name": PORTFOLIO_NAME,
        "job_name": JOB_NAME,
        "analysis_profile_name": None,
        "output_profile_name": None,
        "event_rate_scheme_name": None,
        "treaty_names": [],
        "tag_names": [],
        "skip_duplicate_check": True,
        "model_profile_id": MODEL_PROFILE_ID,
        "output_profile_id": OUTPUT_PROFILE_ID,
        "event_rate_scheme_id": EVENT_RATE_SCHEME_ID,
        "analysis_type": "HD",
    }
