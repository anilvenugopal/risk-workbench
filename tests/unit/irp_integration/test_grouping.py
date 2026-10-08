"""Offline contract tests for rules-based grouping inspection and submission."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from conftest import FakeClient, FakeResponse
from irp_integration.constants import GET_ANALYSIS_GROUPING_JOB
from irp_integration.exceptions import IRPAPIError, IRPGroupingValidationError, IRPValidationError
from irp_integration.grouping import (
    EventRateSelection,
    GroupingCurrency,
    GroupingManager,
    GroupingPartitionKey,
    GroupingSettings,
    SimulationPeriodsSelection,
    SimulationSetSelection,
)

# Invented petName values for JPWS PET 15 and PET 16. The PET fixture lists
# both IDs under model version 2.0 and 2.1 with a different name per version,
# so a region fact's pet_name proves which row the resolved model version
# selected.
TYPHOON_PET_NAME = "Example Stochastic Rates - Typhoon Only"
NON_TYPHOON_PET_NAME = "Example Stochastic Rates - Non-Typhoon Flood Only"

WS_PARTITION = GroupingPartitionKey("WS", "NA", "11.0")
MIXED_SIMULATION_SET = [SimulationSetSelection(WS_PARTITION, 1001)]


def analysis(
    analysis_id: int,
    *,
    framework: str = "ELT",
    engine_type: str = "DLM",
    scheme_id: Optional[int] = 101,
    scheme_name: Optional[str] = None,
    is_group: bool = False,
) -> Dict[str, Any]:
    """Build one invented analysis-detail fixture."""
    return {
        "analysisId": analysis_id,
        "analysisFramework": framework,
        "engineType": "Group" if is_group else engine_type,
        "isGroup": is_group,
        "engineVersion": "HDv2.0" if framework == "PLT" else "RL23",
        "perilCode": "WF" if framework == "PLT" else "WS",
        "regionCode": "NA",
        "eventRateSchemeId": scheme_id,
        "eventRateSchemeName": scheme_name,
    }


def region(
    analysis_id: int,
    *,
    framework: str = "ELT",
    scheme_id: Optional[int] = 101,
    pet_id: Optional[int] = None,
    periods: Optional[int] = None,
    sub_region: str = "FL",
    apply_contract: bool = False,
) -> Dict[str, Any]:
    """Build one invented analysis-region fixture."""
    return {
        "analysisId": analysis_id,
        "framework": framework,
        "engineVersion": "HDv2.0" if framework == "PLT" else "RL23",
        "peril": "WF" if framework == "PLT" else "WS",
        "region": "NA",
        "subRegion": sub_region,
        "rateSchemeId": scheme_id,
        "petId": pet_id,
        "periods": periods,
        "applyContractFlag": apply_contract,
    }


def multi_peril_group(analysis_id: int) -> Dict[str, Any]:
    """Build one multi-peril group detail: perilCode YY, peril "Multi-Peril"."""
    return {
        "analysisId": analysis_id,
        "analysisFramework": "ELT",
        "engineType": "Group",
        "engineVersion": "RL25",
        "groupType": "CDGP",
        "isGroup": False,
        "peril": "Multi-Peril",
        "perilCode": "YY",
        "region": "North Atlantic (including Hawaii)",
        "regionCode": "NA",
    }


def multi_peril_regions(analysis_id: int) -> List[Dict[str, Any]]:
    """Build one Earthquake and one Windstorm region row carrying display names."""
    return [
        {
            "analysisId": analysis_id,
            "framework": "ELT",
            "engineVersion": "RL25",
            "peril": "Earthquake",
            "region": "NA",
            "subRegion": "CA",
            "eventRateSchemeId": 103,
            "petId": 0,
            "periods": 0,
            "applyContractFlag": False,
        },
        {
            "analysisId": analysis_id,
            "framework": "ELT",
            "engineVersion": "RL25",
            "peril": "Windstorm",
            "region": "NA",
            "subRegion": "FL",
            "eventRateSchemeId": 101,
            "petId": 0,
            "periods": 0,
            "applyContractFlag": False,
        },
    ]


class FakeAnalysisManager:
    """Return mutable analysis, region, and treaty fixtures by exact ID."""

    def __init__(
        self,
        details: Dict[int, Dict[str, Any]],
        regions: Dict[int, List[Dict[str, Any]]],
        treaties: Optional[Dict[int, List[Dict[str, Any]]]] = None,
    ):
        self.details = details
        self.regions = regions
        self.treaties = treaties or {}

    def get_analysis_by_id(self, analysis_id: int) -> Dict[str, Any]:
        """Return an exact-ID detail response."""
        return self.details.get(analysis_id, {})

    def get_regions(self, analysis_id: int) -> List[Dict[str, Any]]:
        """Return exact-ID region rows."""
        return self.regions.get(analysis_id, [])

    def search_analysis_treaties_paginated(
        self, analysis_id: int
    ) -> List[Dict[str, Any]]:
        """Return every treaty associated with one analysis ID."""
        return self.treaties.get(analysis_id, [])


class FakeReferenceDataManager:
    """Resolve fixture model versions, PETs, event-rate schemes, and exact
    simulation mappings."""

    def __init__(self) -> None:
        self.event_rate_schemes = [
            {
                "eventRateSchemeId": 101,
                "eventRateSchemeName": "Historical",
                "perilCode": "WS",
                "modelRegionCode": "NAWS",
                "modelVersionCode": "11.0",
            },
            {
                "eventRateSchemeId": 102,
                "eventRateSchemeName": "Stochastic",
                "perilCode": "WS",
                "modelRegionCode": "NAWS",
                "modelVersionCode": "11.0",
            },
            {
                "eventRateSchemeId": 103,
                "eventRateSchemeName": "Time-Independent",
                "perilCode": "EQ",
                "modelRegionCode": "NAEQ",
                "modelVersionCode": "17.0",
            },
        ]
        self.pet_metadata = [
            {
                "id": 50,
                "perilCode": "WF",
                "modelRegionCode": "NAWF",
                "modelVersionCode": "2.0",
                "numberOfPeriods": 100000,
                "petName": "Example Stochastic Rates - CatLoss US (Default)",
            },
            {
                "id": 51,
                "perilCode": "WF",
                "modelRegionCode": "NAWF",
                "modelVersionCode": "2.0",
                "numberOfPeriods": 100000,
                "petName": "Example Stochastic Rates - CatLoss US Utility",
            },
            {
                "id": 60,
                "perilCode": "EQ",
                "modelRegionCode": "USEQ",
                "modelVersionCode": "23.0",
                "numberOfPeriods": 50000,
            },
            {"id": 15, "perilCode": "WS", "modelRegionCode": "JPWS",
             "modelVersionCode": "2.0", "petName": f"{TYPHOON_PET_NAME} v2.0"},
            {"id": 16, "perilCode": "WS", "modelRegionCode": "JPWS",
             "modelVersionCode": "2.0", "petName": f"{NON_TYPHOON_PET_NAME} v2.0"},
            {"id": 15, "perilCode": "WS", "modelRegionCode": "JPWS",
             "modelVersionCode": "2.1", "petName": TYPHOON_PET_NAME},
            {"id": 16, "perilCode": "WS", "modelRegionCode": "JPWS",
             "modelVersionCode": "2.1", "petName": NON_TYPHOON_PET_NAME},
        ]
        self.event_rate_scheme_total: Any = None
        self.pet_metadata_error: Optional[IRPAPIError] = None
        self.pet_metadata_calls: List[Dict[str, Any]] = []
        self.simulation_sets = [
            {
                "id": 1001,
                "eventRateSchemeId": 101,
                "name": "North America Windstorm Stochastic",
                "perilCode": "WS",
                "modelRegionCode": "NAWS",
                "modelVersionCode": "11.0",
                "defaultPeriods": 100000,
                "peqtSource": "SYSTEM",
            }
        ]
        self.model_version_error: Optional[IRPAPIError] = None

    def get_event_rate_schemes(self) -> Dict[str, Any]:
        """Return the fixture scheme list in the Platform's envelope.

        ``totalCount`` reports the row count unless a test overrides it with
        ``event_rate_scheme_total``, which stands in for a server-side cap or
        for a ``totalCount`` the response restated in another shape.
        """
        total = self.event_rate_scheme_total
        return {
            "items": self.event_rate_schemes,
            "totalCount": len(self.event_rate_schemes) if total is None else total,
        }

    def get_model_version_by_engine_region_peril(
        self, engine_version: str, region_code: str, peril_code: str
    ) -> str:
        """Return the exact fixture model version.

        ``SoftwareModelVersionMap`` carries no row for a peril display name or
        for the multi-peril code ``YY``, so an unmapped peril raises here the
        way the reference read does rather than returning a version for it.
        """
        if self.model_version_error:
            raise self.model_version_error
        if peril_code not in {"EQ", "WF", "WS"}:
            raise IRPAPIError(
                f"No model version mapping found for {engine_version}, "
                f"{region_code}, {peril_code}"
            )
        if engine_version in {"RL23", "RL25"} and region_code == "NA":
            if peril_code == "EQ":
                return "17.0"
            return "11.0"
        if region_code == "US" and peril_code == "EQ":
            return "23.0"
        return "2.1" if engine_version == "HDv2.1" else "2.0"

    def get_all_pet_metadata(self) -> List[Dict[str, Any]]:
        """Return every PET fixture row."""
        return list(self.pet_metadata)

    def get_pet_metadata_by_id(self, pet_id: int) -> Dict[str, Any]:
        """Preserve strict lookup behavior in the test double."""
        matches = [pet for pet in self.pet_metadata if pet["id"] == pet_id]
        if not matches:
            raise IRPAPIError(f"No PET metadata found for PET ID {pet_id}")
        if len(matches) > 1:
            raise IRPAPIError(f"Multiple PET metadata rows found for PET ID {pet_id}")
        return matches[0]

    def get_pet_metadata_exact(self, **kwargs: Any) -> Dict[str, Any]:
        """Return one PET fixture matching every supplied qualifier."""
        self.pet_metadata_calls.append(kwargs)
        if self.pet_metadata_error:
            raise self.pet_metadata_error
        matches = [
            pet for pet in self.pet_metadata
            if pet["id"] == kwargs["pet_id"]
            and pet["modelVersionCode"] == kwargs["model_version"]
            and (
                kwargs["model_region_code"] is None
                or pet["modelRegionCode"] == kwargs["model_region_code"]
            )
        ]
        if not matches:
            raise IRPAPIError("No PET metadata found")
        if len(matches) > 1:
            raise IRPAPIError("Multiple PET metadata rows found")
        return matches[0]

    def get_all_simulation_sets(self) -> List[Dict[str, Any]]:
        """Return every active simulation-set fixture."""
        return list(self.simulation_sets)


def make_manager(
    details: Dict[int, Dict[str, Any]],
    regions: Dict[int, List[Dict[str, Any]]],
    *,
    treaties: Optional[Dict[int, List[Dict[str, Any]]]] = None,
    post: bool = False,
) -> tuple[GroupingManager, FakeClient, FakeReferenceDataManager]:
    """Build a grouping manager over offline test doubles."""
    responses = []
    if post:
        responses.append(FakeResponse(
            201,
            headers={"location": "/platform/grouping/v1/jobs/7001"},
            has_body=False,
        ))
    client = FakeClient(responses)
    reference_data = FakeReferenceDataManager()
    irp = SimpleNamespace(
        client=client,
        analysis=FakeAnalysisManager(details, regions, treaties),
        reference_data=reference_data,
    )
    return GroupingManager(irp), client, reference_data


def settings() -> GroupingSettings:
    """Return explicit caller settings with no optional fields."""
    return GroupingSettings(
        analysis_name="example_group",
        currency=GroupingCurrency(
            code="USD", scheme="RMS", vintage="RL25", as_of_date="2026-01-01"
        ),
        propagate_detailed_losses=True,
        num_of_simulations=1,
    )


def submit(manager, inspection, **overrides):
    """Submit with the four required arguments; each override replaces one."""
    kwargs = dict(
        analysis_ids=list(inspection.analysis_ids),
        settings=settings(),
        event_rate_selections=[],
        expected_inspection_fingerprint=inspection.fingerprint,
    )
    kwargs.update(overrides)
    return manager.submit(**kwargs)


def pure_elt_fixtures(conflicting: bool = False):
    """Return two pure-ELT members in one partition."""
    second_scheme = 102 if conflicting else 101
    details = {
        1: analysis(1, scheme_id=101, scheme_name="Historical"),
        2: analysis(2, scheme_id=second_scheme, scheme_name="Stochastic"),
    }
    regions = {
        1: [region(1, scheme_id=101)],
        2: [region(2, scheme_id=second_scheme)],
    }
    return details, regions


def treaty(
    analysis_id: int,
    treaty_id: int,
    *,
    treaty_number: str = "CATA-1",
    occurrence_limit: float = 1_000_000,
) -> Dict[str, Any]:
    """Build one Platform analysis-treaty fixture."""
    return {
        "analysisId": analysis_id,
        "treatyId": treaty_id,
        "treatyNumber": treaty_number,
        "treatyName": f"Display name {treaty_id}",
        "cedant": {"cedantId": "CED-1", "cedantName": "Example Cedant"},
        "producer": {"producerId": str(treaty_id), "producerName": "Producer"},
        "treatyType": "CATA",
        "currency": {"id": 1, "code": "USD", "name": "US Dollar"},
        "attachmentBasis": "L",
        "attachmentLevel": "PORT",
        "premium": treaty_id * 1000,
        "occurrenceLimit": occurrence_limit,
        "attachmentPoint": 100_000,
        "riskLimit": None,
        "retentionAmount": None,
        "percentagePlaced": 100,
        "effectiveDate": "2026-01-01T00:00:00.000Z",
        "expirationDate": "2026-12-31T00:00:00.000Z",
        "percentageRetention": 100,
        "percentageRiShare": 100,
        "percentageCovered": 100,
        "priority": 1,
        "numberOfReinstatements": 1,
        "reinstatementCharge": 0,
        "maolAmount": None,
        "isValid": True,
        "userId1": f"user-{treaty_id}",
        "userId2": None,
        "aggregateDeductible": 0,
        "aggregateLimit": 0,
        "uri": f"/analyses/{analysis_id}/treaties/{treaty_id}",
        "lobs": [{"lobId": 9, "lobName": "FACTORY", "uri": f"/lobs/{treaty_id}"}],
        "lossOccurrences": [{
            "id": treaty_id,
            "treatyId": treaty_id,
            "uri": f"/loss-occurrences/{treaty_id}",
            "regionPeril": {"id": 4, "code": "NAWF", "name": "Wildfire"},
            "lossOccurrenceTime": 168,
            "lossOccurrenceRadius": 0,
            "radiusUnit": {"id": 1, "code": "MI", "name": "Miles"},
            "multiLossOccurrence": {"id": 2, "code": "NO", "name": "No"},
        }],
        "tagIds": [treaty_id],
    }


def test_pure_elt_inspection_reports_one_observed_scheme():
    """Classify pure ELT without inventing an event-rate choice."""
    manager, client, _ = make_manager(*pure_elt_fixtures())

    result = manager.inspect(analysis_ids=[1, 2])

    assert result.output_loss_table == "ELT"
    assert result.simulate_to_plt is False
    assert result.blocking_problems == ()
    assert result.partitions[0].event_rate_selection_required is False
    assert [option.event_rate_scheme_id for option in result.partitions[0].event_rate_scheme_options] == [101]
    assert client.calls == []


def test_matching_treaty_terms_ignore_non_loss_properties():
    """Do not warn for IDs, names, premiums, producers, tags, or URIs."""
    details, regions = pure_elt_fixtures()
    manager, _, _ = make_manager(
        details,
        regions,
        treaties={1: [treaty(1, 11)], 2: [treaty(2, 22)]},
    )

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.warnings == ()
    assert inspection.blocking_problems == ()


def test_inconsistent_treaty_terms_return_structured_warning():
    """Return compared treaties sorted by analysis and treaty ID with the differing value."""
    details, regions = pure_elt_fixtures()
    details[3] = analysis(3, scheme_id=101)
    regions[3] = [region(3, scheme_id=101)]
    manager, _, _ = make_manager(
        details,
        regions,
        treaties={
            1: [treaty(1, 31)],
            2: [treaty(2, 22, occurrence_limit=2_000_000)],
            3: [treaty(3, 13)],
        },
    )

    inspection = manager.inspect(analysis_ids=[3, 1, 2])

    assert inspection.blocking_problems == ()
    assert len(inspection.warnings) == 1
    warning = inspection.warnings[0]
    assert warning.code == "inconsistent_treaty_terms"
    assert warning.analysis_ids == (1, 2, 3)
    assert warning.treaty_numbers == ("CATA-1",)
    assert warning.treaty_ids == (13, 22, 31)
    assert warning.differing_fields == ("occurrenceLimit",)
    assert [
        (row.analysis_id, row.treaty_id, row.treaty_number)
        for row in warning.treaties
    ] == [(1, 31, "CATA-1"), (2, 22, "CATA-1"), (3, 13, "CATA-1")]
    assert [row.terms["occurrenceLimit"] for row in warning.treaties] == [
        1_000_000,
        2_000_000,
        1_000_000,
    ]


def test_inconsistent_treaty_currency_returns_normalized_codes():
    """Return normalized currency codes that explain a currency mismatch."""
    details, regions = pure_elt_fixtures()
    first = treaty(1, 11)
    second = treaty(2, 22)
    second["currency"] = {"id": 2, "code": "CAD", "name": "Canadian Dollar"}
    manager, _, _ = make_manager(
        details,
        regions,
        treaties={1: [first], 2: [second]},
    )

    warning = manager.inspect(analysis_ids=[1, 2]).warnings[0]

    assert warning.differing_fields == ("currency",)
    assert [row.terms["currency"] for row in warning.treaties] == ["USD", "CAD"]


def test_inconsistent_treaty_row_keeps_missing_treaty_id():
    """Return a compared analysis treaty whose treatyId is absent."""
    details, regions = pure_elt_fixtures()
    first = treaty(1, 11)
    first.pop("treatyId")
    manager, _, _ = make_manager(
        details,
        regions,
        treaties={
            1: [first],
            2: [treaty(2, 22, occurrence_limit=2_000_000)],
        },
    )

    warning = manager.inspect(analysis_ids=[1, 2]).warnings[0]

    assert warning.treaty_ids == (22,)
    assert [(row.analysis_id, row.treaty_id) for row in warning.treaties] == [
        (1, None),
        (2, 22),
    ]


def test_different_treaty_numbers_are_not_compared():
    """Compare terms only when Treaty Number identifies the same treaty."""
    details, regions = pure_elt_fixtures()
    manager, _, _ = make_manager(
        details,
        regions,
        treaties={
            1: [treaty(1, 11, treaty_number="CATA-1")],
            2: [
                treaty(
                    2,
                    22,
                    treaty_number="CATA-2",
                    occurrence_limit=2_000_000,
                )
            ],
        },
    )

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.warnings == ()


def test_loss_occurrence_and_lob_differences_are_treaty_warnings():
    """Compare nested loss-occurrence terms and LOB assignments."""
    details, regions = pure_elt_fixtures()
    first = treaty(1, 11)
    second = treaty(2, 22)
    second["lobs"] = [{"lobId": 10, "lobName": "OFFICE"}]
    second["lossOccurrences"][0]["lossOccurrenceTime"] = 72
    manager, _, _ = make_manager(
        details,
        regions,
        treaties={1: [first], 2: [second]},
    )

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.warnings[0].differing_fields == ("lobs", "lossOccurrences")


def test_inconsistent_treaty_warning_does_not_block_submission():
    """Submit when treaty terms remain inconsistent after reinspection."""
    details, regions = pure_elt_fixtures()
    manager, client, _ = make_manager(
        details,
        regions,
        treaties={
            1: [treaty(1, 11)],
            2: [treaty(2, 22, occurrence_limit=2_000_000)],
        },
        post=True,
    )
    inspection = manager.inspect(analysis_ids=[1, 2])

    submission = submit(manager, inspection)

    assert submission.job_id == 7001
    assert client.calls[-1]["method"] == "POST"


def test_treaty_term_change_after_inspection_changes_fingerprint():
    """Require another review when treaty terms change before submission."""
    details, regions = pure_elt_fixtures()
    treaties = {1: [treaty(1, 11)], 2: [treaty(2, 22)]}
    manager, client, _ = make_manager(details, regions, treaties=treaties)
    inspection = manager.inspect(analysis_ids=[1, 2])
    treaties[2][0]["occurrenceLimit"] = 2_000_000

    with pytest.raises(IRPGroupingValidationError) as exc_info:
        submit(manager, inspection)

    assert exc_info.value.problems[0].code == "inspection_changed"
    assert client.calls == []


def test_conflicting_partition_offers_and_submits_unobserved_applicable_scheme():
    """Offer an unobserved scheme matching all DLM partition codes."""
    manager, client, reference_data = make_manager(
        *pure_elt_fixtures(conflicting=True), post=True
    )
    reference_data.event_rate_schemes.extend([
        {
            "eventRateSchemeId": 103,
            "eventRateSchemeName": "Applicable but unobserved",
            "perilCode": "WS",
            "modelRegionCode": "NAWS",
            "modelVersionCode": "11.0",
            "isDefault": True,
        },
        {
            "eventRateSchemeId": 104,
            "eventRateSchemeName": "Wrong model region",
            "perilCode": "WS",
            "modelRegionCode": "EUWS",
            "modelVersionCode": "11.0",
        },
        {
            "eventRateSchemeId": 105,
            "eventRateSchemeName": "Wrong peril",
            "perilCode": "EQ",
            "modelRegionCode": "NAWS",
            "modelVersionCode": "11.0",
        },
    ])

    inspection = manager.inspect(analysis_ids=[1, 2])
    partition = inspection.partitions[0]

    assert partition.event_rate_selection_required is True
    assert [
        (option.event_rate_scheme_id, option.label)
        for option in partition.event_rate_scheme_options
    ] == [
        (101, "Historical"),
        (102, "Stochastic"),
        (103, "Applicable but unobserved"),
    ]

    result = submit(
        manager, inspection,
        event_rate_selections=[EventRateSelection(partition.key, 103)],
    )

    assert result.request_body["settings"]["regionPerilSimulationSet"][0][
        "eventRateSchemeId"
    ] == 103
    assert result.request_body == client.calls[-1]["json"]


def test_dlm_partition_matches_event_rates_by_model_version():
    """Offer the NAEQ 17.0 schemes and exclude a NAEQ scheme from model version 9.0."""
    details, regions = pure_elt_fixtures(conflicting=True)
    details[1].update({"perilCode": "EQ", "eventRateSchemeId": 901})
    details[2].update({"perilCode": "EQ", "eventRateSchemeId": 902})
    for raw_region in regions[1]:
        raw_region.update({"peril": "EQ", "rateSchemeId": 901})
    for raw_region in regions[2]:
        raw_region.update({"peril": "EQ", "rateSchemeId": 902})
    manager, _, reference_data = make_manager(details, regions)
    reference_data.event_rate_schemes = [
        {
            "eventRateSchemeId": scheme_id,
            "eventRateSchemeName": f"NAEQ scheme {scheme_id}",
            "perilCode": "EQ",
            "modelRegionCode": "NAEQ",
            "modelVersionCode": model_version,
        }
        for scheme_id, model_version in ((901, "17.0"), (902, "17.0"), (903, "9.0"))
    ]

    inspection = manager.inspect(analysis_ids=[1, 2])

    partition = inspection.partitions[0]
    assert partition.key == GroupingPartitionKey("EQ", "NA", "17.0")
    assert [
        option.event_rate_scheme_id
        for option in partition.event_rate_scheme_options
    ] == [901, 902]


def test_conflicting_partition_without_a_reference_row_blocks():
    """Block a conflicting partition that no active reference scheme matches."""
    manager, _, reference_data = make_manager(*pure_elt_fixtures(conflicting=True))
    reference_data.event_rate_schemes = []

    inspection = manager.inspect(analysis_ids=[1, 2])

    codes = {problem.code for problem in inspection.blocking_problems}
    assert "event_rate_scheme_mapping_missing" in codes
    assert "event_rate_scheme_missing" not in codes
    partition = inspection.partitions[0]
    assert partition.event_rate_selection_required is True
    assert partition.event_rate_scheme_options == ()
    problem = next(
        problem for problem in inspection.blocking_problems
        if problem.code == "event_rate_scheme_mapping_missing"
    )
    assert problem.partition == WS_PARTITION
    assert problem.analysis_ids == (1, 2)


def test_dlm_engine_type_matches_in_any_case():
    """Apply the DLM model-version restriction when engineType is not upper case."""
    details, regions = pure_elt_fixtures(conflicting=True)
    for detail in details.values():
        detail["engineType"] = "dlm"
    manager, _, reference_data = make_manager(details, regions)
    reference_data.event_rate_schemes.append({
        "eventRateSchemeId": 103,
        "eventRateSchemeName": "Earlier model version",
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "10.0",
    })

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert [
        option.event_rate_scheme_id
        for option in inspection.partitions[0].event_rate_scheme_options
    ] == [101, 102]


def test_hd_partition_retains_event_rate_comparison_without_model_version():
    """Compare an HD partition by peril and model region only."""
    details, regions = pure_elt_fixtures(conflicting=True)
    for detail in details.values():
        detail["engineType"] = "HD"
    manager, _, reference_data = make_manager(details, regions)
    reference_data.event_rate_schemes.append({
        "eventRateSchemeId": 103,
        "eventRateSchemeName": "Different model version",
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "10.0",
    })

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert [
        option.event_rate_scheme_id
        for option in inspection.partitions[0].event_rate_scheme_options
    ] == [101, 102, 103]


def test_dlm_partition_in_hd_group_matches_event_rates_by_model_version():
    """Filter a DLM partition by modelVersionCode inside a PLT HD group."""
    details, regions = pure_elt_fixtures(conflicting=True)
    details[3] = analysis(3, framework="PLT", engine_type="HD", scheme_id=None)
    regions[3] = [
        region(3, framework="PLT", scheme_id=None, pet_id=50, periods=100000)
    ]
    manager, _, reference_data = make_manager(details, regions)
    reference_data.event_rate_schemes.append({
        "eventRateSchemeId": 103,
        "eventRateSchemeName": "Earlier model version",
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "10.0",
    })

    inspection = manager.inspect(analysis_ids=[1, 2, 3])

    assert inspection.output_loss_table == "PLT"
    partition = next(
        candidate for candidate in inspection.partitions
        if candidate.key == GroupingPartitionKey("WS", "NA", "11.0")
    )
    assert [
        option.event_rate_scheme_id
        for option in partition.event_rate_scheme_options
    ] == [101, 102]


def test_is_default_does_not_select_a_scheme_for_a_conflict():
    """Require a caller selection even when an applicable scheme is default."""
    manager, client, reference_data = make_manager(
        *pure_elt_fixtures(conflicting=True)
    )
    reference_data.event_rate_schemes[0]["isDefault"] = True
    inspection = manager.inspect(analysis_ids=[1, 2])

    with pytest.raises(IRPGroupingValidationError) as raised:
        submit(manager, inspection)

    assert inspection.partitions[0].event_rate_selection_required is True
    assert [problem.code for problem in raised.value.problems] == [
        "event_rate_selection_missing"
    ]
    assert client.calls == []


def test_display_name_region_rows_resolve_to_the_detail_codes():
    """Platform region rows carry ``peril`` as a display name; both members
    must land in one coded partition with labelled choices and no problems."""
    details, regions = pure_elt_fixtures(conflicting=True)
    for detail in details.values():
        detail.update({
            "peril": "Windstorm",
            "region": "North Atlantic (including Hawaii)",
            "eventRateSchemeName": None,
            "eventRateSchemeNames": [
                {"id": 0, "code": "0", "name": f"Scheme {detail['eventRateSchemeId']}"}
            ],
        })
    for rows in regions.values():
        for row in rows:
            row["peril"] = "Windstorm"
    manager, _, _ = make_manager(details, regions)

    result = manager.inspect(analysis_ids=[1, 2])

    assert result.blocking_problems == ()
    assert [partition.key for partition in result.partitions] == [WS_PARTITION]
    assert [
        (option.event_rate_scheme_id, option.label)
        for option in result.partitions[0].event_rate_scheme_options
    ] == [(101, "Historical"), (102, "Stochastic")]


def test_truncated_event_rate_scheme_read_raises():
    """Raise when the reference read returns fewer rows than its totalCount.

    The endpoint applies no default page size today, so a short list means the
    server capped the read and the peril and model-region comparisons would be
    made against part of the table."""
    manager, _, reference = make_manager(*pure_elt_fixtures())
    reference.event_rate_scheme_total = len(reference.event_rate_schemes) + 1

    with pytest.raises(IRPAPIError, match=r"Event-rate scheme search returned \d+ of \d+ rows"):
        manager.inspect(analysis_ids=[1, 2])


@pytest.mark.parametrize("restate", [str, float])
def test_truncated_event_rate_scheme_read_raises_on_a_restated_total(restate):
    """Catch a short read whether totalCount is an int, a string or a float.

    ``_positive_int`` requires ``isinstance(value, int)``, so a ``totalCount``
    of ``"151"`` or ``151.0`` would switch the guard off — a changed response
    shape is exactly the case where a short read would go unnoticed."""
    manager, _, reference = make_manager(*pure_elt_fixtures())
    reference.event_rate_scheme_total = restate(
        len(reference.event_rate_schemes) + 1
    )

    with pytest.raises(IRPAPIError, match=r"Event-rate scheme search returned \d+ of \d+ rows"):
        manager.inspect(analysis_ids=[1, 2])


def test_event_rate_scheme_total_that_is_not_a_row_count_raises():
    """Raise rather than skipping the truncation guard on an unreadable total."""
    manager, _, reference = make_manager(*pure_elt_fixtures())
    reference.event_rate_scheme_total = {}

    with pytest.raises(IRPAPIError, match="totalCount that is not a row count"):
        manager.inspect(analysis_ids=[1, 2])


def test_multi_peril_group_members_partition_by_the_peril_their_schemes_name():
    """A multi-peril group's detail carries perilCode YY and peril "Multi-Peril"
    while its region rows carry "Earthquake" and "Windstorm". YY is RMS ALL
    PERILS and is not in SoftwareModelVersionMap, so the peril code has to come
    from each row's eventRateSchemeId."""
    details = {1: multi_peril_group(1), 2: multi_peril_group(2)}
    regions = {1: multi_peril_regions(1), 2: multi_peril_regions(2)}
    manager, _, _ = make_manager(details, regions, post=True)

    inspection = manager.inspect(analysis_ids=[1, 2])
    result = submit(manager, inspection)

    assert inspection.blocking_problems == ()
    assert [member.is_group for member in inspection.members] == [True, True]
    assert {partition.key for partition in inspection.partitions} == {
        GroupingPartitionKey("EQ", "NA", "17.0"),
        GroupingPartitionKey("WS", "NA", "11.0"),
    }
    assert {
        (fact.peril_code, fact.model_region_code)
        for member in inspection.members
        for fact in member.regions
    } == {("EQ", "CAEQ"), ("WS", "FLWS")}
    assert result.job_id == 7001


