"""Unit tests for the breakout plan builder and overlap arithmetic (spec 005
T021 — FR-010/FR-007, R4/P-11/P-13).

``build_breakout_plan`` is pure and deterministic: name ≤ 40 characters with
the source truncated and the value kept whole (suffix room reserved), number
≤ 20 characters with a hash tail on a long token, collision suffixing against
existing AND intra-plan names, ``exists`` marking, and stable value ordering.
``compute_overlap`` is Σ accounts versus ``account_total``. Blank values never
appear here — the summary SQL scrubs them; the disclosure is UI copy.
"""

from __future__ import annotations

from app.services.breakout_service import (
    PORTFOLIO_NAME_MAX,
    PORTFOLIO_NUMBER_MAX,
    BreakoutValue,
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


def test_collision_takes_lowest_free_suffix_against_existing_names():
    existing = {"usfl_commercial - TX", "usfl_commercial - TX (2)"}
    plan = _plan([_bv("TX")], existing_names=existing)
    assert plan[0].name == "usfl_commercial - TX (3)"
    assert len(plan[0].name) <= PORTFOLIO_NAME_MAX


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
    assert plan[0].number == "P4319-S-TX"
    plan = _plan([_bv("FLD Comm")], source_irp_id="4319", dimension="lob")
    assert plan[0].number == "P4319-L-FLDCOMM"  # non-alphanumerics removed, uppercased
    assert len(plan[0].number) <= PORTFOLIO_NUMBER_MAX


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

def test_overlap_clean_partition():
    overlap = compute_overlap([_bv("TX", 220), _bv("CA", 1481)], 1701)
    assert overlap.summed == 1701
    assert overlap.repeats == 0
    assert overlap.partition is True


def test_overlap_heavy_repeats():
    overlap = compute_overlap([_bv("TX", 1200), _bv("CA", 900)], 1701)
    assert overlap.repeats == 399
    assert overlap.partition is False


def test_overlap_absent_account_total_degrades_to_qualitative():
    overlap = compute_overlap([_bv("TX", 220)], None)
    assert overlap.account_total is None
    assert overlap.repeats is None
    assert overlap.partition is False


def test_overlap_never_negative():
    # A stale count sum below the total floors at 0, never a negative repeat.
    overlap = compute_overlap([_bv("TX", 100)], 1701)
    assert overlap.repeats == 0
    assert overlap.partition is True
