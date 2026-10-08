"""
Tests for the tag rules in ``submit_portfolio_analysis_job``.

An analysis may have no tags to apply. ``treatyIds`` and ``tagIds`` go into the
same ``settings`` dict, so an empty ``tagIds`` is as valid as the empty
``treatyIds`` the package already sends: the tag lookup is skipped and the job
is submitted with ``tagIds: []``.

The event rate scheme rules are covered in ``test_analysis_submission.py``.
"""

from unittest.mock import Mock

from irp_integration.constants import (
    CREATE_ANALYSIS_JOB, GET_MODEL_PROFILES, GET_OUTPUT_PROFILES, GET_TAGS,
    SEARCH_ANALYSIS_RESULTS
)


# Invented names. Nothing here may name a real EDM, portfolio or tenant: this
# file ships in the sdist, so a name used here is a name published to PyPI.
EDM_NAME = "example_edm"
PORTFOLIO_NAME = "example_portfolio"
JOB_NAME = "example_analysis"
PORTFOLIO_URI = "/platform/riskdata/v1/exposures/42/portfolios/7"
ANALYSIS_PROFILE_NAME = "example_model_profile"
OUTPUT_PROFILE_NAME = "example_output_profile"
TAG_NAME = "example_tag"
CURRENCY = {"code": "USD", "scheme": "RMS", "vintage": "RL25", "asOfDate": "2026-01-01"}

# softwareVersionCode contains "HD", so analysis_type_for_software_version returns
# "HD" and no event rate scheme is required. That keeps the event rate scheme
# request out of the queue.
MODEL_PROFILE_BODY = {
    "count": 1,
    "items": [{
        "id": 11,
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "softwareVersionCode": "RL25HD",
    }],
}


def make_manager(make_analysis_manager, response, tags=None):
    """
    Build a manager wired for one submit_portfolio_analysis_job call.

    ``tags`` queues a Search Tags response, which the submit path requests only
    when tag_names is non-empty. Leaving it out means an unexpected tag lookup
    fails the test on FakeClient's exhausted queue.
    """
    responses = [
        response(200, json_body=[]),
        response(200, json_body=[{"uri": PORTFOLIO_URI, "portfolioId": 7}]),
        response(200, json_body=MODEL_PROFILE_BODY),
        response(200, json_body=[{"id": 22}]),
    ]
    if tags is not None:
        responses.append(response(200, json_body=tags))
    responses.append(response(201, headers={
        "location": "/platform/model/v1/jobs/901"
    }))
    return make_analysis_manager(responses=responses, edms=[{"exposureId": 42}])


def submit(manager, tag_names):
    """Submit one analysis with no treaties and no event rate scheme."""
    return manager.submit_portfolio_analysis_job(
        edm_name=EDM_NAME,
        portfolio_name=PORTFOLIO_NAME,
        job_name=JOB_NAME,
        analysis_profile_name=ANALYSIS_PROFILE_NAME,
        output_profile_name=OUTPUT_PROFILE_NAME,
        event_rate_scheme_name="",
        treaty_names=[],
        tag_names=tag_names,
        currency=CURRENCY,
    )


def test_empty_tag_names_submits_with_no_tags(make_analysis_manager, response):
    manager, client, _ = make_manager(make_analysis_manager, response)

    job_id, request_body = submit(manager, [])

    expected_body = {
        "resourceUri": PORTFOLIO_URI,
        "resourceType": "portfolio",
        "type": "HD",
        "settings": {
            "name": JOB_NAME,
            "modelProfileId": 11,
            "outputProfileId": 22,
            "treatyIds": [],
            "tagIds": [],
            "currency": CURRENCY,
            "franchiseDeductible": False,
            "minLossThreshold": 1.0,
            "treatConstructionOccupancyAsUnknown": True,
            "numMaxLossEvent": 1,
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
    assert GET_TAGS not in [call['path'] for call in client.calls], (
        "an empty tag_names must skip the tag lookup, not fail validation"
    )
    assert [call['path'] for call in client.calls] == [
        SEARCH_ANALYSIS_RESULTS,
        '/platform/riskdata/v1/exposures/42/portfolios',
        GET_MODEL_PROFILES,
        GET_OUTPUT_PROFILES,
        CREATE_ANALYSIS_JOB,
    ]


def test_tag_names_resolve_to_tag_ids(make_analysis_manager, response):
    manager, client, _ = make_manager(
        make_analysis_manager, response, tags=[{"tagId": 5}]
    )

    job_id, request_body = submit(manager, [TAG_NAME])

    assert job_id == 901
    assert request_body['settings']['tagIds'] == [5]
    assert client.calls[-2] == {
        "method": "GET",
        "path": GET_TAGS,
        "params": {"isActive": True, "filter": f"TAGNAME = '{TAG_NAME}'"},
        "json": None,
    }


def test_batch_defaults_missing_treaty_and_tag_names(
    make_analysis_manager, response
):
    manager, _, _ = make_analysis_manager(
        responses=[response(200, json_body=[])]
    )
    manager.submit_portfolio_analysis_job = Mock(side_effect=[(101, {})])

    job_ids = manager.submit_portfolio_analysis_jobs([{
        "edm_name": EDM_NAME,
        "portfolio_name": PORTFOLIO_NAME,
        "job_name": JOB_NAME,
        "analysis_profile_name": ANALYSIS_PROFILE_NAME,
        "output_profile_name": OUTPUT_PROFILE_NAME,
        "event_rate_scheme_name": "",
    }])

    assert job_ids == [101]
    assert manager.submit_portfolio_analysis_job.call_args_list[0].kwargs == {
        "edm_name": EDM_NAME,
        "portfolio_name": PORTFOLIO_NAME,
        "job_name": JOB_NAME,
        "analysis_profile_name": ANALYSIS_PROFILE_NAME,
        "output_profile_name": OUTPUT_PROFILE_NAME,
        "event_rate_scheme_name": "",
        "treaty_names": [],
        "tag_names": [],
        "skip_duplicate_check": True,
        "model_profile_id": None,
        "output_profile_id": None,
        "event_rate_scheme_id": None,
        "analysis_type": None,
    }