def test_multi_peril_row_resolving_to_no_peril_code_blocks():
    """Block the row whose IDs resolve no peril code: peril "Multi-Peril" falls
    back to the detail's YY, and no model-version mapping carries YY."""
    details = {1: multi_peril_group(1), 2: multi_peril_group(2)}
    regions = {
        1: [dict(multi_peril_regions(1)[0], eventRateSchemeId=0, peril="Multi-Peril")],
        2: multi_peril_regions(2),
    }
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert [problem.code for problem in inspection.blocking_problems] == [
        "model_version_mapping_missing"
    ]
    assert inspection.members[0].regions == ()


def test_row_peril_is_used_when_the_scheme_model_region_names_another_peril():
    """A scheme registered under a modelRegionCode whose peril the row does not
    carry must not drop the row. Scheme 104 names NACS, so the derived peril is
    CS and no SoftwareModelVersionMap row carries RL23/NA/CS; the row's own
    perilCode WS resolves 11.0 and is the peril the fact keeps."""
    details = {1: analysis(1), 2: analysis(2)}
    regions = {1: [region(1, scheme_id=104)], 2: [region(2)]}
    manager, _, reference = make_manager(details, regions)
    reference.event_rate_schemes.append({
        "eventRateSchemeId": 104,
        "eventRateSchemeName": "Convective Storm Stochastic",
        "perilCode": "CS",
        "modelRegionCode": "NACS",
        "modelVersionCode": "5.0",
    })

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.blocking_problems == ()
    assert [
        (fact.peril_code, fact.model_region_code, fact.model_version)
        for fact in inspection.members[0].regions
    ] == [("WS", "FLWS", "11.0")]


