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
``display_value`` turns a peril code into its mnemonic and leaves every other
dimension's value untouched.
"""

from __future__ import annotations

import re

import pytest

from app.services.breakout_service import (
    PORTFOLIO_NAME_MAX,
    PORTFOLIO_NUMBER_MAX,
    BreakoutValue,
    DimensionCoverage,
    build_breakout_plan,
    compute_overlap,
    display_value,
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
    values = [_bv("US-TX", 220), _bv("US-CA", 1289), _bv("US-FL", 88)]
    first = _plan(values)
    second = _plan(list(reversed(values)))
    assert first == second
    assert [p.value for p in first] == ["US-CA", "US-FL", "US-TX"]


def test_unlabeled_state_names_by_its_value_and_hashes_its_number():
    # P-31: the hyphen in `US-TX` is legal in a name but cannot be carried in
    # the number token, so the number takes the hash tail.
    plan = _plan([_bv("US-TX"), _bv("US-CA")])
    assert [p.name for p in plan] == ["usfl_commercial_US-CA",
                                      "usfl_commercial_US-TX"]
    assert re.fullmatch(r"P1-S-USCA[0-9A-F]{6}", plan[0].number)
    assert re.fullmatch(r"P1-S-USTX[0-9A-F]{6}", plan[1].number)


def test_country_dimension_numbers_with_its_own_letter():
    plan = _plan([_bv("US"), _bv("CA")], dimension="country")
    assert [p.number for p in plan] == ["P1-C-CA", "P1-C-US"]


def test_long_source_is_truncated_and_value_kept_whole():
    source = "TY2607 Meridian Cedant Commercial Book"  # 38 chars
    plan = _plan([_bv("General Liability", 5), _bv("Homeowners", 7)],
                 source_name=source, dimension="lob")
    for p in plan:
        assert len(p.name) <= PORTFOLIO_NAME_MAX - 4  # suffix room reserved
        assert p.name.endswith("_" + p.value.replace(" ", "_"))  # the value stays whole
    # source budget for "General_Liability" (17): 40 − 4 − 1 − 17 = 18
    assert plan[0].name == "TY2607_Meridian_Ce_General_Liability"


def test_very_long_value_truncates_value_after_source_floor():
    # A value long enough to push the source below 4 characters truncates the
    # VALUE from the right instead (safe — the number is the identity, R4).
    value = "V" * 40
    plan = _plan([_bv(value), _bv("US-TX")], source_name="usfl_commercial")
    long_entry = next(p for p in plan if p.value == value)
    assert len(long_entry.name) <= PORTFOLIO_NAME_MAX - 4
    assert long_entry.name.startswith("usfl")          # the 4-char source floor
    assert long_entry.value == value                    # the plan value is untruncated


def test_name_uses_display_label_and_identity_keeps_the_code():
    # P-12 as revised 2026-08-05: a geocoded portfolio names its
    # sub-portfolios by Admin1Name, never by the Admin1Code — while the value,
    # the number token, and the sort order all keep the code. BE-11 and NL-11
    # share the code 11 and stay apart (P-31).
    plan = _plan([_bv("NL-11", 3, label="Groningen"),
                  _bv("BE-11", 5, label="Antwerpen")])
    assert [p.value for p in plan] == ["BE-11", "NL-11"]
    by_value = {p.value: p for p in plan}
    assert by_value["BE-11"].name == "usfl_commercial_Antwerpen"
    assert by_value["NL-11"].name == "usfl_commercial_Groningen"
    assert by_value["BE-11"].number.startswith("P1-S-BE11")
    assert by_value["NL-11"].number.startswith("P1-S-NL11")


def test_identical_labels_on_distinct_values_get_collision_suffixed():
    # Two codes carrying the same label compose the same base name — the
    # intra-plan suffix keeps them distinct; the numbers never collide.
    plan = _plan([_bv("VI-010", label="Twin"), _bv("VI-020", label="Twin")])
    assert [p.name for p in plan] == ["usfl_commercial_Twin",
                                      "usfl_commercial_Twin_2"]
    assert len({p.number for p in plan}) == 2


def test_collision_takes_lowest_free_suffix_against_existing_names():
    existing = {"usfl_commercial_US-TX", "usfl_commercial_US-TX_2"}
    plan = _plan([_bv("US-TX")], existing_names=existing)
    assert plan[0].name == "usfl_commercial_US-TX_3"
    assert len(plan[0].name) <= PORTFOLIO_NAME_MAX


def test_collision_detection_ignores_case():
    # Risk Modeler rejects a duplicate name without distinguishing case, so an
    # existing USFL_COMMERCIAL_us-tx must push the planned name to a suffix —
    # otherwise the create fails on a name the analyst already approved.
    plan = _plan([_bv("US-TX")], existing_names={"USFL_COMMERCIAL_us-tx"})
    assert plan[0].name == "usfl_commercial_US-TX_2"


def test_intra_plan_collisions_are_suffixed_too():
    # Two long values truncated alike must not produce the same composed name.
    a = "A" * 35 + "one!!"
    b = "A" * 35 + "two!!"
    plan = _plan([_bv(a), _bv(b)], source_name="usfl_commercial")
    names = [p.name for p in plan]
    assert len(set(names)) == 2
    assert names[1] == f"{names[0]}_2"
    assert all(len(n) <= PORTFOLIO_NAME_MAX for n in names)


# ── numbers ───────────────────────────────────────────────────────────────────────

def test_number_shape_and_budget():
    plan = _plan([_bv("US")], source_irp_id="4319", dimension="country")
    assert plan[0].number == "P4319-C-US"      # already number-safe → verbatim
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
    clean = _plan([_bv("US-TX")])
    collided = _plan([_bv("US-TX")], existing_names={"usfl_commercial_US-TX"})
    assert clean[0].name != collided[0].name
    assert clean[0].number == collided[0].number


# ── exists marking ────────────────────────────────────────────────────────────────

def test_exists_marks_values_with_live_lineage_rows():
    plan = _plan([_bv("US-TX"), _bv("US-CA")], existing_values={"US-TX"})
    by_value = {p.value: p for p in plan}
    assert by_value["US-TX"].exists is True
    assert by_value["US-CA"].exists is False


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


# ── peril display (D4 — closes O-02) ────────────────────────────────────────────

def test_peril_codes_display_as_mnemonics():
    assert [display_value(v, "peril")
            for v in ("1", "2", "3", "4", "5", "6", "7")] == [
        "EQ", "WS", "CS/WT", "FL", "FR", "TR", "WC"]


def test_unmapped_peril_code_displays_as_itself():
    # A code the maintained map does not carry is shown raw — never a guessed
    # mnemonic (the same rule P-12 applies to labels).
    assert display_value("42", "peril") == "42"


def test_peril_plan_names_by_mnemonic_and_numbers_by_code():
    # The name and the entry label read the mnemonic; the value the run filters
    # on and the number token stay the numeric code (P-30).
    plan = _plan([_bv("2", 1701), _bv("4", 517)], dimension="peril")
    assert [(p.value, p.label, p.name, p.number) for p in plan] == [
        ("2", "WS", "usfl_commercial_WS", "P1-P-2"),
        ("4", "FL", "usfl_commercial_FL", "P1-P-4")]


def test_unmapped_peril_code_names_by_code_and_carries_no_label():
    plan = _plan([_bv("42")], dimension="peril")
    assert (plan[0].name, plan[0].label) == ("usfl_commercial_42", None)


def test_other_dimensions_display_their_value_verbatim():
    assert display_value("2", "state") == "2"
    assert display_value("EQ Comm", "lob") == "EQ Comm"
