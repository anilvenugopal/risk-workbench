"""Rules of the inspection screen's view model (spec 012, contracts/routes.md),
on hand-built package inspections — no database, no gateway."""

from __future__ import annotations

from app.services.grouping_service import GroupingInspectionView, GroupMember
from app.services.grouping_view import build_inspection_screen
from app.services.irp_gateway import (
    EventRateSchemeOption,
    GroupingInspection,
    GroupingMember,
    GroupingPartition,
    GroupingPartitionKey,
    GroupingProblem,
    GroupingRegionFact,
)

WS_NA = GroupingPartitionKey("WS", "NA", "11.0")


def _member(irp_id: int, name: str) -> GroupMember:
    return GroupMember(id=f"id-{irp_id}", irp_id=irp_id, name=name,
                       display_name=name, kind="own", engine="DLM")


def _fact(analysis_id: int, key=WS_NA, *, scheme: int | None = 101,
          engine_version: str = "RL25") -> GroupingRegionFact:
    return GroupingRegionFact(
        analysis_id=analysis_id, framework="ELT" if scheme else "PLT",
        peril_code=key.peril_code, region_code=key.region_code,
        model_version=key.model_version, engine_version=engine_version,
        sub_region="NA", model_region_code="NA_WS", event_rate_scheme_id=scheme,
        pet_id=None if scheme else 900, periods=None if scheme else 10000,
        apply_contract_flag=False)


def _view(facts_by_member: dict[int, list[GroupingRegionFact]],
          partitions: tuple[GroupingPartition, ...],
          problems: tuple[GroupingProblem, ...] = (),
          members: dict[int, GroupMember] | None = None,
          output: str = "ELT") -> GroupingInspectionView:
    ids = tuple(facts_by_member)
    inspection = GroupingInspection(
        analysis_ids=ids, resource_uris=(), inspected_at="now", fingerprint="v1:x",
        members=tuple(GroupingMember(
            analysis_id=i, exists=True, is_group=False, analysis_framework="ELT",
            engine_type="DLM", engine_version="RL25", peril_code="WS",
            region_code="NA", model_version="11.0", regions=tuple(facts))
            for i, facts in facts_by_member.items()),
        output_loss_table=output, simulate_to_plt=output == "PLT",
        partitions=partitions, simulation_mappings=(), required_caller_inputs=(),
        warnings=(), blocking_problems=problems)
    if members is None:
        members = {i: _member(i, f"A{i}") for i in ids}
    return GroupingInspectionView(inspection=inspection, members=members,
                                  suggested_num_of_simulations=1)


def _partition(ids, options, *, required=False, compatible=True, key=WS_NA):
    return GroupingPartition(
        key=key, analysis_ids=tuple(ids), event_rate_scheme_options=tuple(options),
        observed_pet_ids=(), event_rate_selection_required=required,
        simulation_set_compatible=compatible)


def test_one_shared_scheme_resolves_the_row():
    view = _view({1: [_fact(1)], 2: [_fact(2)]},
                 (_partition([1, 2], [EventRateSchemeOption(101, "RL25 NA HU")]),))

    screen = build_inspection_screen(view)

    row, = screen.rows
    assert row.mode == "resolved"
    assert row.resolved.label == "RL25 NA HU"
    assert row.resolved.member_count == 2
    assert row.member_names == ("A1", "A2")
    assert row.label == "WS / NA / 11.0"
    assert (screen.blocked, screen.conflict_count) == (False, 0)


def test_conflicting_schemes_offer_labelled_options_with_member_counts():
    view = _view(
        {1: [_fact(1, scheme=101)], 2: [_fact(2, scheme=739)],
         3: [_fact(3, scheme=739, engine_version="RL23")]},
        (_partition([1, 2, 3], [EventRateSchemeOption(101, "RL25 NA HU"),
                                EventRateSchemeOption(739, None)],
                    required=True),))

    row, = build_inspection_screen(view).rows

    assert row.mode == "choose"
    assert [(o.label, o.member_count) for o in row.options] == [
        ("RL25 NA HU", 1), ("Scheme 739", 2)]
    assert row.options[1].value == {
        "peril_code": "WS", "region_code": "NA", "model_version": "11.0",
        "event_rate_scheme_id": 739}
    assert row.engine_versions == ("RL23", "RL25")
    assert build_inspection_screen(view).conflict_count == 1


def test_incompatible_simulation_sets_win_over_a_required_choice():
    view = _view({1: [_fact(1, scheme=None)], 2: [_fact(2, scheme=None)]},
                 (_partition([1, 2], [EventRateSchemeOption(101), EventRateSchemeOption(2)],
                             required=True, compatible=False),),
                 output="PLT")

    row, = build_inspection_screen(view).rows

    assert row.mode == "incompatible"
    assert row.resolved is None


def test_a_partition_without_schemes_has_nothing_to_show():
    view = _view({1: [_fact(1, scheme=None)]}, (_partition([1], []),), output="PLT")

    row, = build_inspection_screen(view).rows

    assert row.mode == "none"
    assert row.options == ()


def test_problems_carry_the_message_and_the_members_display_names():
    problem = GroupingProblem(
        code="member_not_found", message="Analysis 77 was not found.",
        analysis_ids=(1, 77))
    view = _view({1: [_fact(1)]}, (_partition([1], [EventRateSchemeOption(101)]),),
                 problems=(problem,), members={1: _member(1, "CRE_P1_T1")})

    screen = build_inspection_screen(view)

    assert screen.blocked
    text, = screen.problems
    assert text.text == "Analysis 77 was not found."
    assert text.member_names == ("CRE_P1_T1", "77")