def test_each_offered_scheme_is_named_from_reference_data():
    """A group's detail names only one of the schemes its members ran under, so
    naming every offered scheme from the detail showed one name on both. Each
    ID takes its own name from the reference list."""
    details = {
        1: analysis(1, is_group=True, scheme_id=None, engine_type="Group"),
        2: analysis(2, scheme_id=101, scheme_name="Historical"),
    }
    details[1]["additionalProperties"] = [{
        "key": "eventRateSchemes",
        "properties": [{"id": 0, "name": "", "value": {
            "regionCode": "NA", "perilCode": "WS", "framework": "ELT",
            "eventRateSchemeId": 101,
            "eventRateSchemeName": "Historical"}}],
    }]
    regions = {
        1: [region(1, scheme_id=101), region(1, scheme_id=102, sub_region="TX")],
        2: [region(2, scheme_id=101)],
    }
    manager, _, _ = make_manager(details, regions)

    result = manager.inspect(analysis_ids=[1, 2])

    partition = result.partitions[0]
    assert partition.event_rate_selection_required is True
    assert [
        (option.event_rate_scheme_id, option.label)
        for option in partition.event_rate_scheme_options
    ] == [(101, "Historical"), (102, "Stochastic")]


def test_reversing_members_keeps_partition_and_choice_order():
    """Normalize partitions and the fingerprint independently of member order."""
    manager, _, _ = make_manager(*pure_elt_fixtures(conflicting=True))

    forward = manager.inspect(analysis_ids=[1, 2])
    reverse = manager.inspect(analysis_ids=[2, 1])

    assert forward.partitions == reverse.partitions
    assert forward.fingerprint == reverse.fingerprint


