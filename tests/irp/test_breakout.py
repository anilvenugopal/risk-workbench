"""Sandbox round-trip for the breakout composition (spec 005 T061 — T-01/T-07/SC-003).

Re-verifies, in this app's tier, what the probe run established
(specs/005-subportfolio-breakouts/probe-findings.md): the DataBridge selection
vocabulary matches the stored-summary scripts byte-for-byte, create → add →
read-back composes whole accounts, re-adding members is safe (``completed 0``
— W-9), the 40/20-character name and number limits raise client-side, and a
40+ division state fan-out lands every entry with an outcome and refuses none
for size (SC-003).

**And proves the one thing the probe run did not**: that the DataBridge member
count sees the REST add immediately. The probe measured read-back equality
against the paginated REST enumeration; W-20 moved the read-back to a
DataBridge scalar on 2026-08-05 for the 100,000-account ceiling and never
re-proved write visibility across the two systems. Every sub-portfolio the
worker creates depends on it — see
``test_create_add_readback_chunking_and_idempotent_readd``.

Opt-in: ``pytest tests/irp --run-irp`` with the sandbox IRP env configured.
Fixture: the probe EDM (default ``usfl_edm_small`` / source portfolio RM id
``1`` — ``usfl_commercial``, 1,701 accounts, 48 states); override with
``IRP_TEST_BREAKOUT_EDM_NAME`` / ``IRP_TEST_BREAKOUT_PORTFOLIO_ID``.

Created sub-portfolios are named ``rwbt …`` and numbered under the
``P{pid}T-…`` prefix — a namespace the app itself never generates, so these
test portfolios can never be adopted by a real breakout run. The app never
deletes portfolios (P-07); re-runs of this suite adopt the existing ones by
``portfolioNumber`` instead of duplicating them.
"""

from __future__ import annotations

import os

import pytest

from app.services import irp_gateway
from app.services.breakout_service import (
    PORTFOLIO_NAME_MAX,
    PORTFOLIO_NUMBER_MAX,
    BreakoutValue,
    build_breakout_plan,
)

pytestmark = pytest.mark.irp

EDM_NAME = os.environ.get("IRP_TEST_BREAKOUT_EDM_NAME", "usfl_edm_small")
PORTFOLIO_ID = os.environ.get("IRP_TEST_BREAKOUT_PORTFOLIO_ID", "1")
# A source-id token the app never composes (the app uses the bare RM id), so
# the generated numbers live in their own namespace.
TEST_SOURCE_TOKEN = f"{PORTFOLIO_ID}T"
DESCRIPTION = "Risk Workbench spec-005 T061 sandbox test"


@pytest.fixture(scope="module")
def sandbox() -> dict:
    """Resolve the fixture EDM and its computed summary from the live sandbox;
    skip (never fail) when the sandbox is unreachable or the fixture is
    absent."""
    try:
        hits = irp_gateway.search_edms(EDM_NAME)
    except Exception as exc:  # noqa: BLE001 — no sandbox env → skip the tier
        pytest.skip(f"sandbox unreachable: {exc}")
    if not hits:
        pytest.skip(f"fixture EDM {EDM_NAME!r} not found in the sandbox")
    exposure_id = hits[0].irp_id
    summary = irp_gateway.get_edm_exposure_summary(
        edm_name=EDM_NAME, edm_irp_id=int(exposure_id))
    entry = summary.get(str(PORTFOLIO_ID))
    if not entry or not entry.get("breakout_values"):
        pytest.skip(f"portfolio {PORTFOLIO_ID} carries no breakout summary "
                    f"in {EDM_NAME!r}")
    return {"exposure_id": exposure_id, "summary": entry}


def _select(sandbox: dict, dimension: str,
            values: list[str]) -> irp_gateway.BreakoutSelection:
    return irp_gateway.select_breakout_accounts(
        edm_name=EDM_NAME, exposure_irp_id=sandbox["exposure_id"],
        source_portfolio_irp_id=str(PORTFOLIO_ID), dimension=dimension,
        values=values)


def _compose_or_adopt(sandbox: dict, *, name: str, number: str,
                      account_ids: list[int]) -> irp_gateway.SubPortfolioResult:
    """The worker's create-else-adopt sequence (R7/T-07), so re-runs of this
    suite heal instead of duplicating."""
    try:
        return irp_gateway.create_sub_portfolio(
            edm_name=EDM_NAME, exposure_irp_id=sandbox["exposure_id"],
            name=name, number=number, description=DESCRIPTION,
            account_ids=account_ids)
    except irp_gateway.DuplicatePortfolioNameError:
        hits = irp_gateway.find_portfolio_by_number(
            exposure_irp_id=sandbox["exposure_id"], number=number)
        assert len(hits) == 1, f"{len(hits)} portfolios carry number {number}"
        return irp_gateway.populate_sub_portfolio(
            edm_name=EDM_NAME, exposure_irp_id=sandbox["exposure_id"],
            portfolio_irp_id=hits[0].irp_id, account_ids=account_ids)


def test_selection_vocabulary_matches_the_stored_summary(sandbox):
    # The selection script mirrors the summary script's joins (R1 revised
    # 2026-08-05): filtering on the summary's own values must return exactly
    # the per-value account counts the summary reported — for BOTH dimensions.
    for dimension in ("lob", "state"):
        values = sandbox["summary"]["breakout_values"].get(dimension) or []
        if not values:
            continue
        selection = _select(sandbox, dimension, [v["value"] for v in values])
        assert selection.errors_by_value == {}
        for v in values:
            got = len(selection.accounts_by_value[v["value"]])
            assert got == v["accounts"], (
                f"{dimension} {v['value']!r}: selection returned {got} "
                f"accounts, the stored summary says {v['accounts']}")


