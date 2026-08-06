"""Unit tests for the breakout plan builder and overlap arithmetic (spec 005
T021 — FR-010/FR-007, R4/P-11/P-13).

``build_breakout_plan`` is pure and deterministic: name ≤ 40 characters with
the source truncated and the name token — the display label where one exists,
else the value (P-12 as revised 2026-08-05) — kept whole (suffix room
reserved), number ≤ 20 characters with a hash tail on a long token, collision
suffixing against existing AND intra-plan names, ``exists`` marking, and
stable value ordering.
``compute_overlap`` reads the two counts the summary measured per account —
accounts carrying at least one value, accounts carrying more than one — and
never derives either from Σ accounts, which counts memberships. Blank values
never appear in the value list (the summary SQL scrubs them); how many accounts
they cost is what ``uncovered`` states.
"""

from __future__ import annotations

import pytest

from app.services.breakout_service import (
    PORTFOLIO_NAME_MAX,
    PORTFOLIO_NUMBER_MAX,
    BreakoutValue,
    DimensionCoverage,
    build_breakout_plan,
    compute_overlap,
)


def _bv(value: str, accounts: int = 10, label: str | None = None) -> BreakoutValue:
    return BreakoutValue(value=value, label=label, accounts=accounts)


def _plan(values, *, source_name="usfl_commercial", source_irp_id="1",
          dimension="state", existing_names=(), existing_values=()):
    return build_breakout_plan(
        source_name=source_name, source_portfolio_irp_id=source_irp_id,
        dimension=dimension, values=values,
        existing_names=existing_names, existing_values=existing_values)


# ── naming ────────────────────────────────────────────────────────────────────────

def test_plan_is_deterministic_and_sorted_by_value():
    values = [_bv("TX", 220), _bv("CA", 1289), _bv("FL", 88)]
    first = _plan(values)
    second = _plan(list(reversed(values)))
    assert first == second
    assert [p.value for p in first] == ["CA", "FL", "TX"]


def test_short_names_compose_untouched():
    plan = _plan([_bv("TX"), _bv("CA")])
    assert [p.name for p in plan] == ["usfl_commercial - CA",
                                      "usfl_commercial - TX"]
    assert [p.number for p in plan] == ["P1-S-CA", "P1-S-TX"]


def test_long_source_is_truncated_and_value_kept_whole():
    source = "TY2607 Meridian Cedant Commercial Book"  # 38 chars
    plan = _plan([_bv("General Liability", 5), _bv("Homeowners", 7)],
                 source_name=source, dimension="lob")
    for p in plan:
        assert len(p.name) <= PORTFOLIO_NAME_MAX - 4  # suffix room reserved
        assert p.name.endswith(f" - {p.value}")       # the value stays whole
    # source budget for "General Liability" (17): 40 − 4 − 3 − 17 = 16
    assert plan[0].name == f"{source[:16].rstrip()} - General Liability"


def test_very_long_value_truncates_value_after_source_floor():
    # A value long enough to push the source below 4 characters truncates the
    # VALUE from the right instead (safe — the number is the identity, R4).
    value = "V" * 40
    plan = _plan([_bv(value), _bv("TX")], source_name="usfl_commercial")
    long_entry = next(p for p in plan if p.value == value)
    assert len(long_entry.name) <= PORTFOLIO_NAME_MAX - 4
    assert long_entry.name.startswith("usfl")          # the 4-char source floor
    assert long_entry.value == value                    # the plan value is untruncated


def test_name_uses_display_label_and_identity_keeps_the_code():
    # P-12 as revised 2026-08-05: a Caribbean portfolio names its
    # sub-portfolios by Admin1Name, never by the numeric Admin1Code — while
    # the value, the number token, and the sort order all keep the code.
    plan = _plan([_bv("200", 2437, label="Puerto Rico"),
                  _bv("010", 74, label="St Croix")])
    assert [p.value for p in plan] == ["010", "200"]
    by_value = {p.value: p for p in plan}
    assert by_value["200"].name == "usfl_commercial - Puerto Rico"
    assert by_value["010"].name == "usfl_commercial - St Croix"
    assert by_value["200"].number == "P1-S-200"
    assert by_value["010"].number == "P1-S-010"