def test_conflicting_pure_elt_submission_emits_verified_zero_simulation_fields():
    """Use explicit zeros only for the documented pure-ELT RPS encoding."""
    manager, client, _ = make_manager(*pure_elt_fixtures(conflicting=True), post=True)
    inspection = manager.inspect(analysis_ids=[1, 2])
    partition = inspection.partitions[0].key

    result = submit(
        manager, inspection,
        event_rate_selections=[EventRateSelection(partition, 102)],
    )

    assert result.job_id == 7001
    assert result.request_body == client.calls[-1]["json"]
    request_settings = result.request_body["settings"]
    assert request_settings["simulateToPLT"] is False
    assert "description" not in request_settings
    assert "reportingWindowStart" not in request_settings
    assert request_settings["regionPerilSimulationSet"] == [{
        "engineVersion": "RL23",
        "eventRateSchemeId": 102,
        "modelRegionCode": "FLWS",
        "modelVersion": "11.0",
        "perilCode": "WS",
        "regionCode": "NA",
        "simulationPeriods": 0,
        "simulationSetId": 0,
    }]


@pytest.mark.parametrize(
    ("selections", "code"),
    [
        ([], "event_rate_selection_missing"),
        ([EventRateSelection(WS_PARTITION, 999)],
         "event_rate_selection_not_offered"),
        ([EventRateSelection(WS_PARTITION, 101), EventRateSelection(WS_PARTITION, 102)],
         "event_rate_selection_duplicate"),
        ([EventRateSelection(GroupingPartitionKey("EQ", "US", "23.0"), 101)],
         "event_rate_selection_unknown_partition"),
    ],
)
def test_invalid_event_rate_selection_blocks_post(selections, code):
    """Return stable selection problems and perform no Platform POST."""
    manager, client, _ = make_manager(*pure_elt_fixtures(conflicting=True))
    inspection = manager.inspect(analysis_ids=[1, 2])

    with pytest.raises(IRPGroupingValidationError) as raised:
        submit(manager, inspection, event_rate_selections=selections)

    assert code in {problem.code for problem in raised.value.problems}
    assert client.calls == []