def test_whole_account_bucketing_and_blank_value_gap(sandbox):
    # W-3/W-11: one matching policy/location admits the WHOLE account, so the
    # per-value lists overlap (Σ per-value ≥ distinct union) and together the
    # values cover at most the portfolio's account total — the shortfall is
    # the blank-value exposure the preview disclosure names (FR-007).
    values = sandbox["summary"]["breakout_values"]["lob"]
    selection = _select(sandbox, "lob", [v["value"] for v in values])
    per_value = list(selection.accounts_by_value.values())
    union = set().union(*per_value) if per_value else set()
    summed = sum(len(ids) for ids in per_value)
    assert summed >= len(union)
    total = sandbox["summary"].get("account_total")
    if total is not None:
        assert len(union) <= total


def test_create_add_readback_chunking_and_idempotent_readd(sandbox):
    # One sub-portfolio holding EVERY selected account of the source — with
    # the probe fixture that is 1,701 ids, so the add runs in two 1,000-id
    # chunks; success is the DataBridge read-back, never the add's
    # `completed` figure (W-9). The second populate re-adds every member and
    # must leave the count unchanged (idempotent re-add).
    #
    # THE ASSERTION THIS SUITE EXISTS FOR (T061). The composition PATCHes the
    # accounts over REST and then counts members with DataBridge SQL against
    # the EDM database — two different systems, and nothing has shown the
    # second sees the first's write immediately. W-1's read-back equality was
    # measured against the paginated REST enumeration, before W-20 moved the
    # read-back to a scalar count on 2026-08-05. `_compose_or_adopt` returning
    # at all IS that proof: the gateway raises when the count differs from the
    # ids sent, on the FIRST read, with no retry and no sleep anywhere in the
    # path. If this fails with a count below len(ids) while Risk Modeler shows
    # the portfolio fully populated, the EDM database lags the PATCH — record
    # the observed delay in probe-findings.md and add a bounded re-read to
    # irp_gateway._member_count. Every breakout entry depends on it: a lagging
    # read fails the sub-portfolio after its RM portfolio was created, and the
    # failure reads as a short add rather than as a stale count.
    values = sandbox["summary"]["breakout_values"]["lob"]
    selection = _select(sandbox, "lob", [v["value"] for v in values])
    ids = sorted(set().union(*selection.accounts_by_value.values()))
    assert ids, "fixture portfolio selected no accounts"

    result = _compose_or_adopt(sandbox, name="rwbt union",
                               number=f"P{TEST_SOURCE_TOKEN}-L-UNION",
                               account_ids=ids)
    assert result.account_count == len(ids)

    healed = irp_gateway.populate_sub_portfolio(
        edm_name=EDM_NAME, exposure_irp_id=sandbox["exposure_id"],
        portfolio_irp_id=result.portfolio_irp_id, account_ids=ids)
    assert healed.account_count == len(ids)


def test_name_and_number_limits_raise_client_side(sandbox):
    # Probe L-8: the wheel validates both limits client-side (server-side the
    # name caps unchecked at 40 and the number used to truncate silently).
    from irp_integration.exceptions import IRPValidationError

    with pytest.raises(IRPValidationError):
        irp_gateway.create_sub_portfolio(
            edm_name=EDM_NAME, exposure_irp_id=sandbox["exposure_id"],
            name="x" * (PORTFOLIO_NAME_MAX + 1), number="P1T-L-LIMIT",
            description=DESCRIPTION, account_ids=[1])
    with pytest.raises(IRPValidationError):
        irp_gateway.create_sub_portfolio(
            edm_name=EDM_NAME, exposure_irp_id=sandbox["exposure_id"],
            name="rwbt limit probe", number="N" * (PORTFOLIO_NUMBER_MAX + 1),
            description=DESCRIPTION, account_ids=[1])


def test_state_fanout_over_40_divisions_lands_every_entry(sandbox):
    # SC-003: a 40+ division state fan-out — one selection read, then the
    # per-entry compose loop; every entry gets an outcome, none is refused
    # for size. Names/numbers come from the real plan builder against the
    # rwbt namespace so re-runs adopt instead of duplicating.
    raw = sandbox["summary"]["breakout_values"].get("state") or []
    if len(raw) < 40:
        pytest.skip(f"fixture portfolio has {len(raw)} states; need 40+")
    values = [BreakoutValue(value=v["value"], label=v.get("label"),
                            accounts=v["accounts"]) for v in raw]
    plan = build_breakout_plan(
        source_name="rwbt state fan", source_portfolio_irp_id=TEST_SOURCE_TOKEN,
        dimension="state", values=values, existing_names=(),
        existing_values=())
    assert all(len(p.name) <= PORTFOLIO_NAME_MAX for p in plan)
    assert all(len(p.number) <= PORTFOLIO_NUMBER_MAX for p in plan)

    selection = _select(sandbox, "state", [p.value for p in plan])
    outcomes: dict[str, int] = {}
    for entry in plan:
        ids = selection.accounts_by_value.get(entry.value) or []
        assert ids, f"state {entry.value!r} selected no accounts"
        result = _compose_or_adopt(sandbox, name=entry.name, number=entry.number,
                                   account_ids=ids)
        outcomes[entry.value] = result.account_count
    assert len(outcomes) == len(plan) >= 40
    assert all(count > 0 for count in outcomes.values())
