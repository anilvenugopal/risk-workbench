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
    GroupingTreaty,
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
          output: str = "ELT",
          warnings: tuple[GroupingProblem, ...] = ()) -> GroupingInspectionView:
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
        warnings=warnings, blocking_problems=problems)
    if members is None:
        members = {i: _member(i, f"A{i}") for i in ids}
    return GroupingInspectionView(inspection=inspection, members=members,
                                  suggested_num_of_simulations=1)


def _partition(ids, options, *, required=False, key=WS_NA):
    return GroupingPartition(
        key=key, analysis_ids=tuple(ids), event_rate_scheme_options=tuple(options),
        observed_pet_ids=(), event_rate_selection_required=required)


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


def _treaty(analysis_id: int, treaty_id: int | None, *,
            number: str = "XOL-2026-01", **overrides) -> GroupingTreaty:
    """One compared treaty, keyed and normalized the way the package hands it
    over: RM codes and ISO date-times raw, currency already its code."""
    terms = {"treatyType": "CATA", "effectiveDate": "2026-01-01T00:00:00.000Z",
             "expirationDate": "2026-12-31T00:00:00.000Z",
             "attachmentPoint": 5_000_000, "occurrenceLimit": 10_000_000,
             "riskLimit": 1_000_000, "currency": "USD", "priority": 1}
    terms.update(overrides)
    return GroupingTreaty(analysis_id=analysis_id, treaty_id=treaty_id,
                          treaty_number=number, terms=terms)


TREATY_WARNING = GroupingProblem(
    code="inconsistent_treaty_terms",
    message="Treaty number XOL-2026-01 has inconsistent loss-affecting terms.",
    analysis_ids=(1, 2), treaty_numbers=("XOL-2026-01",),
    treaty_ids=(88412, 90177),
    differing_fields=("attachmentPoint", "lobs", "maolAmount"),
    treaties=(_treaty(1, 88412),
              _treaty(2, 90177, attachmentPoint=2_500_000)))

WS_PARTITION = (_partition([1, 2], [EventRateSchemeOption(101)]),)
TWO_MEMBERS = {1: [_fact(1)], 2: [_fact(2)]}


def _mismatch(warning: GroupingProblem, **kwargs):
    screen = build_inspection_screen(
        _view(TWO_MEMBERS, WS_PARTITION, warnings=(warning,), **kwargs))
    mismatch, = screen.treaty_mismatches
    return screen, mismatch


def test_a_treaty_warning_becomes_one_row_per_compared_treaty():
    screen, mismatch = _mismatch(TREATY_WARNING)

    assert mismatch.treaty_number == "XOL-2026-01"
    assert mismatch.differing_terms == (
        "Attachment Point", "Lines of Business", "MAOL Amount")
    assert mismatch.analysis_count == 2
    first, second = mismatch.rows
    assert (first.analysis_name, first.analysis_id, first.treaty_id) == (
        "A1", 1, 88412)
    assert (second.analysis_name, second.analysis_id, second.treaty_id) == (
        "A2", 2, 90177)
    assert first.terms["attachmentPoint"] == 5_000_000
    assert second.terms["attachmentPoint"] == 2_500_000
    assert screen.treaty_mismatch_count == 1
    assert not screen.blocked


def test_treaty_terms_spell_out_codes_and_truncate_dates():
    _, mismatch = _mismatch(TREATY_WARNING)

    row = mismatch.rows[0]
    assert row.terms["treatyType"] == "Catastrophe"
    assert row.terms["effectiveDate"] == "2026-01-01"
    assert row.terms["expirationDate"] == "2026-12-31"
    assert row.terms["currency"] == "USD"


def test_a_currency_difference_tells_the_two_analyses_apart():
    warning = GroupingProblem(
        code="inconsistent_treaty_terms", message="Currency differs.",
        analysis_ids=(1, 2), treaty_numbers=("CATA-2026-04",),
        treaty_ids=(88412, 90177), differing_fields=("currency",),
        treaties=(_treaty(1, 88412, number="CATA-2026-04"),
                  _treaty(2, 90177, number="CATA-2026-04", currency="CAD")))

    _, mismatch = _mismatch(warning)

    assert mismatch.differing_terms == ("Currency",)
    assert [row.terms["currency"] for row in mismatch.rows] == ["USD", "CAD"]


def test_a_term_the_table_does_not_carry_is_still_named():
    warning = GroupingProblem(
        code="inconsistent_treaty_terms", message="Priority differs.",
        analysis_ids=(1, 2), treaty_numbers=("XOL-2026-09",),
        treaty_ids=(88420, 90185), differing_fields=("priority",),
        treaties=(_treaty(1, 88420, number="XOL-2026-09"),
                  _treaty(2, 90185, number="XOL-2026-09", priority=3)))

    screen, mismatch = _mismatch(warning)

    assert mismatch.differing_terms == ("Priority",)
    assert [key for key, _ in screen.treaty_columns
            if key in mismatch.differing_keys] == []


def test_a_treaty_risk_modeler_returned_without_an_id_still_gets_a_row():
    warning = GroupingProblem(
        code="inconsistent_treaty_terms", message="Occurrence limit differs.",
        analysis_ids=(1, 2), treaty_numbers=("XOL-2026-01",),
        treaty_ids=(88412,), differing_fields=("occurrenceLimit",),
        treaties=(_treaty(1, 88412),
                  _treaty(2, None, occurrenceLimit=2_000_000)))

    _, mismatch = _mismatch(warning)

    assert [row.treaty_id for row in mismatch.rows] == [88412, None]


def test_two_treaties_on_one_analysis_report_one_analysis():
    warning = GroupingProblem(
        code="inconsistent_treaty_terms", message="Occurrence limit differs.",
        analysis_ids=(1,), treaty_numbers=("XOL-2026-01",),
        treaty_ids=(88412, 88413), differing_fields=("occurrenceLimit",),
        treaties=(_treaty(1, 88412),
                  _treaty(1, 88413, occurrenceLimit=2_000_000)))

    _, mismatch = _mismatch(warning)

    assert len(mismatch.rows) == 2
    assert mismatch.analysis_count == 1


def test_a_treaty_on_an_unknown_analysis_falls_back_to_its_id():
    _, mismatch = _mismatch(TREATY_WARNING, members={1: _member(1, "CRE_P1_T1")})

    assert [row.analysis_name for row in mismatch.rows] == ["CRE_P1_T1", "2"]


def test_a_blocked_inspection_still_carries_its_treaty_mismatches():
    problem = GroupingProblem(code="member_not_found",
                              message="Analysis 2 was not found.",
                              analysis_ids=(2,))
    screen, _ = _mismatch(TREATY_WARNING, problems=(problem,))

    assert screen.blocked
    assert screen.treaty_mismatch_count == 1


def test_no_warnings_means_no_treaty_mismatches():
    screen = build_inspection_screen(_view(TWO_MEMBERS, WS_PARTITION))

    assert screen.treaty_mismatches == ()
    assert screen.treaty_mismatch_count == 0