def test_fingerprint_ignores_timestamp_and_display_label():
    """Exclude inspection time and reference-data labels from the fingerprint."""
    details, regions = pure_elt_fixtures(conflicting=True)
    manager, _, reference_data = make_manager(details, regions)
    first = manager.inspect(analysis_ids=[1, 2])
    reference_data.event_rate_schemes[0]["eventRateSchemeName"] = "Renamed label"

    second = manager.inspect(analysis_ids=[1, 2])

    assert first.inspected_at != second.inspected_at
    assert first.partitions != second.partitions
    assert first.fingerprint == second.fingerprint


def test_event_rate_option_ids_change_the_fingerprint():
    """Include offered event-rate scheme IDs in the fingerprint."""
    manager, _, reference_data = make_manager(*pure_elt_fixtures(conflicting=True))
    first = manager.inspect(analysis_ids=[1, 2])
    reference_data.event_rate_schemes.append({
        "eventRateSchemeId": 103,
        "eventRateSchemeName": "New applicable scheme",
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "11.0",
    })

    second = manager.inspect(analysis_ids=[1, 2])

    assert first.fingerprint != second.fingerprint


def test_changed_scheme_rejects_submission_before_post():
    """Detect a request-affecting reinspection change."""
    details, regions = pure_elt_fixtures(conflicting=True)
    manager, client, _ = make_manager(details, regions)
    inspection = manager.inspect(analysis_ids=[1, 2])
    details[2]["eventRateSchemeId"] = 103
    regions[2][0]["rateSchemeId"] = 103

    with pytest.raises(IRPGroupingValidationError) as raised:
        submit(manager, inspection)

    assert [problem.code for problem in raised.value.problems] == ["inspection_changed"]
    assert client.calls == []


def mixed_fixtures():
    """Return one ELT and one PLT member in the same partition."""
    details = {
        1: analysis(1, scheme_id=101),
        2: analysis(2, framework="PLT", engine_type="HD", scheme_id=None),
    }
    regions = {
        1: [region(1, scheme_id=101)],
        2: [region(2, framework="PLT", scheme_id=None, pet_id=50, periods=100000)],
    }
    return details, regions


def jp_plt_fixtures(*, nested: bool = False):
    """Return JP Typhoon and Non-Typhoon PLT members for model 2.1."""
    details = {
        1: analysis(
            1,
            framework="PLT",
            engine_type="HD",
            scheme_id=None,
            is_group=nested,
        ),
        2: analysis(2, framework="PLT", engine_type="HD", scheme_id=None),
    }
    for detail in details.values():
        detail.update({
            "engineVersion": "HDv2.1",
            "perilCode": "WS",
            "regionCode": "JP",
        })
    regions = {
        1: [region(1, framework="PLT", scheme_id=None, pet_id=15, periods=100000)],
        2: [region(2, framework="PLT", scheme_id=None, pet_id=16, periods=100000)],
    }
    for rows in regions.values():
        for row in rows:
            row.update({
                "engineVersion": "HDv2.1",
                "peril": "WS",
                "region": "JP",
                "subRegion": "JP",
            })
    return details, regions