def test_identical_labels_on_distinct_values_get_collision_suffixed():
    # Two codes carrying the same label compose the same base name — the
    # intra-plan suffix keeps them distinct; the numbers never collide.
    plan = _plan([_bv("010", label="Twin"), _bv("020", label="Twin")])
    assert [p.name for p in plan] == ["usfl_commercial - Twin",
                                      "usfl_commercial - Twin (2)"]
    assert len({p.number for p in plan}) == 2


def test_collision_takes_lowest_free_suffix_against_existing_names():
    existing = {"usfl_commercial - TX", "usfl_commercial - TX (2)"}
    plan = _plan([_bv("TX")], existing_names=existing)
    assert plan[0].name == "usfl_commercial - TX (3)"
    assert len(plan[0].name) <= PORTFOLIO_NAME_MAX


def test_collision_detection_ignores_case():
    # Risk Modeler rejects a duplicate name without distinguishing case, so an
    # existing USFL_COMMERCIAL - tx must push the planned name to a suffix —
    # otherwise the create fails on a name the analyst already approved.
    plan = _plan([_bv("TX")], existing_names={"USFL_COMMERCIAL - tx"})
    assert plan[0].name == "usfl_commercial - TX (2)"


def test_intra_plan_collisions_are_suffixed_too():
    # Two long values truncated alike must not produce the same composed name.
    a = "A" * 35 + "one!!"
    b = "A" * 35 + "two!!"
    plan = _plan([_bv(a), _bv(b)], source_name="usfl_commercial")
    names = [p.name for p in plan]
    assert len(set(names)) == 2
    assert names[1] == f"{names[0]} (2)"
    assert all(len(n) <= PORTFOLIO_NAME_MAX for n in names)


# ── numbers ───────────────────────────────────────────────────────────────────────

def test_number_shape_and_budget():
    plan = _plan([_bv("TX")], source_irp_id="4319", dimension="state")
    assert plan[0].number == "P4319-S-TX"      # already number-safe → verbatim
    plan = _plan([_bv("FLD Comm")], source_irp_id="4319", dimension="lob")
    # the space cannot be carried, so the token is hashed rather than merged
    # with the number a different value would compose
    assert plan[0].number.startswith("P4319-L-FLDC")
    assert len(plan[0].number) <= PORTFOLIO_NUMBER_MAX


def test_an_unregistered_dimension_refuses_to_compose_a_number():
    # The number is the identity adoption resolves on, so a dimension with no
    # registered letter raises rather than deriving one from the code — two
    # codes sharing a first letter would otherwise compose one number for two
    # different breakouts of the same value.
    with pytest.raises(ValueError, match="no portfolio_number letter"):
        _plan([_bv("TX")], source_irp_id="1", dimension="complement")


def test_values_differing_only_in_punctuation_whitespace_or_case_never_share_a_number():
    # Stripping non-alphanumerics and uppercasing map all four of these onto the
    # token AB. The number is the identity adoption resolves on (FR-011), so
    # each value must still get its own (R4).
    values = ["AB", "A-B", "a b", "ab", " AB"]
    plan = _plan([_bv(v) for v in values], source_irp_id="1", dimension="lob")
    numbers = [p.number for p in plan]
    assert len(set(numbers)) == len(values)
    assert all(len(n) <= PORTFOLIO_NUMBER_MAX for n in numbers)
    assert all(n.startswith("P1-L-") for n in numbers)
    # only the value that needs no normalization keeps the readable form
    assert next(p.number for p in plan if p.value == "AB") == "P1-L-AB"


