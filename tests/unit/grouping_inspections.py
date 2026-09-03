"""The mixed grouping the approver reproduced in Risk Modeler (spec 012
research.md, 2026-09-03) as a hand-built package inspection for the unit tier:
one HD analysis (Japan typhoon, PLT on PET 15), one DLM analysis (US
earthquake, ELT on scheme 163) and one finished group (US hurricane, ELT on
schemes 738 and 739). The output is PLT, so both ELT partitions offer
simulation sets and require a choice; the HD partition keeps its PET.
"""

from __future__ import annotations

from app.services.irp_gateway import (
    EventRateSchemeOption,
    GroupingInspection,
    GroupingMember,
    GroupingPartition,
    GroupingPartitionKey,
    GroupingRegionFact,
    SimulationSetOption,
)
from db import execute_one
from tests.unit.grouping_rows import seed_group, seed_own_analysis

HD_NAME = "CRE_WS_JP_COM_HD_JPWS_Stochastic_Typhoon-Only"
DLM_NAME = "CRE_EQ_HI_RES_US EQ wFFSL wDS - PERS Stochastic"
GROUP_NAME = "HU_US Workbench Group"

JP_WS = GroupingPartitionKey("WS", "JP", "2.1")
JP_WS_PET_NAME = "RMS V2.0 Stochastic Event Rates - Typhoon Events Only"
NA_EQ = GroupingPartitionKey("EQ", "NA", "17.0")
NA_WS = GroupingPartitionKey("WS", "NA", "11.0")
FINGERPRINT = "v5:" + "c" * 64

# Reference rows: ``event_rate_scheme_id`` names the scheme the row was built
# for and constrains nothing — set 147 names 739 and is submitted under 738.
NA_EQ_SIMULATION_SETS = (
    SimulationSetOption(83, 50000, 161, "North America Earthquake Historical"),
    SimulationSetOption(84, 50000, 162, "North America Earthquake Time-Dependent"),
    SimulationSetOption(85, 100000, 163, "North America Earthquake Stochastic 100K"),
    SimulationSetOption(86, 100000, 164, "North America Earthquake Long Term"),
    SimulationSetOption(87, 100000, 163, "North America Earthquake Stochastic"),
)
NA_WS_SIMULATION_SETS = (
    SimulationSetOption(146, 100000, 738, "North Atlantic Hurricane Historical v2"),
    SimulationSetOption(147, 100000, 739, "North Atlantic Hurricane Stochastic v2"),
)


def _fact(analysis_id: int, key: GroupingPartitionKey, *, framework: str,
          engine_version: str, sub_region: str, scheme: int | None = None,
          pet_id: int | None = None, pet_name: str | None = None,
          periods: int | None = None) -> GroupingRegionFact:
    return GroupingRegionFact(
        analysis_id=analysis_id, framework=framework, peril_code=key.peril_code,
        region_code=key.region_code, model_version=key.model_version,
        engine_version=engine_version, sub_region=sub_region,
        model_region_code=f"{sub_region}{key.peril_code}",
        event_rate_scheme_id=scheme, pet_id=pet_id, pet_name=pet_name,
        periods=periods,
        apply_contract_flag=False)


def mixed_group_inspection(hd_id: int, dlm_id: int, group_id: int) -> GroupingInspection:
    members = (
        GroupingMember(
            analysis_id=hd_id, exists=True, is_group=False, analysis_framework="PLT",
            engine_type="HD", engine_version="HDv2.1", peril_code="WS",
            region_code="JP", model_version="2.1",
            regions=(_fact(hd_id, JP_WS, framework="PLT", engine_version="HDv2.1",
                           sub_region="JP", pet_id=15, pet_name=JP_WS_PET_NAME,
                           periods=50000),)),
        GroupingMember(
            analysis_id=dlm_id, exists=True, is_group=False, analysis_framework="ELT",
            engine_type="DLM", engine_version="RL25", peril_code="EQ",
            region_code="NA", model_version="17.0",
            regions=(_fact(dlm_id, NA_EQ, framework="ELT", engine_version="RL25",
                           sub_region="CA", scheme=163),)),
        GroupingMember(
            analysis_id=group_id, exists=True, is_group=True, analysis_framework="ELT",
            engine_type="Group", engine_version="RL25", peril_code="WS",
            region_code="NA", model_version="11.0",
            regions=(_fact(group_id, NA_WS, framework="ELT", engine_version="RL23",
                           sub_region="FL", scheme=739),
                     _fact(group_id, NA_WS, framework="ELT", engine_version="RL25",
                           sub_region="FL", scheme=738))),
    )
    partitions = (
        GroupingPartition(
            key=NA_EQ, analysis_ids=(dlm_id,),
            event_rate_scheme_options=(EventRateSchemeOption(163, None),),
            observed_pet_ids=(), event_rate_selection_required=False,
            simulation_set_options=NA_EQ_SIMULATION_SETS,
            simulation_set_selection_required=True),
        GroupingPartition(
            key=JP_WS, analysis_ids=(hd_id,), event_rate_scheme_options=(),
            observed_pet_ids=(15,), event_rate_selection_required=False),
        GroupingPartition(
            key=NA_WS, analysis_ids=(group_id,),
            event_rate_scheme_options=(
                EventRateSchemeOption(738, "RMS 2025 Historical Event Rates"),
                EventRateSchemeOption(739, "RMS 2025 Stochastic Event Rates")),
            observed_pet_ids=(), event_rate_selection_required=True,
            simulation_set_options=NA_WS_SIMULATION_SETS,
            simulation_set_selection_required=True),
    )
    ids = (hd_id, dlm_id, group_id)
    return GroupingInspection(
        analysis_ids=ids,
        resource_uris=tuple(f"/platform/riskdata/v1/analyses/{i}" for i in ids),
        inspected_at="2026-09-03T00:00:00+00:00", fingerprint=FINGERPRINT,
        members=members, output_loss_table="PLT", simulate_to_plt=True,
        partitions=partitions, simulation_mappings=(),
        required_caller_inputs=("analysis_name", "currency",
                                "propagate_detailed_losses", "num_of_simulations",
                                "event_rate_selections", "simulation_set_selections"),
        warnings=(), blocking_problems=())


def seed_mixed_group(fake_irp, submission_id: str, edm_id: str) -> dict:
    """Seed the three members on the submission and make ``fake_irp`` return
    their inspection. Returns the member ids, their Platform ids in posted
    order, and the inspection."""
    hd = seed_own_analysis(edm_id, HD_NAME, settings={"engineType": "HD"})
    dlm = seed_own_analysis(edm_id, DLM_NAME, settings={"engineType": "DLM"})
    group = seed_group(submission_id, GROUP_NAME)
    irp_ids = [_irp(i) for i in (hd, dlm, group)]
    fake_irp.grouping_inspection = mixed_group_inspection(*irp_ids)
    return {"member_ids": [hd, dlm, group], "irp_ids": irp_ids,
            "inspection": fake_irp.grouping_inspection}


def _irp(analysis_id: str) -> int:
    return int(execute_one("SELECT irp_id FROM irp_analysis WHERE id = :id",
                           {"id": analysis_id}, connection="WORKBENCH")["irp_id"])