def test_jp_typhoon_and_non_typhoon_pets_submit_for_model_2_1():
    """Resolve duplicated PET IDs by version and submit both JPWS rows."""
    manager, _, reference_data = make_manager(*jp_plt_fixtures(), post=True)

    inspection = manager.inspect(analysis_ids=[1, 2])
    result = submit(manager, inspection)

    assert inspection.blocking_problems == ()
    assert inspection.partitions[0].observed_pet_ids == (15, 16)
    rows = result.request_body["settings"]["regionPerilSimulationSet"]
    assert [(row["modelRegionCode"], row["modelVersion"], row["simulationSetId"])
            for row in rows] == [("JPWS", "2.1", 15), ("JPWS", "2.1", 16)]
    assert {
        (call["pet_id"], call["model_version"], call["model_region_code"])
        for call in reference_data.pet_metadata_calls
    } == {(15, "2.1", "JPWS"), (16, "2.1", "JPWS")}


@pytest.mark.parametrize(
    "pet_error",
    [IRPAPIError("No PET metadata found"), IRPAPIError("Multiple PET metadata rows found")],
)
def test_unavailable_exact_pet_metadata_does_not_warn_or_block(pet_error):
    """Use analysis and software-version data when qualified PET lookup fails."""
    manager, _, reference_data = make_manager(*jp_plt_fixtures())
    reference_data.pet_metadata_error = pet_error

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.blocking_problems == ()
    assert inspection.warnings == ()
    assert [fact.pet_name for member in inspection.members
            for fact in member.regions] == [None, None]


def test_plt_region_facts_name_the_pet():
    """Carry the PETMetadata petName for the version the PET resolved against."""
    manager, _, _ = make_manager(*jp_plt_fixtures())

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert [(fact.pet_id, fact.pet_name) for member in inspection.members
            for fact in member.regions] == [
        (15, TYPHOON_PET_NAME), (16, NON_TYPHOON_PET_NAME)]


def test_mixed_elt_plt_uses_exact_simulation_mapping_and_pet():
    """Build a group PLT without replacing the selected scheme or member PET."""
    manager, client, _ = make_manager(*mixed_fixtures(), post=True)
    inspection = manager.inspect(analysis_ids=[1, 2])

    result = submit(
        manager, inspection, simulation_set_selections=MIXED_SIMULATION_SET
    )

    assert inspection.output_loss_table == "PLT"
    rps = result.request_body["settings"]["regionPerilSimulationSet"]
    assert {entry["simulationSetId"] for entry in rps} == {50, 1001}
    assert {entry["eventRateSchemeId"] for entry in rps} == {0, 101}
    assert client.calls[-1]["json"] == result.request_body


def test_elt_partition_row_joins_every_member_engine_version():
    """Write every member engine version, comma-joined, on an ELT partition's row."""
    details, regions = pure_elt_fixtures(conflicting=True)
    details[2]["engineVersion"] = "RL25"
    regions[2][0]["engineVersion"] = "RL25"
    manager, _, _ = make_manager(details, regions, post=True)
    inspection = manager.inspect(analysis_ids=[1, 2])

    result = submit(
        manager, inspection,
        event_rate_selections=[EventRateSelection(WS_PARTITION, 101)],
    )

    rows = result.request_body["settings"]["regionPerilSimulationSet"]
    assert [row["engineVersion"] for row in rows] == ["RL23,RL25"]


def test_imported_simulation_sets_are_not_offered():
    """Offer SYSTEM simulation sets only; an IMPORTED PEQT is not a Risk Modeler choice."""
    manager, _, reference_data = make_manager(*mixed_fixtures())
    reference_data.event_rate_schemes.append({
        "eventRateSchemeId": 911,
        "eventRateSchemeName": "Windstorm Stochastic",
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "11.0",
    })
    reference_data.simulation_sets = [
        {
            "id": 8001,
            "eventRateSchemeId": 911,
            "name": "System windstorm simulation",
            "perilCode": "WS",
            "modelRegionCode": "NAWS",
            "modelVersionCode": "11.0",
            "defaultPeriods": 100000,
            "peqtSource": "SYSTEM",
        },
        {
            "id": 8002,
            "eventRateSchemeId": 911,
            "name": "Imported windstorm simulation",
            "perilCode": "WS",
            "modelRegionCode": "NAWS",
            "modelVersionCode": "11.0",
            "defaultPeriods": 100000,
            "peqtSource": "IMPORTED",
        },
    ]

    inspection = manager.inspect(analysis_ids=[1, 2])

    partition = next(p for p in inspection.partitions if p.key == WS_PARTITION)
    assert [
        option.simulation_set_id for option in partition.simulation_set_options
    ] == [8001]


def test_differing_pet_ids_in_one_partition_submit_every_pet():
    """Submit each distinct source PET and leave compatibility to Platform."""
    details = {
        1: analysis(1, framework="PLT", engine_type="HD", scheme_id=None),
        2: analysis(2, framework="PLT", engine_type="HD", scheme_id=None),
    }
    regions = {
        1: [region(1, framework="PLT", scheme_id=None, pet_id=50, periods=100000)],
        2: [region(2, framework="PLT", scheme_id=None, pet_id=51, periods=100000)],
    }
    manager, _, _ = make_manager(details, regions, post=True)

    inspection = manager.inspect(analysis_ids=[1, 2])
    result = submit(manager, inspection)

    assert inspection.blocking_problems == ()
    assert inspection.partitions[0].observed_pet_ids == (50, 51)
    rows = result.request_body["settings"]["regionPerilSimulationSet"]
    assert [row["simulationSetId"] for row in rows] == [50, 51]


def test_different_pet_ids_across_different_partitions_submit():
    """Permit exact PET mappings that belong to different documented partitions."""
    details = {
        1: analysis(1, framework="PLT", engine_type="HD", scheme_id=None),
        2: analysis(2, framework="PLT", engine_type="HD", scheme_id=None),
    }
    second_region = region(
        2, framework="PLT", scheme_id=None, pet_id=60, periods=50000,
    )
    second_region.update({"peril": "EQ", "region": "US", "subRegion": "CA"})
    regions = {
        1: [region(1, framework="PLT", scheme_id=None, pet_id=50, periods=100000)],
        2: [second_region],
    }
    manager, _, _ = make_manager(details, regions, post=True)
    inspection = manager.inspect(analysis_ids=[1, 2])

    result = submit(manager, inspection)

    assert inspection.blocking_problems == ()
    assert {partition.observed_pet_ids for partition in inspection.partitions} == {(50,), (60,)}
    assert {entry["simulationSetId"] for entry in result.request_body["settings"]["regionPerilSimulationSet"]} == {50, 60}


def test_apply_contract_flag_blocks_plt_member():
    """Block an HD/PLT member that applies contract dates."""
    details, regions = mixed_fixtures()
    regions[2][0]["applyContractFlag"] = True
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert "apply_contract_flag_unsupported" in {p.code for p in inspection.blocking_problems}


def test_missing_plt_pet_id_blocks():
    """Require a positive PET ID on every PLT region."""
    details, regions = mixed_fixtures()
    regions[2][0]["petId"] = None
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert "pet_id_missing" in {p.code for p in inspection.blocking_problems}


def test_missing_plt_region_periods_block_even_when_pet_has_default_periods():
    """Do not replace a missing source PLT length with PET reference periods."""
    details, regions = mixed_fixtures()
    regions[2][0]["periods"] = None
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert "pet_periods_missing" in {p.code for p in inspection.blocking_problems}


def test_simulation_reference_lookup_is_reused_across_subregions():
    """Read the simulation-set list once for all matching source subregions."""
    details, regions = mixed_fixtures()
    regions[1].append(region(1, scheme_id=101, sub_region="TX"))
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.blocking_problems == ()
    assert len(inspection.simulation_mappings) == 1


def test_missing_member_and_region_data_have_stable_codes():
    """Do not drop requested members or silently accept empty region data."""
    details = {1: analysis(1), 2: {}}
    regions = {1: [], 2: []}
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.analysis_ids == (1, 2)
    assert {p.code for p in inspection.blocking_problems} == {
        "member_not_found", "member_region_data_missing"
    }


