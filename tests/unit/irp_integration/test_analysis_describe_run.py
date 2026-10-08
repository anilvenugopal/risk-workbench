"""Offline contract tests for ``AnalysisManager.describe_run``.

The analysis details, region rows, treaties and reference rows below are
trimmed to the fields ``describe_run`` reads and keep the shapes the Platform
returns. Reference values the responses did not cover are invented, and each is
marked where it appears: the model versions for ``RL25``/``RL23`` North
Atlantic windstorm and ``RL25`` North America earthquake, and the peril, model
region and model version of event-rate schemes 739 and 740.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from conftest import FakeClient, FakeResponse
from irp_integration.analysis import AnalysisManager
from irp_integration.exceptions import IRPAPIError

# Invented names and identifiers. Nothing here may name a real EDM, RDM,
# portfolio, analysis, treaty or tenant: this file ships in the sdist, so a name
# used here is a name published to PyPI.

# Analysis 101, an RL25 DLM ELT analysis of one own portfolio.
OWN_DLM_DETAIL = {
    "analysisId": 101,
    "analysisName": "example_ws_analysis",
    "exposureName": "example_edm",
    "engineType": "DLM",
    "engineVersion": "RL25",
    "analysisFramework": "ELT",
    "analysisType": "Exceedance Probability",
    "analysisMode": "Distributed",
    "isGroup": False,
    "groupType": "ANLS",
    "peril": "Windstorm",
    "perilCode": "WS",
    "subPeril": "Surge + Wind",
    "region": "North Atlantic (including Hawaii)",
    "regionCode": "NA",
    "eventRateSchemeNames": [
        {"id": 0, "code": "0", "name": "RMS 2025 Stochastic Event Rates"}
    ],
    "simulationSetId": 0,
    "simulationPeriods": 0,
    "modelProfile": {"id": 5250, "code": "", "name": "example_ws_model_profile"},
    "additionalProperties": [
        {"key": "exposure", "properties": [
            {"id": 5, "name": "example_ws_portfolio", "value": "example_edm"}
        ]},
        {"key": "dataVersion", "properties": [
            {"id": 0, "name": "", "value": "22.0.0"}
        ]},
        {"key": "eventRateSchemeId", "properties": [
            {"id": 739, "name": "", "value": ""}
        ]},
    ],
}

OWN_DLM_REGION_ROW = {
    "region": "NA",
    "subRegion": "P1",
    "peril": "Windstorm",
    "eventRateSchemeId": 739,
    "framework": "ELT",
    "analysisId": 101,
    "modelProfileId": 5250,
    "petId": 0,
    "numSamples": 0,
    "periods": 0,
    "applyContractFlag": False,
    "engineVersion": "RL25",
}

# Both North Atlantic windstorm captures return 23 region rows identical apart
# from subRegion. The capture keeps one row and names the first six codes; the
# remaining codes are invented to reach the live row count of 23.
NA_WS_SUB_REGIONS = (
    "AL", "CT", "D1", "DC", "FL", "GA", "P1", "HI", "LA", "MA", "MD", "ME",
    "MS", "NC", "NH", "NJ", "NY", "PA", "RI", "SC", "TX", "VA", "D2",
)

OWN_DLM_TREATIES = [
    {
        "treatyId": 21,
        "treatyNumber": "example_treaty_1",
        "treatyName": "Example Working Layer",
        "treatyType": "WORK",
        "currency": {"id": 0, "code": "USD", "name": "US Dollar"},
        "attachmentBasis": "L",
        "attachmentLevel": "POL",
        "occurrenceLimit": 1000000.0,
        "attachmentPoint": 250000.0,
        "retentionAmount": 0.0,
        "riskLimit": 250000.0,
        "percentageRiShare": 100.0,
        "percentagePlaced": 100.0,
        "effectiveDate": "2026-01-01T00:00:00.000Z",
        "expirationDate": "2026-12-31T00:00:00.000Z",
        "isValid": True,
        "analysisId": 101,
        "uri": "/platform/riskdata/v1/analyses/101/treaties/21",
    },
]

# Analysis 102, an HDv3.0 PLT analysis of one own portfolio.
OWN_HD_DETAIL = {
    "analysisId": 102,
    "analysisName": "example_eq_analysis",
    "exposureName": "example_edm",
    "engineType": "HD",
    "engineVersion": "HDv3.0",
    "analysisFramework": "PLT",
    "analysisType": "Exceedance Probability",
    "analysisMode": "Simulated",
    "isGroup": False,
    "peril": "Earthquake",
    "perilCode": "EQ",
    "subPeril": "Multi-SubPeril",
    "region": "New Zealand",
    "regionCode": "NZ",
    "eventRateSchemeNames": [],
    "simulationSetId": 0,
    "simulationPeriods": 0,
    "modelProfile": {"id": 5328, "code": "", "name": "example_eq_model_profile"},
    "additionalProperties": [
        {"key": "dataVersion", "properties": [
            {"id": 0, "name": "", "value": "22.0.0"}
        ]},
        {"key": "exposure", "properties": [
            {"id": 1, "name": "example_eq_portfolio", "value": "example_edm"}
        ]},
    ],
}

OWN_HD_REGION_ROW = {
    "region": "NZ",
    "subRegion": "NZ",
    "peril": "Earthquake",
    "eventRateSchemeId": 0,
    "framework": "PLT",
    "analysisId": 102,
    "modelProfileId": 5328,
    "petId": 12,
    "numSamples": 1,
    "periods": 1978459,
    "applyContractFlag": False,
    "engineVersion": "HDv3.0",
}

# Analysis 103, an RL23 DLM ELT analysis imported from a broker RDM.
BROKER_DLM_DETAIL = {
    "analysisId": 103,
    "analysisName": "example_broker_analysis",
    "sourceRdmName": "example_rdm",
    "exposureName": "",
    "engineType": "DLM",
    "engineVersion": "RL23",
    "analysisFramework": "ELT",
    "analysisType": "Exceedance Probability",
    "analysisMode": "Distributed",
    "isGroup": False,
    "groupType": "ANLS",
    "peril": "Windstorm",
    "perilCode": "WS",
    "subPeril": "Surge Only",
    "region": "North Atlantic (including Hawaii)",
    "regionCode": "NA",
    "eventRateSchemeNames": [],
    "simulationSetId": 0,
    "simulationPeriods": 0,
    "modelProfile": {"id": 0, "code": "", "name": ""},
    "additionalProperties": [
        {"key": "exposure", "properties": [
            {"id": 3, "name": "", "value": "example_broker_edm"}
        ]},
        {"key": "dataVersion", "properties": [{"id": 0, "name": "", "value": ""}]},
    ],
}

BROKER_DLM_REGION_ROW = {
    "region": "NA",
    "subRegion": "HI",
    "peril": "Windstorm",
    "eventRateSchemeId": 577,
    "framework": "ELT",
    "analysisId": 103,
    "modelProfileId": 0,
    "petId": 0,
    "numSamples": 0,
    "periods": 0,
    "applyContractFlag": False,
    "engineVersion": "RL23",
}

BROKER_DLM_TREATIES = [
    {
        "treatyId": 22,
        "treatyNumber": "example_treaty_2",
        "treatyName": "Example Excess Layer 100",
        "treatyType": "WORK",
        "currency": {"id": 0, "code": "USD", "name": "US Dollar"},
        "attachmentBasis": "L",
        "attachmentLevel": "POL",
        "occurrenceLimit": 10000000.0,
        "attachmentPoint": 5000000.0,
        "retentionAmount": 0.0,
        "riskLimit": 5000000.0,
        "percentageRiShare": 100.0,
        "percentagePlaced": 100.0,
        "effectiveDate": "2000-01-01T00:00:00.000Z",
        "expirationDate": "2030-12-31T00:00:00.000Z",
        "isValid": True,
        "analysisId": 103,
        "uri": "/platform/riskdata/v1/analyses/103/treaties/22",
    },
    {
        "treatyId": 23,
        "treatyNumber": "example_treaty_3",
        "treatyName": "Example Excess Layer 95",
        "treatyType": "WORK",
        "currency": {"id": 0, "code": "USD", "name": "US Dollar"},
        "attachmentBasis": "L",
        "attachmentLevel": "POL",
        "occurrenceLimit": 9000000.0,
        "attachmentPoint": 2000000.0,
        "retentionAmount": 0.0,
        "riskLimit": 3000000.0,
        "percentageRiShare": 100.0,
        "percentagePlaced": 95.0,
        "effectiveDate": "2000-01-01T00:00:00.000Z",
        "expirationDate": "2030-12-31T00:00:00.000Z",
        "isValid": True,
        "analysisId": 103,
        "uri": "/platform/riskdata/v1/analyses/103/treaties/23",
    },
]

# Analysis 104, a broker group: isGroup is false and groupType is INGP, and
# engineType is Group.
BROKER_GROUP_DETAIL = {
    "analysisId": 104,
    "analysisName": "example_broker_group",
    "sourceRdmName": "example_rdm",
    "engineType": "Group",
    "engineVersion": "RL25",
    "analysisFramework": "ELT",
    "isGroup": False,
    "groupType": "INGP",
    "peril": "Windstorm",
    "perilCode": "WS",
    "region": "North Atlantic (including Hawaii)",
    "regionCode": "NA",
    "eventRateSchemeNames": [],
    "additionalProperties": [
        {"key": "propagateDetailedOutput", "properties": [
            {"id": 0, "name": "", "value": "Yes"}
        ]},
        {"key": "eventRateSchemes", "properties": [
            {"id": 0, "name": "", "value": {
                "regionCode": "NA",
                "perilCode": "WS",
                "framework": "ELT",
                "eventRateSchemeId": 578,
                "eventRateSchemeName": "RMS 2023 Stochastic Event Rates",
                "simulationSetId": 0,
                "simulationSetName": "",
                "simulationPeriods": 0,
            }}
        ]},
    ],
}

BROKER_GROUP_REGION_ROW = dict(
    BROKER_DLM_REGION_ROW,
    subRegion="DC",
    eventRateSchemeId=578,
    analysisId=104,
    engineVersion="RL25",
)

# Analysis 105, a multi-peril group. The detail carries perilCode YY and peril
# "Multi-Peril" while its region rows carry "Earthquake" and "Windstorm". The
# Peril reference table lists YY as RMS ALL PERILS with isActive false, so YY is
# never in SoftwareModelVersionMap and neither the row nor the detail states the
# row's peril code: it comes from the row's eventRateSchemeId or petId.
MULTI_PERIL_GROUP_DETAIL = {
    "analysisId": 105,
    "analysisName": "example_multi_peril_group",
    "engineType": "Group",
    "engineVersion": "RL25",
    "analysisFramework": "ELT",
    "analysisType": "Exceedance Probability",
    "isGroup": False,
    "groupType": "CDGP",
    "peril": "Multi-Peril",
    "perilCode": "YY",
    "region": "North Atlantic (including Hawaii)",
    "regionCode": "NA",
    "eventRateSchemeNames": [],
    "simulationSetId": 0,
    "simulationPeriods": 0,
    "additionalProperties": [
        {"key": "propagateDetailedOutput", "properties": [
            {"id": 0, "name": "", "value": "Yes"}
        ]},
    ],
}

MULTI_PERIL_GROUP_REGION_ROWS = [
    {
        "region": "NA",
        "subRegion": "FL",
        "peril": "Windstorm",
        "eventRateSchemeId": 739,
        "framework": "ELT",
        "analysisId": 105,
        "modelProfileId": 5250,
        "petId": 0,
        "numSamples": 0,
        "periods": 0,
        "applyContractFlag": False,
        "engineVersion": "RL25",
    },
    {
        "region": "NA",
        "subRegion": "CA",
        "peril": "Earthquake",
        "eventRateSchemeId": 740,
        "framework": "ELT",
        "analysisId": 105,
        "modelProfileId": 5251,
        "petId": 0,
        "numSamples": 0,
        "periods": 0,
        "applyContractFlag": False,
        "engineVersion": "RL25",
    },
]

# One PLT row of the same group: eventRateSchemeId is 0, so the row's peril code
# comes from PET ID 12 rather than from an event-rate scheme.
MULTI_PERIL_PLT_REGION_ROW = {
    "region": "NZ",
    "subRegion": "NZ",
    "peril": "Earthquake",
    "eventRateSchemeId": 0,
    "framework": "PLT",
    "analysisId": 105,
    "modelProfileId": 5328,
    "petId": 12,
    "numSamples": 1,
    "periods": 1978459,
    "applyContractFlag": False,
    "engineVersion": "HDv3.0",
}

# Analysis 106, an HDv2.0 PLT wildfire analysis. The PETMetadata row for PET 50
# carries modelRegionCode NAWF and perilCode FR, and SoftwareModelVersionMap
# keys on modelRegionCode: NA followed by FR matches no mapping, NA followed by
# WF matches the HDv2.0 row.
WILDFIRE_DETAIL = {
    "analysisId": 106,
    "analysisName": "example_wf_analysis",
    "exposureName": "example_edm",
    "engineType": "HD",
    "engineVersion": "HDv2.0",
    "analysisFramework": "PLT",
    "analysisType": "Exceedance Probability",
    "analysisMode": "Simulated",
    "isGroup": False,
    "peril": "Wildfire",
    "perilCode": "WF",
    "region": "North America",
    "regionCode": "NA",
    "eventRateSchemeNames": [],
    "simulationSetId": 0,
    "simulationPeriods": 0,
    "modelProfile": {"id": 4526, "code": "", "name": "example_wf_model_profile"},
}

WILDFIRE_REGION_ROW = {
    "region": "NA",
    "subRegion": "N2",
    "peril": "Wildfire",
    "eventRateSchemeId": 0,
    "framework": "PLT",
    "analysisId": 106,
    "modelProfileId": 4526,
    "petId": 50,
    "numSamples": 1,
    "periods": 100000,
    "applyContractFlag": False,
    "engineVersion": "HDv2.0",
}

# Three of the 151 active event-rate scheme rows, plus schemes 739 and 740. The
# name of 739 is the one analysis 101 reports in eventRateSchemeNames; the
# peril, model region and model version of 739 and 740 are invented.
EVENT_RATE_SCHEME_ROWS = [
    {
        "eventRateSchemeId": 139,
        "perilCode": "EQ",
        "modelRegionCode": "NZEQ",
        "modelVersionCode": "3.0",
        "eventRateSchemeName": "RMS 2020 Time-Independent Rates",
        "isActive": True,
    },
    {
        "eventRateSchemeId": 175,
        "perilCode": "EQ",
        "modelRegionCode": "JPEQ",
        "modelVersionCode": "2.1",
        "eventRateSchemeName": "RMS V2.0 Time-Dependent Rates",
        "isActive": True,
    },
    {
        "eventRateSchemeId": 138,
        "perilCode": "EQ",
        "modelRegionCode": "NZEQ",
        "modelVersionCode": "3.0",
        "eventRateSchemeName": "RMS 2020 Time-Dependent Rates",
        "isActive": True,
    },
    {
        "eventRateSchemeId": 739,
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "11.0",
        "eventRateSchemeName": "RMS 2025 Stochastic Event Rates",
        "isActive": True,
    },
    {
        "eventRateSchemeId": 740,
        "perilCode": "EQ",
        "modelRegionCode": "NAEQ",
        "modelVersionCode": "17.0",
        "eventRateSchemeName": "RMS 2025 Time-Independent Rates",
        "isActive": True,
    },
]

# PET ID 12 exists for model version 2.0 and 3.0 with a different petName. Both
# rows carry modelRegionCode NZEQ, so the PET ID resolves a model region even
# though it does not resolve a single row.
PET_METADATA_ROWS = [
    {
        "id": 12,
        "petName": "RMS 2020 Time-Dependent Rates",
        "perilCode": "EQ",
        "modelRegionCode": "NZEQ",
        "modelVersionCode": "3.0",
        "numberOfPeriods": 1978459,
        "maxNumberOfSamples": 256,
    },
    {
        "id": 12,
        "petName": "RMS V2.0 Time-Dependent Rates",
        "perilCode": "EQ",
        "modelRegionCode": "NZEQ",
        "modelVersionCode": "2.0",
        "numberOfPeriods": 1978459,
        "maxNumberOfSamples": 256,
    },
    {
        "id": 50,
        "petName": "RMS V2.0 Stochastic Rates - CatLoss US (Default)",
        "perilCode": "FR",
        "modelRegionCode": "NAWF",
        "modelVersionCode": "2.0",
        "numberOfPeriods": 100000,
        "maxNumberOfSamples": 512,
    },
]

# HDv3.0/NZ/EQ and HDv2.0/NAWF are captured. The three RL model versions are
# invented: the capture did not read a North Atlantic windstorm or North America
# earthquake mapping. Keying on ``(engine, region, peril)`` here mirrors
# ``get_model_version_by_engine_region_peril``, which matches
# ``region_code + peril_code`` against ``modelRegionCode``.
MODEL_VERSIONS = {
    ("HDv3.0", "NZ", "EQ"): "3.0",
    ("RL25", "NA", "WS"): "11.0",
    ("RL23", "NA", "WS"): "11.0",
    ("RL25", "NA", "EQ"): "17.0",
    ("HDv2.0", "NA", "WF"): "2.0",
}


class FakeReferenceDataManager:
    """Answer the reference reads ``describe_run`` makes from capture rows."""

    def __init__(self) -> None:
        self.event_rate_schemes = list(EVENT_RATE_SCHEME_ROWS)
        self.pet_metadata = list(PET_METADATA_ROWS)
        self.pet_metadata_calls: List[Dict[str, Any]] = []
        self.pet_metadata_returns_none = False
        self.all_pet_metadata_calls = 0
        self.all_pet_metadata_error: Optional[IRPAPIError] = None

    def get_event_rate_schemes(self) -> Dict[str, Any]:
        """Return the active scheme rows in the Platform's envelope."""
        return {
            "items": list(self.event_rate_schemes),
            "totalCount": len(self.event_rate_schemes),
        }

    def get_model_version_by_engine_region_peril(
        self, engine_version: str, region_code: str, peril_code: str
    ) -> str:
        """Return the fixture model version for one engine, region and peril."""
        key = (engine_version, region_code, peril_code)
        if key not in MODEL_VERSIONS:
            raise IRPAPIError(f"No model version mapping found for {key}")
        return MODEL_VERSIONS[key]

    def get_all_pet_metadata(self) -> List[Dict[str, Any]]:
        """Return every PET metadata row, or raise the configured read failure."""
        self.all_pet_metadata_calls += 1
        if self.all_pet_metadata_error is not None:
            raise self.all_pet_metadata_error
        return list(self.pet_metadata)

    def get_pet_metadata_exact(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Return the one PET row matching every qualifier."""
        self.pet_metadata_calls.append(kwargs)
        if self.pet_metadata_returns_none:
            return None
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
            raise IRPAPIError(
                f"No PET metadata found for PET ID {kwargs['pet_id']}"
            )
        if len(matches) > 1:
            raise IRPAPIError(
                f"Multiple PET metadata rows found for PET ID {kwargs['pet_id']}"
            )
        return matches[0]

    def get_all_simulation_sets(self) -> List[Dict[str, Any]]:
        """Fail the test: a PET ID does not name a ``SimulationSet`` row."""
        raise AssertionError(
            "describe_run must not read the SimulationSet reference table"
        )


def make_manager(detail, regions, treaties=()):
    """Build an analysis manager over one queued detail, region and treaty page."""
    client = FakeClient([
        FakeResponse(200, detail),
        FakeResponse(200, list(regions)),
        FakeResponse(200, list(treaties)),
    ])
    reference_data = FakeReferenceDataManager()
    irp = SimpleNamespace(client=client, reference_data=reference_data)
    return AnalysisManager(irp), reference_data


def rows(row, sub_regions=NA_WS_SUB_REGIONS):
    """Return one region row per sub-region code."""
    return [dict(row, subRegion=code) for code in sub_regions]


def problem_message(description):
    """Return the message of the one problem a description reports."""
    assert len(description.problems) == 1
    return description.problems[0].message


def test_own_dlm_reports_every_region_row_and_names_its_scheme():
    """Keep all 23 windstorm region rows and name event-rate scheme 739."""
    manager, _ = make_manager(OWN_DLM_DETAIL, rows(OWN_DLM_REGION_ROW))

    description = manager.describe_run(101)

    assert description.analysis_id == 101
    assert description.is_group is False
    assert len(description.regions) == 23
    assert description.problems == ()
    assert {region.event_rate_scheme_id for region in description.regions} == {739}
    assert {region.framework for region in description.regions} == {"ELT"}
    assert {region.peril_code for region in description.regions} == {"WS"}
    assert {region.region_code for region in description.regions} == {"NA"}
    assert {region.model_version for region in description.regions} == {"11.0"}
    assert [region.sub_region for region in description.regions] == list(NA_WS_SUB_REGIONS)
    assert description.event_rate_scheme_names == {
        739: "RMS 2025 Stochastic Event Rates"
    }


def test_own_dlm_reports_its_treaty_with_the_name_grouping_drops():
    """Return treatyName alongside the treaty ID, number and terms."""
    manager, _ = make_manager(
        OWN_DLM_DETAIL, rows(OWN_DLM_REGION_ROW), OWN_DLM_TREATIES
    )

    treaties = manager.describe_run(101).treaties

    assert len(treaties) == 1
    assert treaties[0].treaty_id == 21
    assert treaties[0].treaty_number == "example_treaty_1"
    assert treaties[0].treaty_name == "Example Working Layer"
    assert treaties[0].terms["occurrenceLimit"] == 1000000.0


def test_own_hd_names_the_pet_row_for_the_resolved_model_version():
    """Name PET 12 from the 3.0 row, not the 2.0 row with the same ID."""
    manager, reference_data = make_manager(OWN_HD_DETAIL, [OWN_HD_REGION_ROW])

    description = manager.describe_run(102)

    assert len(description.regions) == 1
    region = description.regions[0]
    assert region.framework == "PLT"
    assert region.model_version == "3.0"
    assert region.pet_id == 12
    assert region.periods == 1978459
    assert region.pet_name == "RMS 2020 Time-Dependent Rates"
    assert reference_data.pet_metadata_calls == [
        {"pet_id": 12, "model_version": "3.0", "model_region_code": "NZEQ"}
    ]
    assert description.event_rate_scheme_names == {}


@pytest.mark.parametrize("unnamed", ["no qualified row", "no row returned"])
def test_unnamed_pet_id_keeps_the_id_and_periods(unnamed):
    """Report pet_name None when no PETMetadata row names the PET ID."""
    pet_id = 99999 if unnamed == "no qualified row" else 12
    manager, reference_data = make_manager(
        OWN_HD_DETAIL, [dict(OWN_HD_REGION_ROW, petId=pet_id)]
    )
    reference_data.pet_metadata_returns_none = unnamed == "no row returned"

    region = manager.describe_run(102).regions[0]

    assert region.pet_name is None
    assert region.pet_id == pet_id
    assert region.periods == 1978459


def test_broker_dlm_reports_both_treaty_names_and_terms():
    """Return the broker analysis's two treaties with names and normalized terms."""
    manager, _ = make_manager(
        BROKER_DLM_DETAIL, rows(BROKER_DLM_REGION_ROW), BROKER_DLM_TREATIES
    )

    description = manager.describe_run(103)

    assert [treaty.treaty_name for treaty in description.treaties] == [
        "Example Excess Layer 100", "Example Excess Layer 95"
    ]
    assert [treaty.treaty_id for treaty in description.treaties] == [22, 23]
    assert [treaty.treaty_number for treaty in description.treaties] == [
        "example_treaty_2", "example_treaty_3"
    ]
    placed = description.treaties[1].terms
    assert placed["currency"] == "USD"
    assert placed["percentagePlaced"] == 95.0
    assert placed["occurrenceLimit"] == 9000000.0
    assert placed["lobs"] == []
    assert placed["lossOccurrences"] == []


def test_broker_dlm_omits_an_event_rate_scheme_no_active_row_names():
    """Keep scheme 577 on every region row and leave it out of the names."""
    manager, _ = make_manager(BROKER_DLM_DETAIL, rows(BROKER_DLM_REGION_ROW))

    description = manager.describe_run(103)

    assert {region.event_rate_scheme_id for region in description.regions} == {577}
    assert description.event_rate_scheme_names == {}


def test_broker_group_type_reports_a_group():
    """Read groupType INGP as a group even though the detail reports isGroup false."""
    manager, _ = make_manager(BROKER_GROUP_DETAIL, [BROKER_GROUP_REGION_ROW])

    assert manager.describe_run(104).is_group is True


def test_cep_group_engine_type_reports_a_group():
    """Read engineType CEPGroup as a group: GetAnalysisResponse lists it beside Group."""
    detail = dict(BROKER_GROUP_DETAIL, engineType="CEPGroup", groupType="UNRECOGNIZED")
    manager, _ = make_manager(detail, [BROKER_GROUP_REGION_ROW])

    assert manager.describe_run(104).is_group is True


def test_is_group_flag_reports_a_group():
    """Read isGroup true as a group."""
    detail = dict(OWN_DLM_DETAIL, isGroup=True)
    manager, _ = make_manager(detail, rows(OWN_DLM_REGION_ROW))

    assert manager.describe_run(101).is_group is True


def test_missing_analysis_raises():
    """Raise when the analysis read fails rather than describing an empty run."""
    client = FakeClient([IRPAPIError("404 Client Error: Not Found")])
    irp = SimpleNamespace(client=client, reference_data=FakeReferenceDataManager())

    with pytest.raises(IRPAPIError, match="101"):
        AnalysisManager(irp).describe_run(101)


def test_empty_region_list_describes_no_regions():
    """Describe an analysis with no region rows without raising."""
    manager, _ = make_manager(OWN_DLM_DETAIL, [], OWN_DLM_TREATIES)

    description = manager.describe_run(101)

    assert description.regions == ()
    assert description.problems == ()
    assert description.event_rate_scheme_names == {}
    assert len(description.treaties) == 1


def test_multi_peril_group_resolves_each_row_peril_from_its_scheme():
    """Keep both rows of a perilCode YY group: WS and EQ come from the scheme IDs."""
    manager, _ = make_manager(MULTI_PERIL_GROUP_DETAIL, MULTI_PERIL_GROUP_REGION_ROWS)

    description = manager.describe_run(105)

    assert description.is_group is True
    assert description.problems == ()
    assert [region.peril_code for region in description.regions] == ["WS", "EQ"]
    assert [region.model_version for region in description.regions] == ["11.0", "17.0"]
    assert [region.model_region_code for region in description.regions] == ["FLWS", "CAEQ"]
    assert {region.region_code for region in description.regions} == {"NA"}
    assert description.event_rate_scheme_names == {
        739: "RMS 2025 Stochastic Event Rates",
        740: "RMS 2025 Time-Independent Rates",
    }


def test_multi_peril_plt_row_resolves_its_peril_from_the_pet_id():
    """Resolve EQ from PET ID 12 on a row carrying eventRateSchemeId 0."""
    manager, _ = make_manager(MULTI_PERIL_GROUP_DETAIL, [MULTI_PERIL_PLT_REGION_ROW])

    description = manager.describe_run(105)

    assert description.problems == ()
    assert len(description.regions) == 1
    region = description.regions[0]
    assert region.framework == "PLT"
    assert region.peril_code == "EQ"
    assert region.region_code == "NZ"
    assert region.model_version == "3.0"
    assert region.pet_id == 12
    assert region.pet_name == "RMS 2020 Time-Dependent Rates"


def test_row_resolving_to_no_peril_code_is_dropped_and_reported():
    """Report the dropped row instead of losing it, and name every peril tried:
    the row's own "Unknown" and the detail's YY each resolve no model version."""
    unresolved = dict(
        MULTI_PERIL_GROUP_REGION_ROWS[0],
        subRegion="D1",
        peril="Unknown",
        eventRateSchemeId=0,
        petId=0,
    )
    manager, _ = make_manager(
        MULTI_PERIL_GROUP_DETAIL, MULTI_PERIL_GROUP_REGION_ROWS + [unresolved]
    )

    description = manager.describe_run(105)

    assert [region.sub_region for region in description.regions] == ["FL", "CA"]
    assert [problem.code for problem in description.problems] == [
        "model_version_mapping_missing"
    ]
    assert problem_message(description) == (
        "Model version for analysis 105, engine RL25, region NA, and peril "
        "Unknown, YY was not resolved exactly."
    )


def test_wildfire_pet_resolves_wf_and_not_the_pet_row_peril_code():
    """Read WF off modelRegionCode NAWF: PET 50's own perilCode is FR, and NA
    followed by FR matches no SoftwareModelVersionMap row."""
    manager, _ = make_manager(WILDFIRE_DETAIL, [WILDFIRE_REGION_ROW])

    description = manager.describe_run(106)

    assert description.problems == ()
    assert len(description.regions) == 1
    region = description.regions[0]
    assert region.peril_code == "WF"
    assert region.region_code == "NA"
    assert region.model_region_code == "N2WF"
    assert region.model_version == "2.0"
    assert region.pet_id == 50
    assert region.pet_name == "RMS V2.0 Stochastic Rates - CatLoss US (Default)"


def test_empty_analysis_detail_raises():
    """Raise IRPAPIError rather than reading region rows against an empty detail."""
    manager, _ = make_manager({}, [])

    with pytest.raises(IRPAPIError, match="Analysis 101 returned no analysis details"):
        manager.describe_run(101)


def test_non_list_region_response_raises():
    """Raise IRPAPIError rather than describing zero regions in silence."""
    client = FakeClient([
        FakeResponse(200, OWN_DLM_DETAIL),
        FakeResponse(200, {"items": []}),
    ])
    irp = SimpleNamespace(client=client, reference_data=FakeReferenceDataManager())

    with pytest.raises(IRPAPIError, match="returned a non-list response"):
        AnalysisManager(irp).describe_run(101)


def test_non_list_treaty_response_raises():
    """Raise IRPAPIError rather than reading an envelope's keys as treaties."""
    client = FakeClient([
        FakeResponse(200, OWN_DLM_DETAIL),
        FakeResponse(200, rows(OWN_DLM_REGION_ROW)),
        FakeResponse(200, {"items": list(OWN_DLM_TREATIES), "totalCount": 1}),
    ])
    irp = SimpleNamespace(client=client, reference_data=FakeReferenceDataManager())

    with pytest.raises(
        IRPAPIError,
        match="Treaty search for analysis ID 101 returned a non-list response",
    ):
        AnalysisManager(irp).describe_run(101)


@pytest.mark.parametrize("unnumbered", ["absent", "empty"])
def test_treaty_without_a_treaty_number_is_reported_and_kept(unnumbered):
    """Report treaty_number_missing and describe the rest of the run anyway."""
    first = dict(OWN_DLM_TREATIES[0])
    if unnumbered == "absent":
        del first["treatyNumber"]
    else:
        first["treatyNumber"] = ""
    second = dict(
        OWN_DLM_TREATIES[0],
        treatyId=24,
        treatyNumber="example_treaty_4",
        treatyName="Example Second Working Layer",
    )
    manager, _ = make_manager(
        OWN_DLM_DETAIL, rows(OWN_DLM_REGION_ROW), [first, second]
    )

    description = manager.describe_run(101)

    assert [treaty.treaty_number for treaty in description.treaties] == [
        None, "example_treaty_4"
    ]
    assert [treaty.treaty_id for treaty in description.treaties] == [21, 24]
    assert description.treaties[0].treaty_name == "Example Working Layer"
    assert description.treaties[0].terms["occurrenceLimit"] == 1000000.0
    assert len(description.regions) == 23
    assert description.event_rate_scheme_names == {
        739: "RMS 2025 Stochastic Event Rates"
    }
    assert [problem.code for problem in description.problems] == [
        "treaty_number_missing"
    ]
    assert description.problems[0].treaty_ids == (21,)
    assert problem_message(description) == (
        "Treaty for analysis ID 101 has no Treaty Number."
    )


def test_scheme_name_skips_a_row_whose_scheme_id_is_not_a_positive_int():
    """Read eventRateSchemeId the way the three other readers of the field do.

    A bare ``==`` matched a row carrying True against scheme ID 1, and named
    the analysis's scheme after it."""
    manager, reference_data = make_manager(
        OWN_DLM_DETAIL,
        rows(dict(OWN_DLM_REGION_ROW, eventRateSchemeId=1)),
    )
    reference_data.event_rate_schemes.append({
        "eventRateSchemeId": True,
        "perilCode": "WS",
        "modelRegionCode": "NAWS",
        "modelVersionCode": "11.0",
        "eventRateSchemeName": "Not a scheme name",
        "isActive": True,
    })

    description = manager.describe_run(101)

    assert {region.event_rate_scheme_id for region in description.regions} == {1}
    assert description.event_rate_scheme_names == {}


def test_failed_pet_metadata_read_reports_the_row_instead_of_raising():
    """Describe the run when the bulk PETMetadata read fails on a row needing it.

    ``_region_facts`` runs outside ``_inspect``'s per-analysis try/except, so a
    500 on the 2,844-row read aborted the whole call. The PLT row's own peril
    "Earthquake" and the group detail's YY resolve no model version, so the row
    needs the derived candidate; with the read failing it is reported, not
    raised on, and the read is attempted once rather than once per row."""
    manager, reference_data = make_manager(
        MULTI_PERIL_GROUP_DETAIL,
        MULTI_PERIL_GROUP_REGION_ROWS + [MULTI_PERIL_PLT_REGION_ROW],
    )
    reference_data.all_pet_metadata_error = IRPAPIError(
        "500 Server Error: Internal Server Error"
    )

    description = manager.describe_run(105)

    assert [region.sub_region for region in description.regions] == ["FL", "CA"]
    assert [problem.code for problem in description.problems] == [
        "model_version_mapping_missing"
    ]
    assert reference_data.all_pet_metadata_calls == 1


def test_a_row_peril_code_that_resolves_skips_the_pet_metadata_read():
    """Read the derived peril only once the row's own code and the detail's fail.

    The wildfire rows carry peril "Wildfire", which resolves to the detail's WF,
    so the 2,844-row PETMetadata pagination the derived candidate needs is never
    read. Every row still records WF and model version 2.0."""
    manager, reference_data = make_manager(
        WILDFIRE_DETAIL, rows(WILDFIRE_REGION_ROW)
    )

    description = manager.describe_run(106)

    assert description.problems == ()
    assert len(description.regions) == 23
    assert {region.peril_code for region in description.regions} == {"WF"}
    assert {region.model_version for region in description.regions} == {"2.0"}
    assert reference_data.all_pet_metadata_calls == 0


def test_rows_failing_the_model_version_lookup_each_name_their_sub_region():
    """Report one problem per sub-region, not 23 byte-identical ones.

    Naming the row is the point of reporting a dropped row rather than raising:
    a caller has to be able to tell which of AL, CT, D1 was dropped."""
    detail = dict(OWN_DLM_DETAIL, engineVersion="RL99")
    manager, _ = make_manager(
        detail, rows(dict(OWN_DLM_REGION_ROW, engineVersion="RL99"))
    )

    description = manager.describe_run(101)

    assert description.regions == ()
    assert {problem.code for problem in description.problems} == {
        "model_version_mapping_missing"
    }
    assert [problem.sub_regions for problem in description.problems] == [
        (code,) for code in NA_WS_SUB_REGIONS
    ]


def test_a_reported_row_is_still_described():
    """Describe a row and report it: the two tuples do not partition the rows.

    A PLT row with applyContractFlag set is kept in ``regions`` and reported in
    ``problems``, so ``len(regions) + len(problems)`` is not the Platform's row
    count. The message states what is true of the row rather than that the
    analysis cannot be grouped, which is not what describe_run was asked."""
    manager, _ = make_manager(
        WILDFIRE_DETAIL, [dict(WILDFIRE_REGION_ROW, applyContractFlag=True)]
    )

    description = manager.describe_run(106)

    assert len(description.regions) == 1
    assert description.regions[0].apply_contract_flag is True
    assert description.regions[0].sub_region == "N2"
    assert [problem.code for problem in description.problems] == [
        "apply_contract_flag_unsupported"
    ]
    assert description.problems[0].sub_regions == ("N2",)
    assert problem_message(description) == (
        "PLT analysis 106 has a region row with applyContractFlag set."
    )


def test_an_unclassified_region_row_is_reported_without_grouping_vocabulary():
    """Report a row with no ELT/PLT classification as a region-row problem.

    ``describe_run`` describes one analysis, so the code names the region row
    rather than a group member the caller never offered."""
    manager, _ = make_manager(
        WILDFIRE_DETAIL, [dict(WILDFIRE_REGION_ROW, framework="Unknown")]
    )

    description = manager.describe_run(106)

    assert description.regions == ()
    assert [problem.code for problem in description.problems] == [
        "region_row_metadata_missing"
    ]
    assert problem_message(description) == (
        "Analysis 106 has a region row with no ELT/PLT classification."
    )
