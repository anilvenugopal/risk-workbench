"""
Tests for the event rate scheme rules in ``submit_portfolio_analysis_job``.

Two rules decide whether an analysis job is submitted at all. A DLM model
profile requires an event rate scheme; an HD one does not. And an event rate
scheme that was found must carry the model profile's ``perilCode`` and
``modelRegionCode`` — ``get_event_rate_scheme_by_name`` drops either filter when
the model profile does not supply it, so the returned scheme is not guaranteed
to match.

Neither rule fails the request at Risk Modeler. A DLM job submitted without an
event rate scheme, or one carrying a scheme for the wrong peril, runs and
returns losses computed against something the caller did not ask for. That is
why both are checked before the POST, and why the assertions below are on the
submitted body rather than on the return value.

``skip_duplicate_check=True``, an empty ``treaty_names`` and an explicit
``currency`` keep each test to the one POST the job submission makes.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from irp_integration.analysis import AnalysisManager
from irp_integration.exceptions import IRPReferenceDataError

from conftest import FakeClient, FakeResponse

# Invented names. Nothing here may name a real EDM, portfolio, analysis or
# tenant: this file ships in the sdist, so a name used here is published to PyPI.
EDM_NAME = "example_edm"
PORTFOLIO_NAME = "example_portfolio"
JOB_NAME = "example_analysis"
MODEL_PROFILE_NAME = "example_model_profile"
OUTPUT_PROFILE_NAME = "example_output_profile"
SCHEME_NAME = "example_event_rate_scheme"
PORTFOLIO_URI = "/platform/riskdata/v1/exposures/42/portfolios/7"
CURRENCY = {"code": "USD", "scheme": "RMS", "vintage": "RL18"}

DLM_VERSION = "RL25"
HD_VERSION = "HDv3.0"


class FakeReferenceDataManager:
    """Stand-in for ``ReferenceDataManager`` covering the analysis job lookups."""

    def __init__(
        self,
        software_version_code: str = DLM_VERSION,
        profile_peril_code: Optional[str] = "WS",
        profile_model_region_code: Optional[str] = "NAWS",
        scheme_peril_code: Optional[str] = "WS",
        scheme_model_region_code: Optional[str] = "NAWS",
    ) -> None:
        self.model_profile = {
            'id': 101,
            'softwareVersionCode': software_version_code,
            'perilCode': profile_peril_code,
            'modelRegionCode': profile_model_region_code,
        }
        self.event_rate_scheme = {
            'eventRateSchemeId': 55,
            'perilCode': scheme_peril_code,
            'modelRegionCode': scheme_model_region_code,
        }
        self.scheme_lookups: List[Dict[str, Any]] = []

    def get_model_profile_by_name(self, name: str) -> Dict[str, Any]:
        """Return a single model profile in the search-response shape."""
        return {'count': 1, 'items': [self.model_profile]}

    def get_output_profile_by_name(self, name: str) -> List[Dict[str, Any]]:
        """Return a single output profile as a list."""
        return [{'id': 202}]

    def get_event_rate_scheme_by_name(
        self,
        scheme_name: str,
        peril_code: Optional[str] = None,
        model_region_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record the filters the caller passed and return the configured scheme."""
        self.scheme_lookups.append({
            'scheme_name': scheme_name,
            'peril_code': peril_code,
            'model_region_code': model_region_code,
        })
        return {'count': 1, 'items': [self.event_rate_scheme]}

    def get_tag_ids_from_tag_names(self, tag_names: List[str]) -> List[int]:
        """Return no tag IDs; the tests pass no tag names."""
        return []


class FakeEDMManagerForAnalysis:
    """Stand-in for ``EDMManager`` covering the exposure ID lookup."""

    def search_edms(self, filter: str = "", **kwargs: Any) -> List[Dict[str, Any]]:
        """Return the one EDM the analysis job resolves."""
        return [{'exposureId': 42}]