def test_nested_elt_group_uses_region_metadata_and_offers_scheme_choice():
    """Do not compare each nested group region with its summary metadata."""
    details, regions = pure_elt_fixtures(conflicting=True)
    for detail in details.values():
        detail.update({
            "engineType": "Group",
            "isGroup": True,
            "engineVersion": "RL24",
            "perilCode": "WF",
            "regionCode": "US",
        })
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.blocking_problems == ()
    assert inspection.partitions[0].key == WS_PARTITION
    assert inspection.partitions[0].event_rate_selection_required is True
    assert [
        option.event_rate_scheme_id
        for option in inspection.partitions[0].event_rate_scheme_options
    ] == [101, 102]


def test_missing_simulation_set_options_block_mixed_group():
    """Treat zero partition simulation-set options as an inspection problem."""
    manager, _, reference_data = make_manager(*mixed_fixtures())
    reference_data.simulation_sets = []

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert "simulation_set_mapping_missing" in {p.code for p in inspection.blocking_problems}


def test_multiple_simulation_sets_are_caller_options():
    """Return multiple simulation sets instead of blocking the grouping."""
    manager, _, reference_data = make_manager(*mixed_fixtures())
    reference_data.simulation_sets.append({
        "id": 1002,
        "eventRateSchemeId": 102,
        "name": "North America Windstorm Alternative",
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "11.0",
        "defaultPeriods": 50000,
    })

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.blocking_problems == ()
    partition = next(
        partition
        for partition in inspection.partitions
        if partition.key == WS_PARTITION
    )
    assert partition.simulation_set_selection_required is True
    assert [option.simulation_set_id for option in partition.simulation_set_options] == [
        1001,
        1002,
    ]


def test_simulation_set_requires_an_active_event_rate_scheme_relationship():
    """Exclude simulation sets whose eventRateSchemeId has no active scheme."""
    manager, _, reference_data = make_manager(*mixed_fixtures())
    reference_data.simulation_sets.append({
        "id": 1009,
        "eventRateSchemeId": 909,
        "name": "Climate-conditioned simulation",
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "11.0",
        "defaultPeriods": 100000,
        "peqtSource": "SYSTEM",
    })

    inspection = manager.inspect(analysis_ids=[1, 2])

    partition = next(
        partition
        for partition in inspection.partitions
        if partition.key == WS_PARTITION
    )
    assert [option.simulation_set_id for option in partition.simulation_set_options] == [
        1001
    ]


@pytest.mark.parametrize(
    ("selections", "code"),
    [
        ([], "simulation_set_selection_missing"),
        (
            [SimulationSetSelection(WS_PARTITION, 999)],
            "simulation_set_selection_not_offered",
        ),
        (
            [SimulationSetSelection(WS_PARTITION, 1001)] * 2,
            "simulation_set_selection_duplicate",
        ),
        (
            [
                SimulationSetSelection(
                    GroupingPartitionKey("EQ", "NA", "17.0"), 1001
                )
            ],
            "simulation_set_selection_unknown_partition",
        ),
    ],
)
def test_invalid_simulation_set_selection_blocks_post(selections, code):
    """Return structured problems for missing, unknown, and unoffered selections."""
    manager, client, _ = make_manager(*mixed_fixtures())
    inspection = manager.inspect(analysis_ids=[1, 2])

    with pytest.raises(IRPGroupingValidationError) as raised:
        submit(manager, inspection, simulation_set_selections=selections)

    assert code in {problem.code for problem in raised.value.problems}
    assert client.calls == []


def _rows_by_set(result):
    return {
        row["simulationSetId"]: row
        for row in result.request_body["settings"]["regionPerilSimulationSet"]
    }


def test_simulation_periods_selection_overrides_pet_and_set_periods():
    """Write the caller's simulationPeriods on PLT and converted-ELT rows alike."""
    manager, client, _ = make_manager(*mixed_fixtures(), post=True)
    inspection = manager.inspect(analysis_ids=[1, 2])
    plt_key = next(p.key for p in inspection.partitions if p.key != WS_PARTITION)

    result = submit(
        manager, inspection,
        simulation_set_selections=MIXED_SIMULATION_SET,
        simulation_periods_selections=[
            SimulationPeriodsSelection(WS_PARTITION, 50000),
            SimulationPeriodsSelection(plt_key, 25000),
        ],
    )

    rows = _rows_by_set(result)
    assert rows[1001]["simulationPeriods"] == 50000
    assert rows[50]["simulationPeriods"] == 25000
    assert client.calls[-1]["json"] == result.request_body


def test_partition_without_simulation_periods_selection_keeps_its_periods():
    """Fall back to the PET's periods and the set's defaultPeriods."""
    manager, _, _ = make_manager(*mixed_fixtures(), post=True)
    inspection = manager.inspect(analysis_ids=[1, 2])

    result = submit(
        manager, inspection,
        simulation_set_selections=MIXED_SIMULATION_SET,
        simulation_periods_selections=[SimulationPeriodsSelection(WS_PARTITION, 50000)],
    )

    rows = _rows_by_set(result)
    assert rows[1001]["simulationPeriods"] == 50000
    assert rows[50]["simulationPeriods"] == 100000


@pytest.mark.parametrize(
    ("selections", "code"),
    [
        (
            [SimulationPeriodsSelection(WS_PARTITION, 50000)] * 2,
            "simulation_periods_selection_duplicate",
        ),
        (
            [SimulationPeriodsSelection(GroupingPartitionKey("EQ", "NA", "17.0"), 50000)],
            "simulation_periods_selection_unknown_partition",
        ),
    ],
)
def test_invalid_simulation_periods_selection_blocks_post(selections, code):
    """Return structured problems for duplicate and unknown-partition selections."""
    manager, client, _ = make_manager(*mixed_fixtures())
    inspection = manager.inspect(analysis_ids=[1, 2])

    with pytest.raises(IRPGroupingValidationError) as raised:
        submit(
            manager, inspection,
            simulation_set_selections=MIXED_SIMULATION_SET,
            simulation_periods_selections=selections,
        )

    assert code in {problem.code for problem in raised.value.problems}
    assert client.calls == []


def test_simulation_periods_selection_on_an_elt_group_is_structured():
    """Reject a simulationPeriods override when the group is not simulated to PLT."""
    manager, client, _ = make_manager(*pure_elt_fixtures())
    inspection = manager.inspect(analysis_ids=[1, 2])

    with pytest.raises(IRPGroupingValidationError) as raised:
        submit(manager, inspection, simulation_periods_selections=[
            SimulationPeriodsSelection(inspection.partitions[0].key, 50000)
        ])

    assert "simulation_periods_selection_not_required" in {
        problem.code for problem in raised.value.problems
    }
    assert client.calls == []


def test_non_positive_simulation_periods_is_direct_validation_error():
    """Reject simulation_periods <= 0 before any Platform read."""
    manager, client, _ = make_manager(*mixed_fixtures())

    with pytest.raises(IRPValidationError):
        manager.submit(
            analysis_ids=[1, 2],
            settings=settings(),
            event_rate_selections=[],
            expected_inspection_fingerprint="v0:not-the-current-fingerprint",
            simulation_periods_selections=[
                SimulationPeriodsSelection(WS_PARTITION, 0)
            ],
        )

    assert client.calls == []


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("No model version mapping found", "model_version_mapping_missing"),
        ("Multiple model version mappings found", "model_version_mapping_ambiguous"),
    ],
)
def test_model_version_cardinality_problem_is_structured(message, code):
    """Distinguish missing and ambiguous model-version mappings."""
    manager, _, reference_data = make_manager(*pure_elt_fixtures())
    reference_data.model_version_error = IRPAPIError(message)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert code in {p.code for p in inspection.blocking_problems}


def test_each_dropped_region_row_names_its_own_sub_region():
    """Report one problem per sub-region rather than one for the analysis.

    ``_deduplicate_problems`` keys on ``sub_regions``, so region rows that fail
    the model-version lookup identically no longer collapse to one problem
    naming whichever sub-region happened to be last."""
    details, regions = pure_elt_fixtures()
    regions[1] = [region(1, sub_region=code) for code in ("AL", "CT", "D1")]
    manager, _, reference_data = make_manager(details, regions)
    reference_data.model_version_error = IRPAPIError("No model version mapping found")

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert [
        problem.sub_regions for problem in inspection.blocking_problems
        if problem.code == "model_version_mapping_missing"
        and problem.analysis_ids == (1,)
    ] == [("AL",), ("CT",), ("D1",)]