def test_long_token_gets_hash_tail_and_shared_prefixes_do_not_collide():
    a = "General Liability Commercial Lines Alpha"
    b = "General Liability Commercial Lines Bravo"
    plan = _plan([_bv(a), _bv(b)], source_irp_id="1", dimension="lob")
    numbers = [p.number for p in plan]
    assert all(len(n) <= PORTFOLIO_NUMBER_MAX for n in numbers)
    assert len(set(numbers)) == 2  # the sha256 tail separates shared prefixes
    assert all(n.startswith("P1-L-") for n in numbers)


def test_number_is_stable_across_runs_regardless_of_name_suffixing():
    # The number depends only on (source RM id, dimension, value) — the same
    # inputs with a different collision universe keep the identity stable (P-11).
    clean = _plan([_bv("TX")])
    collided = _plan([_bv("TX")], existing_names={"usfl_commercial - TX"})
    assert clean[0].name != collided[0].name
    assert clean[0].number == collided[0].number


# ── exists marking ────────────────────────────────────────────────────────────────

def test_exists_marks_values_with_live_lineage_rows():
    plan = _plan([_bv("TX"), _bv("CA")], existing_values={"TX"})
    by_value = {p.value: p for p in plan}
    assert by_value["TX"].exists is True
    assert by_value["CA"].exists is False


# ── overlap arithmetic (FR-007 / P-13) ────────────────────────────────────────────

def _cov(covered: int, multi_value: int) -> DimensionCoverage:
    return DimensionCoverage(covered=covered, multi_value=multi_value)


def test_overlap_clean_partition():
    # Every account carries exactly one value: no repeats, nothing uncovered.
    overlap = compute_overlap([_bv("TX", 220), _bv("CA", 1481)], 1701,
                              _cov(covered=1701, multi_value=0))
    assert overlap.summed == 1701
    assert overlap.covered == 1701
    assert overlap.uncovered == 0
    assert overlap.repeats == 0
    assert overlap.partition is True


def test_overlap_heavy_repeats():
    overlap = compute_overlap([_bv("TX", 1200), _bv("CA", 900)], 1701,
                              _cov(covered=1701, multi_value=399))
    assert overlap.repeats == 399
    assert overlap.uncovered == 0
    assert overlap.partition is False


def test_overlap_absent_coverage_degrades_to_qualitative():
    # A summary written before the 2026-08-05 revision carries no coverage.
    overlap = compute_overlap([_bv("TX", 220)], 1701, None)
    assert overlap.account_total == 1701
    assert overlap.covered is None
    assert overlap.uncovered is None
    assert overlap.repeats is None
    assert overlap.partition is False


def test_overlap_absent_account_total_still_reports_repeats():
    # No denominator means no coverage shortfall can be stated, but the
    # repeat count is measured independently of it.
    overlap = compute_overlap([_bv("TX", 220)], None, _cov(220, 12))
    assert overlap.account_total is None
    assert overlap.uncovered is None
    assert overlap.repeats == 12
    assert overlap.partition is False


def test_overlap_uncovered_accounts_are_not_a_clean_partition():
    # The case the old summed − account_total arithmetic reported as a clean
    # partition: 100 of 1,701 accounts carry a state, 1,601 land nowhere.
    overlap = compute_overlap([_bv("TX", 100)], 1701, _cov(covered=100,
                                                          multi_value=0))
    assert overlap.repeats == 0
    assert overlap.uncovered == 1601
    assert overlap.partition is False


def test_overlap_counts_an_account_once_however_many_values_it_carries():
    # One account in three states inflates `summed` by 2 but is ONE repeating
    # account — which is what the disclosure states.
    overlap = compute_overlap([_bv("TX", 1), _bv("CA", 1), _bv("NV", 1)], 3,
                              _cov(covered=3, multi_value=1))
    assert overlap.summed == 3
    assert overlap.repeats == 1


def test_overlap_uncovered_never_negative():
    # A coverage count above the stored total (summary halves written by
    # different script runs) floors at 0 rather than reporting a negative gap.
    overlap = compute_overlap([_bv("TX", 100)], 90, _cov(covered=100,
                                                        multi_value=0))
    assert overlap.uncovered == 0
    assert overlap.partition is True