class FakePortfolioManagerForAnalysis:
    """Stand-in for ``PortfolioManager`` covering the portfolio URI lookup."""

    def search_portfolios(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """Return the one portfolio the analysis job resolves."""
        return [{'uri': PORTFOLIO_URI}]


@pytest.fixture
def make_analysis_manager():
    """Return a factory building (AnalysisManager, FakeClient, FakeReferenceDataManager)."""
    def build(**reference_data_kwargs):
        client = FakeClient([
            FakeResponse(
                202,
                headers={'location': '/platform/riskdata/v1/jobs/9001'},
                has_body=False,
            ),
        ])
        reference_data = FakeReferenceDataManager(**reference_data_kwargs)
        irp = SimpleNamespace(
            client=client,
            reference_data=reference_data,
            treaty=SimpleNamespace(),
            edm=FakeEDMManagerForAnalysis(),
            portfolio=FakePortfolioManagerForAnalysis(),
        )
        return AnalysisManager(irp), client, reference_data

    return build


def submit(manager, event_rate_scheme_name=SCHEME_NAME):
    """Submit the analysis job with everything but the scheme name held fixed."""
    return manager.submit_portfolio_analysis_job(
        edm_name=EDM_NAME,
        portfolio_name=PORTFOLIO_NAME,
        job_name=JOB_NAME,
        analysis_profile_name=MODEL_PROFILE_NAME,
        output_profile_name=OUTPUT_PROFILE_NAME,
        event_rate_scheme_name=event_rate_scheme_name,
        treaty_names=[],
        tag_names=[],
        currency=CURRENCY,
        skip_duplicate_check=True,
    )


def test_dlm_without_event_rate_scheme_raises(make_analysis_manager):
    """Refuse a DLM analysis with no event rate scheme, before the POST."""
    manager, client, _ = make_analysis_manager(software_version_code=DLM_VERSION)

    with pytest.raises(
        IRPReferenceDataError,
        match="Event rate scheme is required for DLM analyses",
    ):
        submit(manager, event_rate_scheme_name="")

    assert client.calls == [], "the job must not be submitted"


def test_hd_without_event_rate_scheme_submits(make_analysis_manager):
    """Allow an HD analysis with no event rate scheme."""
    manager, client, _ = make_analysis_manager(software_version_code=HD_VERSION)

    job_id, data = submit(manager, event_rate_scheme_name="")

    assert job_id == 9001
    assert "eventRateSchemeId" not in data["settings"], (
        "no scheme was looked up, so none belongs in the settings"
    )
    assert client.calls[-1]["json"] == data


def test_mismatched_scheme_pair_raises(make_analysis_manager):
    """Refuse an event rate scheme whose peril and region are not the profile's."""
    manager, client, _ = make_analysis_manager(
        profile_peril_code="WS",
        profile_model_region_code="NAWS",
        scheme_peril_code="EQ",
        scheme_model_region_code="USEQ",
    )

    with pytest.raises(
        IRPReferenceDataError,
        match=r"Event rate scheme peril/region \(EQ, USEQ\) does not match "
              r"model profile peril/region \(WS, NAWS\)",
    ):
        submit(manager)

    assert client.calls == [], "the job must not be submitted"


def test_matching_scheme_pair_submits(make_analysis_manager):
    """Accept an event rate scheme carrying the model profile's peril and region."""
    manager, client, reference_data = make_analysis_manager()

    job_id, data = submit(manager)

    assert job_id == 9001
    assert data["settings"]["eventRateSchemeId"] == 55
    assert reference_data.scheme_lookups == [{
        'scheme_name': SCHEME_NAME,
        'peril_code': "WS",
        'model_region_code': "NAWS",
    }], "the model profile's codes filter the event rate scheme lookup"
    assert client.calls[-1]["json"] == data


def test_unknown_profile_peril_code_skips_pair_validation(make_analysis_manager):
    """
    Submit when the model profile has no perilCode, even on a scheme mismatch.

    Without a profile peril code there is nothing to compare the scheme against,
    so the pair rule cannot fire. ``get_event_rate_scheme_by_name`` also drops
    the ``perilCode`` filter in this case, which is why the lookup records None.
    """
    manager, client, reference_data = make_analysis_manager(
        profile_peril_code=None,
        scheme_peril_code="EQ",
        scheme_model_region_code="USEQ",
    )

    job_id, data = submit(manager)

    assert job_id == 9001
    assert data["settings"]["eventRateSchemeId"] == 55
    assert reference_data.scheme_lookups[0]["peril_code"] is None


@pytest.mark.parametrize(
    ("software_version_code", "expected_type"),
    [(DLM_VERSION, "DLM"), (HD_VERSION, "HD")],
)
def test_submitted_body_carries_the_job_type(
    make_analysis_manager,
    software_version_code,
    expected_type,
):
    """Post the model profile's analysis type as the job's ``type`` field."""
    manager, client, _ = make_analysis_manager(
        software_version_code=software_version_code,
    )

    _, data = submit(manager)

    assert data["type"] == expected_type
    assert data["resourceUri"] == PORTFOLIO_URI
    assert data["resourceType"] == "portfolio"
    assert client.calls[-1]["json"]["type"] == expected_type