def test_fingerprint_ignores_irrelevant_region_response_order():
    """Normalize region rows before computing the concurrency fingerprint."""
    details, regions = pure_elt_fixtures()
    regions[1].append(region(1, scheme_id=101, sub_region="TX"))
    regions[2].append(region(2, scheme_id=101, sub_region="TX"))
    manager, _, _ = make_manager(details, regions)
    first = manager.inspect(analysis_ids=[1, 2])
    regions[1].reverse()
    regions[2].reverse()

    second = manager.inspect(analysis_ids=[1, 2])

    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    "invalid_settings",
    [
        replace(settings(), analysis_name=""),
        replace(settings(), num_of_simulations=0),
        replace(settings(), propagate_detailed_losses=1),
        replace(settings(), currency=replace(settings().currency, code="")),
    ],
)
def test_every_former_default_is_required(invalid_settings):
    """Reject absent-equivalent settings before reinspection or submission."""
    manager, client, _ = make_manager(*pure_elt_fixtures())

    with pytest.raises(IRPValidationError):
        manager.submit(
            analysis_ids=[1, 2],
            settings=invalid_settings,
            event_rate_selections=[],
            expected_inspection_fingerprint="v1:example",
        )

    assert client.calls == []


def test_empty_expected_fingerprint_is_direct_validation_error():
    """Require the concurrency token with no omission behavior."""
    manager, client, _ = make_manager(*pure_elt_fixtures())

    with pytest.raises(IRPValidationError):
        manager.submit(
            analysis_ids=[1, 2],
            settings=settings(),
            event_rate_selections=[],
            expected_inspection_fingerprint="",
        )

    assert client.calls == []


@pytest.mark.parametrize("analysis_ids", [[], [1], [1, 1], [1, 0], [1, True]])
def test_malformed_analysis_ids_raise_direct_validation_error(analysis_ids):
    """Reject malformed analysis IDs before reading Platform data."""
    manager, _, _ = make_manager({}, {})

    with pytest.raises(IRPValidationError):
        manager.inspect(analysis_ids=analysis_ids)


def test_get_job_uses_grouping_job_endpoint():
    """Expose grouping job status through GroupingManager."""
    manager, client, _ = make_manager({}, {})
    client.responses.append(FakeResponse(200, json_body={"id": 77, "status": "RUNNING"}))

    result = manager.get_job(job_id=77)

    assert result == {"id": 77, "status": "RUNNING"}
    assert client.calls[0]["path"] == GET_ANALYSIS_GROUPING_JOB.format(jobId=77)


def test_elt_region_without_a_rate_scheme_blocks():
    """Block an ELT region that carries no positive event-rate scheme ID."""
    details, regions = pure_elt_fixtures()
    details[2]["eventRateSchemeId"] = None
    regions[2][0]["rateSchemeId"] = None
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    problem = next(
        problem for problem in inspection.blocking_problems
        if problem.code == "event_rate_scheme_missing"
    )
    assert problem.analysis_ids == (2,)
    assert problem.partition == WS_PARTITION


@pytest.mark.parametrize(
    ("raw_region", "detail_overrides"),
    [
        ("not a region row", {}),
        ({**region(2), "framework": "AAL"}, {}),
        ({**region(2), "engineVersion": None}, {"engineVersion": None}),
    ],
)
def test_malformed_region_row_blocks(raw_region, detail_overrides):
    """Report a region row with no mapping, no ELT/PLT framework, or no engine version."""
    details, regions = pure_elt_fixtures()
    details[2].update(detail_overrides)
    regions[2] = [raw_region]
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert "region_row_metadata_missing" in {p.code for p in inspection.blocking_problems}


def test_region_framework_conflicting_with_the_detail_blocks():
    """Block a member whose region frameworks disagree with its analysis framework."""
    details, regions = pure_elt_fixtures()
    regions[2] = [region(2, framework="PLT", scheme_id=None, pet_id=50, periods=100000)]
    manager, _, _ = make_manager(details, regions)

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert "member_classification_conflict" in {
        p.code for p in inspection.blocking_problems
    }


def test_event_rate_selection_for_a_non_conflicting_partition_is_structured():
    """Reject an event-rate selection when the partition has one observed scheme."""
    manager, client, _ = make_manager(*pure_elt_fixtures())
    inspection = manager.inspect(analysis_ids=[1, 2])

    with pytest.raises(IRPGroupingValidationError) as raised:
        submit(
            manager, inspection,
            event_rate_selections=[EventRateSelection(WS_PARTITION, 101)],
        )

    assert [p.code for p in raised.value.problems] == ["event_rate_selection_not_required"]
    assert client.calls == []


def test_simulation_set_selection_for_a_plt_partition_is_structured():
    """Reject a simulation-set selection for a partition not converted from ELT."""
    manager, client, _ = make_manager(*mixed_fixtures())
    inspection = manager.inspect(analysis_ids=[1, 2])
    plt_key = GroupingPartitionKey("WF", "NA", "2.0")

    with pytest.raises(IRPGroupingValidationError) as raised:
        submit(manager, inspection, simulation_set_selections=[
            SimulationSetSelection(WS_PARTITION, 1001),
            SimulationSetSelection(plt_key, 50),
        ])

    assert [p.code for p in raised.value.problems] == [
        "simulation_set_selection_not_required"
    ]
    assert client.calls == []


def test_fingerprint_names_its_version():
    """Prefix the fingerprint with the version that invalidates stored fingerprints."""
    manager, _, _ = make_manager(*pure_elt_fixtures())

    inspection = manager.inspect(analysis_ids=[1, 2])

    assert inspection.fingerprint.startswith(f"v{GroupingManager.FINGERPRINT_VERSION}:")


def test_treaty_without_a_treaty_number_aborts_inspection():
    """Raise rather than compare a treaty that has no Treaty Number."""
    details, regions = pure_elt_fixtures()
    unnumbered = treaty(1, 11)
    unnumbered.pop("treatyNumber")
    manager, _, _ = make_manager(
        details, regions, treaties={1: [unnumbered], 2: [treaty(2, 22)]}
    )

    with pytest.raises(IRPAPIError):
        manager.inspect(analysis_ids=[1, 2])


def test_non_list_event_rate_scheme_response_raises_api_error():
    """Raise when the event-rate scheme response carries no list under items."""
    manager, _, reference_data = make_manager(*pure_elt_fixtures())
    reference_data.event_rate_schemes = {"unexpected": "shape"}

    with pytest.raises(IRPAPIError):
        manager.inspect(analysis_ids=[1, 2])


def test_get_job_rejects_a_non_positive_job_id():
    """Reject job_id <= 0 before any Platform read."""
    manager, client, _ = make_manager({}, {})

    with pytest.raises(IRPValidationError):
        manager.get_job(job_id=0)

    assert client.calls == []


def test_get_job_failure_raises_api_error():
    """Re-raise a failed grouping job read as IRPAPIError."""
    manager, client, _ = make_manager({}, {})
    client.responses.append(RuntimeError("boom"))

    with pytest.raises(IRPAPIError):
        manager.get_job(job_id=77)


def test_grouping_post_failure_raises_api_error():
    """Re-raise a failed grouping POST as IRPAPIError."""
    manager, client, _ = make_manager(*pure_elt_fixtures())
    inspection = manager.inspect(analysis_ids=[1, 2])
    client.responses.append(RuntimeError("boom"))

    with pytest.raises(IRPAPIError):
        submit(manager, inspection)


def test_non_numeric_job_location_raises_api_error():
    """Raise IRPAPIError, not ValueError, for a location header ending in a non-numeric ID."""
    manager, client, _ = make_manager(*pure_elt_fixtures())
    inspection = manager.inspect(analysis_ids=[1, 2])
    client.responses.append(FakeResponse(
        201, headers={"location": "/platform/grouping/v1/jobs/abc"}, has_body=False,
    ))

    with pytest.raises(IRPAPIError):
        submit(manager, inspection)
