"""Breakout service — the one testable home for the portfolio-breakout op (spec 005).

Owns, per contracts/data-access.md §1:
  • the prerequisite gate (``evaluate_gate`` — FR-002/FR-003, the Article 12
    named must-test),
  • the pure name/number plan builder (``build_breakout_plan`` — P-11/T-05),
  • the overlap statement (``compute_overlap`` — FR-007/P-13),
  • the confirm path (``request_breakout`` — seven ordered steps, no ``rwb_job``
    row until all seven pass),
  • the worker-side plan load + outcome assembly (``load_approved_plan`` /
    ``summarize_outcomes`` — R10/T-10).

All SQL through ``db.execute*`` (Article 7). Three reads run on the request
path: ``request_breakout``'s freshness read via
``irp_gateway.fetch_portfolio_stamp`` (the Article 2 submit-time pattern,
FR-002a), and the Add's two fail-open checks — the cached name search through
``name_check`` and the single-row DataBridge count (Article 11's request-path
exception, v3.2.0). Value enumeration reads the STORED spec-004 summary only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Collection, Sequence

from sqlalchemy import text

from app.services import irp_gateway, name_check, rwb_job_service
from app.services._common import _parse_json_dict, _uid, _utcnow
from app.services.name_check import CollisionCheck
from app.workers import dispatch
from db import execute, execute_one, get_connection, is_unique_violation

logger = logging.getLogger(__name__)

# Risk Modeler's hard limits, boundary-confirmed in the probe run (W-2/W-13).
PORTFOLIO_NAME_MAX = 40
PORTFOLIO_NUMBER_MAX = 20
# 4 characters reserved on every composed name so a " (2)"…" (9)" collision
# suffix always fits without a second truncation pass (R4).
_SUFFIX_RESERVE = 4
# The source name is never cut below this; a value long enough to demand that
# is itself truncated from the right (R4 — safe because the name is not the
# identity; the number is).
_MIN_SOURCE_CHARS = 4
_SEPARATOR = " - "

# Above this many sub-portfolios the preview adds the plain statement that the
# run takes several minutes (FR-006c / P-15). One named constant — no cap, no
# second gate.
LARGE_FANOUT_THRESHOLD = 25

# The dimension letter inside the generated portfolio_number (R4) — value
# dimensions only; a custom group's number is its name truncated to 20 (P-26).
_DIMENSION_LETTER = {"lob": "L", "state": "S", "country": "C", "peril": "P"}
# Analyst-facing noun per dimension for disabled-with-reason copy, and its
# plural for the chooser tile's count line — "line of business" and "country"
# both refuse a suffixed s, so the plural is maintained rather than derived.
_DIMENSION_NOUN = {"lob": "line of business", "state": "state",
                   "country": "country", "peril": "peril", "custom": "custom"}
_DIMENSION_NOUN_PLURAL = {"lob": "lines of business", "state": "states",
                          "country": "countries", "peril": "perils"}
# The peril dimension's values are `loccvg.PERIL` numeric codes, and neither the
# EDM nor Risk Modeler pairs a code with a name (W-21), so the mnemonics
# analysts read are maintained here (D4, closing O-02): 1 earthquake,
# 2 windstorm/hurricane, 3 severe convective storm/winterstorm, 4 flood,
# 5 fire, 6 terrorism, 7 workers compensation/human casualty. A code this map
# does not carry displays as itself rather than as a guessed mnemonic.
_PERIL_MNEMONIC = {"1": "EQ", "2": "WS", "3": "CS/WT", "4": "FL", "5": "FR",
                   "6": "TR", "7": "WC"}

MISSING_SUMMARY_REASON = "exposure summary not available — run Sync"
REFRESH_IN_FLIGHT_REASON = ("this EDM is syncing — the exposure summary is "
                            "being rewritten")


def display_value(value: str, dimension: str) -> str:
    """The analyst-facing text for one breakout value, registered as the
    ``breakout_display`` Jinja filter. Display only: the checkbox value, the
    stored ``breakout_group.filters``, the selection read, the stored
    ``breakout_value``, and the number token all stay the raw value."""
    if dimension == "peril":
        return _PERIL_MNEMONIC.get(value, value)
    return value


def _dimension_letter(dimension: str) -> str:
    try:
        return _DIMENSION_LETTER[dimension]
    except KeyError:
        raise ValueError(
            f"no portfolio_number letter registered for breakout dimension "
            f"{dimension!r} — add it to _DIMENSION_LETTER") from None


# ── Refusals (the router maps each to a 409 variant — http-routes.md) ───────────

class BreakoutRefused(Exception):
    """Base refusal: carries the analyst-facing reason. No ``rwb_job`` row has
    been created when this is raised."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class GateRefused(BreakoutRefused):
    """The prerequisite gate no longer passes (FR-002)."""


class SummaryRewritten(BreakoutRefused):
    """The stored summary's ``as_of`` no longer matches the one the preview
    carried — a Sync landed mid-preview (FR-002b). Fires even when the RM
    ``stampDate`` still matches."""


class StaleSummary(BreakoutRefused):
    """Risk Modeler moved under the stored summary — ``stampDate`` mismatch,
    missing stored stamp, or freshness unverifiable (FR-002a)."""


# ── Gate (Article 12 must-test) ──────────────────────────────────────────────────

@dataclass(frozen=True)
class BreakoutValue:
    value: str                # the selection filter value, verbatim: Admin1Code | LOB name (P-12)
    label: str | None         # Admin1Name where the EDM has it; None for lob and un-geocoded state
    accounts: int             # source accounts carrying this value (FR-007 numerator)


@dataclass(frozen=True)
class DimensionCoverage:
    """``summary.breakout_coverage[dimension]`` — the two counts the FR-007
    overlap statement is made of, both measured per account by the coverage
    scripts rather than derived from the per-value counts (which sum
    memberships: an account with three values adds three)."""
    covered: int              # source accounts carrying at least one value
    multi_value: int          # source accounts carrying MORE THAN ONE value


@dataclass(frozen=True)
class DimensionEligibility:
    dimension: str            # breakout_dimension_kind.code
    label: str                # breakout_dimension_kind.label (display)
    noun: str                 # analyst-facing noun for this dimension, resolved once
    noun_plural: str          # its plural, for the chooser tile's count line
    eligible: bool
    values: list[BreakoutValue]   # from the stored summary ([] when ineligible)
    reason: str | None        # analyst-facing disabled-with-reason copy


@dataclass(frozen=True)
class BreakoutGate:
    portfolio_eligible: bool  # EDM ready ∧ not deleted ∧ portfolio live ∧ no refresh in flight
    reason: str | None
    dimensions: list[DimensionEligibility]
    in_flight: str | None     # dimension code of a live run_breakout_* job, if any
    refresh_in_flight: bool   # a backfill_edm_detail for this EDM is pending|running (P-16)
    summary_as_of: str | None # the summary this preview renders from; echoed into the confirm (FR-002b)
    account_total: int | None = None  # summary.account_total — the modal header + overlap denominator (P-13)
    # summary.breakout_coverage per dimension code — the measured overlap
    # (FR-007). Empty for a summary written before the 2026-08-05 revision.
    coverage: dict[str, DimensionCoverage] = field(default_factory=dict)
    # The two rows the gate read, carried so no caller re-reads them: one
    # instant's view of the same rows the eligibility decision was made from,
    # which is what keeps a delete landing mid-request from producing a modal
    # built on a gate that says "portfolio not found".
    rows_live: bool = False    # EDM row and source portfolio row both exist, neither soft-deleted
    edm_irp_id: str | None = None      # irp_edm.irp_id (the RM exposureId)
    edm_name: str | None = None        # irp_edm.name — the DataBridge database lookup key
    source_name: str | None = None     # the source portfolio's name
    source_irp_id: str | None = None   # its RM portfolioId — None until the backfill writes one
    stored_stamp: str | None = None    # exposure_detail.stamp_date — the FR-002a anchor


def _as_of_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _load_rows(edm_id: Any, portfolio_id: Any) -> tuple[dict | None, dict | None]:
    edm = execute_one(
        "SELECT id, name, irp_id, status, deleted_at FROM irp_edm WHERE id = :e",
        {"e": str(edm_id)}, connection="WORKBENCH")
    portfolio = execute_one(
        "SELECT id, edm_id, name, irp_id, exposure_detail, as_of, deleted_at "
        "FROM irp_portfolio WHERE id = :p AND edm_id = :e",
        {"p": str(portfolio_id), "e": str(edm_id)}, connection="WORKBENCH")
    return edm, portfolio


def _parse_summary(exposure_detail_raw: Any) -> dict | None:
    detail = _parse_json_dict(exposure_detail_raw, "exposure_detail")
    if detail is None:
        return None
    summary = detail.get("summary")
    return summary if isinstance(summary, dict) else None


def _stored_stamp(exposure_detail_raw: Any) -> str | None:
    detail = _parse_json_dict(exposure_detail_raw, "exposure_detail")
    if detail is None:
        return None
    stamp = detail.get("stamp_date")
    return str(stamp) if stamp is not None else None


def _parse_breakout_values(summary: dict | None,
                           dimension: str) -> list[BreakoutValue] | None:
    """``None`` means absent — no summary, no ``breakout_values`` key, or a
    malformed container/entry. A present container whose dimension key is
    missing or empty reads as zero values, not absent."""
    if not isinstance(summary, dict):
        return None
    container = summary.get("breakout_values")
    if not isinstance(container, dict):
        return None
    raw = container.get(dimension)
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    seen: set[str] = set()
    values: list[BreakoutValue] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        value = entry.get("value")
        if not isinstance(value, str) or not value:
            return None
        if value in seen:
            continue
        seen.add(value)
        label = entry.get("label")
        accounts = entry.get("accounts")
        values.append(BreakoutValue(
            value=value,
            label=(label if isinstance(label, str) and label else None),
            accounts=(int(accounts) if isinstance(accounts, (int, float))
                      else 0)))
    return sorted(values, key=lambda v: v.value)


def _live_breakout_dimension(portfolio_id: Any) -> str | None:
    """The dimension code of a pending|running breakout job for this portfolio
    — quick (``analyst_request`` keyed on the portfolio) or custom
    (``breakout_group`` keyed on a group row whose source is the portfolio) —
    or ``None``. One breakout episode per portfolio, either direction
    (FR-020): a live cart blocks a quick-mode confirm and vice versa."""
    row = execute_one(
        "SELECT rj.rwb_job_type FROM rwb_job rj "
        "LEFT JOIN breakout_group bg ON rj.requestor_type = 'breakout_group' "
        "AND rj.requestor_id = bg.id "
        "WHERE rj.rwb_job_type LIKE 'run_breakout_%' "
        "AND rj.status_code IN ('pending', 'running') "
        "AND ((rj.requestor_type = 'analyst_request' AND rj.requestor_id = :p) "
        "     OR bg.source_portfolio_id = :p)",
        {"p": str(portfolio_id)}, connection="WORKBENCH")
    if row is None:
        return None
    return str(row["rwb_job_type"]).removeprefix("run_breakout_")


def _backfill_in_flight(edm_id: Any) -> bool:
    """True while a ``backfill_edm_detail`` for this EDM is pending|running
    under any of its three enqueue keys — ``rwb_job_service.
    backfill_edm_detail_rows`` owns the membership predicate — the same
    condition ``edm_service.sync_detail`` applies to itself (P-16)."""
    return bool(rwb_job_service.backfill_edm_detail_rows(
        [edm_id], statuses=("pending", "running")))


def _dimension_rows() -> list[dict]:
    return execute(
        "SELECT code, label FROM breakout_dimension_kind ORDER BY sort_order",
        {}, connection="WORKBENCH")


def evaluate_gate(edm_id: Any, portfolio_id: Any) -> BreakoutGate:
    """The prerequisite gate, computed per request from entity state alone
    (Article 2 — never cached, never stored). Rule (R5): EDM exists ∧ not
    deleted ∧ status 'ready' ∧ portfolio live ∧ no ``backfill_edm_detail``
    pending|running for the EDM; per dimension: the stored summary carries
    ``breakout_values[dimension]`` with ≥ 2 distinct values."""
    edm, portfolio = _load_rows(edm_id, portfolio_id)

    reason: str | None = None
    if edm is None or edm["deleted_at"] is not None:
        reason = "EDM not found"
    elif portfolio is None or portfolio["deleted_at"] is not None:
        reason = "portfolio not found"
    elif edm["status"] != "ready":
        reason = "the EDM is not ready"

    refresh_in_flight = (_backfill_in_flight(edm_id)
                         if reason is None else False)
    if reason is None and refresh_in_flight:
        reason = REFRESH_IN_FLIGHT_REASON

    portfolio_eligible = reason is None
    summary = (_parse_summary(portfolio["exposure_detail"])
               if portfolio is not None and portfolio["deleted_at"] is None
               else None)
    summary_as_of = (_as_of_str(portfolio["as_of"])
                     if portfolio is not None else None)
    in_flight = (_live_breakout_dimension(portfolio_id)
                 if portfolio is not None else None)

    dimensions: list[DimensionEligibility] = []
    for kind in _dimension_rows():
        code, label = str(kind["code"]), str(kind["label"])
        if code == "custom":
            # The grouping pane's lineage code (T-12) — an FK target and a
            # display label, never a value dimension the summary enumerates.
            continue
        noun = _DIMENSION_NOUN.get(code, label.lower())
        plural = _DIMENSION_NOUN_PLURAL.get(code, f"{noun} values")
        values = _parse_breakout_values(summary, code)
        if values is None:
            dimensions.append(DimensionEligibility(
                dimension=code, label=label, noun=noun, noun_plural=plural,
                eligible=False, values=[], reason=MISSING_SUMMARY_REASON))
        elif len(values) < 2:
            dim_reason = (f"only one {noun} present" if len(values) == 1
                          else f"no {noun} values present")
            dimensions.append(DimensionEligibility(
                dimension=code, label=label, noun=noun, noun_plural=plural,
                eligible=False, values=values, reason=dim_reason))
        else:
            dimensions.append(DimensionEligibility(
                dimension=code, label=label, noun=noun, noun_plural=plural,
                eligible=portfolio_eligible, values=values, reason=None))

    account_total = None
    if isinstance(summary, dict):
        raw_total = summary.get("account_total")
        if isinstance(raw_total, (int, float)):
            account_total = int(raw_total)

    rows_live = (edm is not None and edm["deleted_at"] is None
                 and portfolio is not None and portfolio["deleted_at"] is None)
    return BreakoutGate(
        portfolio_eligible=portfolio_eligible, reason=reason,
        dimensions=dimensions, in_flight=in_flight,
        refresh_in_flight=refresh_in_flight, summary_as_of=summary_as_of,
        account_total=account_total, coverage=_parse_coverage(summary),
        rows_live=rows_live,
        edm_irp_id=(str(edm["irp_id"]) if rows_live and edm["irp_id"] is not None
                    else None),
        edm_name=(str(edm["name"]) if rows_live else None),
        source_name=(str(portfolio["name"]) if rows_live else None),
        source_irp_id=(str(portfolio["irp_id"])
                       if rows_live and portfolio["irp_id"] is not None else None),
        stored_stamp=(_stored_stamp(portfolio["exposure_detail"])
                      if rows_live else None))


def _parse_coverage(summary: dict | None) -> dict[str, DimensionCoverage]:
    """``summary.breakout_coverage`` per dimension code, parsed as defensively
    as the rest of the summary. A summary written before the 2026-08-05 FR-007
    revision has no such key: the dimension is simply absent from the result and
    the preview degrades to the qualitative disclosure (data-model §6)."""
    raw = summary.get("breakout_coverage") if isinstance(summary, dict) else None
    if not isinstance(raw, dict):
        return {}
    coverage: dict[str, DimensionCoverage] = {}
    for code, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        covered, multi = entry.get("covered"), entry.get("multi_value")
        if not (isinstance(covered, (int, float))
                and isinstance(multi, (int, float))):
            continue
        coverage[str(code)] = DimensionCoverage(covered=int(covered),
                                               multi_value=int(multi))
    return coverage


# ── Approved plan (pure function — the preview and the persisted plan are the
#    same list, R4/R10) ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SubPortfolioPlan:
    value: str                # selection filter value; stored as breakout_value
    label: str | None         # display only, never a filter input
    name: str                 # ≤ 40 chars: source truncated, label-or-value token whole, collision-suffixed (P-11/P-12 rev. 2026-08-05)
    number: str               # ≤ 20 chars: P{source RM id}-{S|L}-{token}, hash-tailed when long
    accounts: int             # previewed count from the summary (FR-006)
    exists: bool              # a live lineage row already matches (idempotent re-run view)


def _compose_name(source_name: str, token: str, taken: Collection[str]) -> str:
    """``{source} - {token}`` inside the 40-character limit — the token is the
    breakout value's display label where one exists, else the value (P-12 as
    revised 2026-08-05). The token is kept whole and the source absorbs the
    truncation, 4 characters reserved for the collision suffix; the lowest
    free `` (2)``, `` (3)``… wins (R4). ``taken`` holds CASEFOLDED names —
    Risk Modeler rejects a duplicate name without distinguishing case, so
    ``SOURCE - TX`` must push ``source - TX`` to a suffix."""
    source_budget = (PORTFOLIO_NAME_MAX - _SUFFIX_RESERVE - len(_SEPARATOR)
                     - len(token))
    if source_budget < _MIN_SOURCE_CHARS:
        source_part = source_name[:_MIN_SOURCE_CHARS].rstrip()
        value_budget = (PORTFOLIO_NAME_MAX - _SUFFIX_RESERVE - len(_SEPARATOR)
                        - len(source_part))
        value_part = token[:value_budget].rstrip()
    else:
        source_part = source_name[:source_budget].rstrip()
        value_part = token
    base = f"{source_part}{_SEPARATOR}{value_part}"
    name = base
    n = 2
    while name.casefold() in taken:
        name = f"{base} ({n})"
        # Beyond " (9)" the suffix outgrows the 4-character reserve — trim the
        # source further so the composed name never exceeds the RM limit.
        while len(name) > PORTFOLIO_NAME_MAX and len(source_part) > 1:
            source_part = source_part[:-1].rstrip()
            base = f"{source_part}{_SEPARATOR}{value_part}"
            name = f"{base} ({n})"
        n += 1
    return name


def _compose_number(source_portfolio_irp_id: str, dimension: str,
                    value: str) -> str:
    """``P{source RM id}-{S|L}-{token}`` inside 20 characters. The token is the
    value itself when it is already uppercase alphanumerics that fit the budget;
    otherwise its last 6 characters are 6 hex digits of sha256(value). Both
    normalizing steps — dropping non-alphanumerics and uppercasing — map distinct
    values onto one token (``A-B``, ``AB``, ``a b``, and ``ab`` all become
    ``AB``), and the number is the identity adoption resolves on, so a value the
    token cannot carry verbatim is hashed rather than truncated into a
    neighbour's number (R4/FR-011). Never Python's hash() — salted per
    process."""
    prefix = f"P{source_portfolio_irp_id}-{_dimension_letter(dimension)}-"
    budget = PORTFOLIO_NUMBER_MAX - len(prefix)
    if budget < 1:
        raise ValueError(
            f"portfolio_number prefix {prefix!r} leaves no room for a value "
            f"token inside {PORTFOLIO_NUMBER_MAX} characters")
    token = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    if token and token == value and len(token) <= budget:
        return f"{prefix}{token}"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest().upper()[:6]
    return f"{prefix}{(token[:max(budget - 6, 0)] + digest)[:budget]}"


def build_breakout_plan(*, source_name: str, source_portfolio_irp_id: str,
                        dimension: str, values: Sequence[BreakoutValue],
                        existing_names: Collection[str],
                        existing_values: Collection[str],
                        ) -> list[SubPortfolioPlan]:
    """Deterministic, no I/O: same inputs → same plan, sorted by value. Callers
    supply the current portfolio names (collision universe) and the existing
    breakout values (live lineage rows → ``exists``)."""
    taken: set[str] = {n.casefold() for n in existing_names}
    already = set(existing_values)
    plan: list[SubPortfolioPlan] = []
    for v in sorted(values, key=lambda bv: bv.value):
        # The NAME token is the display label when the summary carries one
        # (P-12 as revised 2026-08-05 — "cbhu - Puerto Rico", never
        # "cbhu - 200"), else the value's display — the peril mnemonic
        # (P-30 — "cbhu - WS", never "cbhu - 2"). The value itself stays the
        # filter, the stored breakout_value, and the number token, and is
        # carried as the entry's label so the preview row and the Risk Modeler
        # description show both.
        shown = v.label or display_value(v.value, dimension)
        name = _compose_name(source_name, shown, taken)
        taken.add(name.casefold())
        plan.append(SubPortfolioPlan(
            value=v.value, label=(shown if shown != v.value else None),
            name=name,
            number=_compose_number(source_portfolio_irp_id, dimension, v.value),
            accounts=v.accounts, exists=(v.value in already)))
    return plan


# ── Overlap statement (FR-007 / P-13) ────────────────────────────────────────────

@dataclass(frozen=True)
class Overlap:
    account_total: int | None   # summary.account_total (the denominator)
    summed: int                 # Σ accounts over the dimension's values — MEMBERSHIPS, not accounts
    covered: int | None         # source accounts landing in ≥ 1 sub-portfolio (SC-002)
    uncovered: int | None       # account_total − covered: accounts landing in none (FR-007b)
    repeats: int | None         # source accounts landing in MORE THAN ONE sub-portfolio (FR-007a)
    partition: bool             # no repeats AND no uncovered accounts — a clean partition


def compute_overlap(values: Sequence[BreakoutValue],
                    account_total: int | None,
                    coverage: DimensionCoverage | None = None) -> Overlap:
    """The two FR-007 figures, read from the per-account coverage the summary
    measured — never derived from ``summed``, which is the membership total
    (R11). Absent coverage yields ``repeats=None`` and the preview degrades to
    the qualitative disclosure alone."""
    summed = sum(v.accounts for v in values)
    if coverage is None:
        return Overlap(account_total=account_total, summed=summed, covered=None,
                       uncovered=None, repeats=None, partition=False)
    uncovered = (max(account_total - coverage.covered, 0)
                 if account_total is not None else None)
    return Overlap(account_total=account_total, summed=summed,
                   covered=coverage.covered, uncovered=uncovered,
                   repeats=coverage.multi_value,
                   partition=(coverage.multi_value == 0 and uncovered == 0))


# ── Modal read model (GET /edms/{e}/portfolios/{p}/breakout — http-routes.md) ────

@dataclass(frozen=True)
class BreakoutModal:
    """Everything ``partials/breakout_modal.html`` renders for one snapshot of
    the stored summary. ``dimension`` is the selected ELIGIBLE dimension (the
    request's, else the first eligible), ``None`` when none is selectable —
    the template then shows only the disabled chooser and its reasons."""
    gate: BreakoutGate
    portfolio_name: str
    portfolio_irp_id: str | None
    dimension: str | None
    noun: str | None          # analyst-facing noun for the selected dimension
    plan: list[SubPortfolioPlan]
    overlap: Overlap | None


def modal_context(edm_id: Any, portfolio_id: Any,
                  dimension: str | None = None) -> BreakoutModal | None:
    """The modal GET's single composition: gate + plan + overlap, all from the
    STORED summary — zero Risk Modeler or DataBridge calls (Article 11).
    ``None`` when the EDM or portfolio is missing/deleted (router → 404
    fragment)."""
    gate = evaluate_gate(edm_id, portfolio_id)
    if not gate.rows_live:
        return None

    eligible = [d.dimension for d in gate.dimensions if d.eligible]
    selected = (dimension if dimension in eligible
                else (eligible[0] if eligible else None))
    # A portfolio without its RM id cannot compose portfolio numbers; in
    # practice it also has no summary (both come from the same backfill), so
    # this guard only closes the theoretical gap.
    if gate.source_irp_id is None:
        selected = None

    plan: list[SubPortfolioPlan] = []
    overlap: Overlap | None = None
    if selected is not None:
        plan = compose_plan(gate, edm_id=edm_id, portfolio_id=portfolio_id,
                            source_name=gate.source_name,
                            source_portfolio_irp_id=gate.source_irp_id,
                            dimension=selected)
        values = next(d.values for d in gate.dimensions
                      if d.dimension == selected)
        overlap = compute_overlap(values, gate.account_total,
                                  gate.coverage.get(selected))

    return BreakoutModal(
        gate=gate, portfolio_name=gate.source_name or "",
        portfolio_irp_id=gate.source_irp_id, dimension=selected,
        # The noun the gate already resolved for that dimension — read here
        # rather than looked up a second time, so the modal cannot disagree with
        # the disabled-with-reason copy or render "more than one None".
        noun=next((d.noun for d in gate.dimensions if d.dimension == selected),
                  None),
        plan=plan, overlap=overlap)


# ── Page read model (edm_detail_body.html — in-flight, banner, error lines) ──────

@dataclass(frozen=True)
class BreakoutFlight:
    """A live ``run_breakout_*`` job on one portfolio — the row's in-flight
    indicator. ``done`` counts the plan's values that now have a live generated
    row (the worker upserts per entry, so it advances every poll)."""
    dimension: str
    noun: str
    planned: int
    done: int


@dataclass(frozen=True)
class BreakoutRowError:
    """One failed entry of the LATEST terminal breakout job for a (portfolio,
    dimension) — the durable per-row error line (FR-012): survives refresh,
    carries no dismissal state, superseded only by the next terminal run.
    ``value``/``name`` are empty when the job failed before its loop (plan
    unusable, selection read failed) — the line then shows the job error."""
    dimension: str
    noun: str
    value: str
    name: str
    error: str


@dataclass(frozen=True)
class BreakoutBanner:
    """The completion banner for the newest terminal breakout job on the EDM.
    Visible while ``filling_in`` (its FR-013 follow-up ``backfill_edm_detail``
    is still pending|running — "figures are filling in") or while it carries
    failures; a fully-successful run's banner disappears once the follow-up
    backfill lands, a failed/partial one only when the next terminal run
    supersedes it."""
    source_name: str
    noun: str
    created: int
    adopted: int
    skipped_existing: int
    failed: int
    ok: bool                  # succeeded with zero failures
    filling_in: bool
    error: str | None         # rwb_job.error_detail when the job itself failed


@dataclass(frozen=True)
class BreakoutPageState:
    running: bool                                # keeps the 3s self-poll alive
    banner: BreakoutBanner | None
    flights: dict[str, BreakoutFlight]           # portfolio id → live run
    errors: dict[str, list[BreakoutRowError]]    # portfolio id → durable lines


def _noun_for_job_type(rwb_job_type: str) -> tuple[str, str]:
    # Display only, and read off a job row that may name a dimension this build
    # no longer carries a noun for — so the code stands in rather than raising a
    # completed run's banner off the page.
    code = str(rwb_job_type).removeprefix("run_breakout_")
    return code, _DIMENSION_NOUN.get(code, code)


def _count(output: dict, key: str) -> int:
    value = output.get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def _plan_values(input_data_raw: Any) -> list[str]:
    data = _parse_json_dict(input_data_raw, "input_data") or {}
    raw = data.get("plan")
    if not isinstance(raw, list):
        return []
    return [e["value"] for e in raw
            if isinstance(e, dict) and isinstance(e.get("value"), str)]


def page_state(edm_id: Any) -> BreakoutPageState:
    """The EDM body's breakout read model, attached by
    ``edm_service.get_edm_detail`` — pure WORKBENCH reads.

    The queries read every breakout job of the EDM, which stays bounded:
    ``UNIQUE(requestor_type, requestor_id, rwb_job_type)`` holds one row per
    (portfolio, quick dimension) and one per custom group, and
    ``ensure_pending_rwb_job`` revives them, so re-runs reuse rows rather than
    adding them. Every terminal row is needed — FR-012 renders its failed
    entries on that portfolio's row until the next terminal run supersedes
    them, which also rules out bounding the read by age. Custom-group jobs
    reach their source portfolio through ``breakout_group.source_portfolio_id``
    (FR-015 as amended); a live cart renders one flight per portfolio
    ("custom breakouts: k of n done") and terminal jobs sharing the newest
    ``cart_id`` aggregate into one banner (FR-020)."""
    live = execute(
        "SELECT rj.requestor_id, rj.rwb_job_type, rj.input_data "
        "FROM rwb_job rj JOIN irp_portfolio p ON rj.requestor_id = p.id "
        "WHERE p.edm_id = :e AND rj.requestor_type = 'analyst_request' "
        "AND rj.rwb_job_type LIKE 'run_breakout_%' "
        "AND rj.status_code IN ('pending', 'running')",
        {"e": str(edm_id)}, connection="WORKBENCH")
    flights: dict[str, BreakoutFlight] = {}
    for row in live:
        pid = _uid(row["requestor_id"])
        code, noun = _noun_for_job_type(row["rwb_job_type"])
        values = _plan_values(row["input_data"])
        done = len(set(values) & _existing_breakout_values(pid, code))
        flights[pid] = BreakoutFlight(dimension=code, noun=noun,
                                      planned=len(values), done=done)

    live_custom = execute(
        "SELECT bg.source_portfolio_id AS pid, bg.group_key, rj.input_data "
        "FROM rwb_job rj "
        "JOIN breakout_group bg ON rj.requestor_id = bg.id "
        "JOIN irp_portfolio p ON bg.source_portfolio_id = p.id "
        "WHERE p.edm_id = :e AND rj.requestor_type = 'breakout_group' "
        "AND rj.rwb_job_type = 'run_breakout_custom' "
        "AND rj.status_code IN ('pending', 'running')",
        {"e": str(edm_id)}, connection="WORKBENCH")
    terminal_custom = execute(
        "SELECT rj.id, rj.status_code, rj.output_data, rj.error_detail, "
        "rj.input_data, rj.updated_at, bg.source_portfolio_id AS pid, "
        "bg.group_key, p.name AS source_name "
        "FROM rwb_job rj "
        "JOIN breakout_group bg ON rj.requestor_id = bg.id "
        "JOIN irp_portfolio p ON bg.source_portfolio_id = p.id "
        "WHERE p.edm_id = :e AND rj.requestor_type = 'breakout_group' "
        "AND rj.rwb_job_type = 'run_breakout_custom' "
        "AND rj.status_code IN ('succeeded', 'failed') "
        "ORDER BY rj.updated_at DESC, rj.id",
        {"e": str(edm_id)}, connection="WORKBENCH")

    live_by_pid: dict[str, list] = {}
    for row in live_custom:
        live_by_pid.setdefault(_uid(row["pid"]), []).append(row)
    for pid, live_rows in live_by_pid.items():
        # The episode is the cart (FR-020): "custom breakouts: k of n done" counts
        # every group of the live jobs' cart — the already-completed ones
        # included — with done = the groups whose live lineage row exists, so
        # the counter advances every poll like the quick flight's.
        carts = {_cart_id_of(r) for r in live_rows}
        keys = {str(r["group_key"]) for r in live_rows}
        keys.update(str(r["group_key"]) for r in terminal_custom
                    if _uid(r["pid"]) == pid and _cart_id_of(r) in carts)
        done = len(keys & _existing_breakout_values(pid, "custom"))
        flights[pid] = BreakoutFlight(dimension="custom",
                                      noun=_DIMENSION_NOUN["custom"],
                                      planned=len(keys), done=done)

    terminal = execute(
        "SELECT rj.id, rj.requestor_id, rj.rwb_job_type, rj.status_code, "
        "rj.output_data, rj.error_detail, rj.updated_at, "
        "p.name AS source_name "
        "FROM rwb_job rj JOIN irp_portfolio p ON rj.requestor_id = p.id "
        "WHERE p.edm_id = :e AND rj.requestor_type = 'analyst_request' "
        "AND rj.rwb_job_type LIKE 'run_breakout_%' "
        "AND rj.status_code IN ('succeeded', 'failed') "
        "ORDER BY rj.updated_at DESC, rj.id",
        {"e": str(edm_id)}, connection="WORKBENCH")

    errors: dict[str, list[BreakoutRowError]] = {}
    for row in terminal:
        code, noun = _noun_for_job_type(row["rwb_job_type"])
        _collect_error_lines(errors, _uid(row["requestor_id"]), code, noun, row)
    for row in terminal_custom:
        _collect_error_lines(errors, _uid(row["pid"]), "custom",
                             _DIMENSION_NOUN["custom"], row)

    return BreakoutPageState(running=bool(flights),
                             banner=_newest_banner(terminal, terminal_custom),
                             flights=flights, errors=errors)


def _cart_id_of(row) -> str | None:
    return (_parse_json_dict(row["input_data"], "input_data") or {}).get(
        "cart_id")


def _collect_error_lines(errors: dict[str, list[BreakoutRowError]], pid: str,
                         code: str, noun: str, row) -> None:
    """The FR-012 durable lines of one terminal job row — its failed entries,
    or the job error when it died before producing any."""
    output = _parse_json_dict(row["output_data"], "output_data") or {}
    lines: list[BreakoutRowError] = []
    for entry in (output.get("sub_portfolios") or []):
        if isinstance(entry, dict) and entry.get("outcome") == "failed":
            lines.append(BreakoutRowError(
                dimension=code, noun=noun,
                value=str(entry.get("value") or ""),
                name=str(entry.get("name") or ""),
                error=str(entry.get("error") or "failed")))
    if not lines and row["status_code"] == "failed":
        lines.append(BreakoutRowError(
            dimension=code, noun=noun, value="", name="",
            error=str(row["error_detail"] or "the breakout run failed")))
    if lines:
        errors.setdefault(pid, []).extend(lines)


def _follow_up_pending(job_ids: Sequence[str]) -> bool:
    """True while any of the jobs' FR-013 follow-up ``backfill_edm_detail``
    heads is pending|running — the banner's "figures are filling in"."""
    for jid in job_ids:
        follow_up = execute_one(
            "SELECT status_code FROM rwb_job "
            "WHERE requestor_type = 'rwb_job' AND requestor_id = :j "
            "AND rwb_job_type = 'backfill_edm_detail' "
            "ORDER BY updated_at DESC",
            {"j": str(jid)}, connection="WORKBENCH")
        if (follow_up is not None
                and follow_up["status_code"] in ("pending", "running")):
            return True
    return False


def _newest_banner(terminal: Sequence, terminal_custom: Sequence,
                   ) -> BreakoutBanner | None:
    """The newest terminal EPISODE's summary, or nothing at all: a quick run
    is one job row; a cart is every terminal job sharing the newest custom
    job's ``cart_id``, aggregated (FR-020). Both lists arrive newest-first."""
    quick = terminal[0] if terminal else None
    custom = terminal_custom[0] if terminal_custom else None
    if quick is None and custom is None:
        return None
    if custom is None or (quick is not None
                          and quick["updated_at"] >= custom["updated_at"]):
        code, noun = _noun_for_job_type(quick["rwb_job_type"])
        return _banner_over([quick], noun=noun,
                            source_name=str(quick["source_name"]))
    cart_id = _cart_id_of(custom)
    rows = ([r for r in terminal_custom if _cart_id_of(r) == cart_id]
            if cart_id else [custom])
    return _banner_over(rows, noun=_DIMENSION_NOUN["custom"],
                        source_name=str(custom["source_name"]))


def _banner_over(rows: Sequence, *, noun: str,
                 source_name: str) -> BreakoutBanner | None:
    counts = {"created": 0, "adopted": 0, "skipped_existing": 0, "failed": 0}
    error: str | None = None
    any_job_failed = False
    for r in rows:
        output = _parse_json_dict(r["output_data"], "output_data") or {}
        for key in counts:
            counts[key] += _count(output, key)
        if r["status_code"] == "failed":
            any_job_failed = True
            if error is None and r["error_detail"]:
                error = str(r["error_detail"])
    filling_in = _follow_up_pending([str(r["id"]) for r in rows])
    if not (filling_in or counts["failed"] or any_job_failed):
        return None
    return BreakoutBanner(
        source_name=source_name, noun=noun, created=counts["created"],
        adopted=counts["adopted"], skipped_existing=counts["skipped_existing"],
        failed=counts["failed"], ok=(not any_job_failed
                                     and counts["failed"] == 0),
        filling_in=filling_in, error=error)


# ── Confirm (the POST path — five ordered steps, no rwb_job row until all pass) ──

def _existing_breakout_values(portfolio_id: Any, dimension: str) -> set[str]:
    rows = execute(
        "SELECT breakout_value FROM irp_portfolio "
        "WHERE source_portfolio_id = :s AND breakout_dimension_code = :d "
        "AND deleted_at IS NULL",
        {"s": str(portfolio_id), "d": dimension}, connection="WORKBENCH")
    return {r["breakout_value"] for r in rows if r["breakout_value"]}


def _live_portfolio_names(edm_id: Any) -> set[str]:
    rows = execute(
        "SELECT name FROM irp_portfolio WHERE edm_id = :e AND deleted_at IS NULL",
        {"e": str(edm_id)}, connection="WORKBENCH")
    return {r["name"] for r in rows}


def compose_plan(gate: BreakoutGate, *, edm_id: Any, portfolio_id: Any,
                 source_name: str, source_portfolio_irp_id: str,
                 dimension: str) -> list[SubPortfolioPlan]:
    """The preview's plan — the same builder call the confirm persists (FR-006b),
    fed from the gate's stored-summary values plus the live name/lineage reads."""
    eligibility = next((d for d in gate.dimensions if d.dimension == dimension),
                       None)
    values = eligibility.values if eligibility is not None else []
    return build_breakout_plan(
        source_name=source_name,
        source_portfolio_irp_id=source_portfolio_irp_id,
        dimension=dimension, values=values,
        existing_names=_live_portfolio_names(edm_id),
        existing_values=_existing_breakout_values(portfolio_id, dimension))


def _verify_freshness(gate: BreakoutGate, portfolio_id: Any) -> None:
    """The FR-002a freshness check — the flow's one web-layer RM call, shared
    by the quick confirm and the cart confirm (once per cart). Raises
    ``StaleSummary`` on a stamp mismatch, a missing stored stamp, or an
    unverifiable read; returns silently when Risk Modeler has not moved."""
    if gate.stored_stamp is None:
        raise StaleSummary(
            "Portfolio data has changed in Risk Modeler since the last sync — "
            "Sync the EDM, then retry.")
    if gate.edm_irp_id is None or gate.source_irp_id is None:
        raise StaleSummary("couldn't verify freshness — Sync the EDM, then retry.")
    try:
        current_stamp = irp_gateway.fetch_portfolio_stamp(
            exposure_irp_id=gate.edm_irp_id,
            portfolio_irp_id=gate.source_irp_id)
    except Exception as exc:  # noqa: BLE001 — unverifiable freshness refuses, never proceeds
        logger.warning("breakout freshness read failed for portfolio %s: %s",
                       portfolio_id, exc)
        raise StaleSummary(
            "couldn't verify freshness — try again or Sync the EDM.") from exc
    if current_stamp is None or str(current_stamp) != gate.stored_stamp:
        raise StaleSummary(
            "Portfolio data has changed in Risk Modeler since the last sync — "
            "Sync the EDM, then retry.")


@dataclass(frozen=True)
class BreakoutRequested:
    """A confirmed quick breakout: the enqueued job and its plan size — the
    router's toast reads the count from here, never back off the job row."""
    job_id: str
    planned: int


def _confirm_preconditions(edm_id: Any, portfolio_id: Any,
                           summary_as_of: str | None,
                           shape_check) -> BreakoutGate | None:
    """The ordered refusals both confirms share, and **no rwb_job row exists
    until all pass** (contracts/data-access.md §1): gate re-check → in-flight →
    the caller's ``shape_check(gate)`` (quick: the dimension is eligible; cart:
    not empty) → summary-unchanged (FR-002b) → freshness (FR-002a). Returns the
    gate the caller composes its plan from — every row value it reads was
    loaded by the gate re-check, so the confirm decides from one instant's view
    of the two rows — or ``None`` when a breakout episode is already live on
    the portfolio (the caller's "already running" 409)."""
    gate = evaluate_gate(edm_id, portfolio_id)
    if not gate.portfolio_eligible:
        raise GateRefused(gate.reason or "breakout is not available")
    if gate.in_flight is not None:
        return None
    shape_check(gate)

    # Summary-unchanged (FR-002b): the stored summary must be the one the
    # preview rendered from. A detail refresh that landed mid-preview changes
    # the value set the analyst judged from, and FR-002a cannot see it (an
    # untouched RM portfolio writes back an equal stampDate).
    if gate.summary_as_of != (str(summary_as_of) if summary_as_of else None):
        raise SummaryRewritten(
            "This EDM was synced while you were reviewing — here is the "
            "current breakout.")

    # Freshness (FR-002a): the flow's one web-layer RM call.
    _verify_freshness(gate, portfolio_id)
    return gate


def request_breakout(edm_id: Any, portfolio_id: Any, dimension: str,
                     summary_as_of: str | None,
                     actor_id: Any) -> BreakoutRequested | None:
    """The quick confirm: the shared preconditions
    (``_confirm_preconditions``) with the dimension-eligibility shape check,
    then build and persist the approved plan and enqueue idempotently. Returns
    the job id with the plan size, or ``None`` when a live job already exists
    (UI: "already running")."""
    def _dimension_eligible(gate: BreakoutGate) -> None:
        eligibility = next(
            (d for d in gate.dimensions if d.dimension == dimension), None)
        if eligibility is None:
            raise GateRefused(f"unknown breakout dimension {dimension!r}")
        if not eligibility.eligible:
            raise GateRefused(eligibility.reason or "dimension is not eligible")

    gate = _confirm_preconditions(edm_id, portfolio_id, summary_as_of,
                                  _dimension_eligible)
    if gate is None:
        return None  # already running — the router renders the 409 variant

    # Build and persist the approved plan — composed ONCE, here; from this
    # point the plan is authoritative and the worker executes it verbatim
    # (AGENTS.md rule 8 / R10 / P-14).
    plan = compose_plan(gate, edm_id=edm_id, portfolio_id=portfolio_id,
                        source_name=gate.source_name,
                        source_portfolio_irp_id=gate.source_irp_id,
                        dimension=dimension)
    input_data = {
        "edm_id": str(edm_id), "portfolio_id": str(portfolio_id),
        "dimension": dimension, "actor_id": str(actor_id),
        "plan": [{"value": p.value, "label": p.label, "name": p.name,
                  "number": p.number, "accounts": p.accounts} for p in plan],
    }

    # Idempotent enqueue — one live-job slot per (portfolio, dimension).
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="analyst_request", requestor_id=str(portfolio_id),
        rwb_job_type=f"run_breakout_{dimension}", input_data=input_data,
        actor_id=str(actor_id))
    if job_id is None:
        return None
    # Business-event log (FR-015/P-08).
    logger.info("breakout %s requested for portfolio %s by analyst %s "
                "(n_sub_portfolios=%d)", dimension, portfolio_id, actor_id,
                len(plan))
    dispatch.dispatch(rwb_job_id=job_id,
                      rwb_job_type=f"run_breakout_{dimension}")
    return BreakoutRequested(job_id=job_id, planned=len(plan))


# ── Custom grouping (follow-on FR-018–021, T-12/T-13) ────────────────────────────
# A group is a named member set: selected values per dimension, OR within a
# dimension and AND across dimensions (P-20). Its identity is the canonical
# member-set hash (P-22) — the label decorates, the key identifies. Each group
# in a confirmed cart becomes one breakout_group row and one run_breakout_custom
# rwb_job keyed on the row's UUID (T-13).

def compute_group_key(filters: dict[str, list[str]]) -> str:
    """The canonical member-set hash (P-22/T-12): dimensions and values sorted,
    values deduped — the same members always hash to the same key, so a
    re-confirm adopts the existing group instead of renaming or duplicating."""
    canonical = {dim: sorted(set(values)) for dim, values in filters.items()}
    return hashlib.sha256(json.dumps(
        canonical, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class GroupPlan:
    """One cart row, composed server-side — the group-preview POST and the
    cart confirm build the same object (preview-confirm intact)."""
    label: str                        # as the analyst typed
    filters: dict[str, list[str]]     # canonical: dimensions/values sorted, deduped
    key: str                          # compute_group_key(filters)
    name: str                         # ≤ 40: the label as typed (P-24)
    number: str                       # ≤ 20: the name truncated (P-26)
    accounts_upper_bound: int         # "up to N accounts" (P-23)
    exists: bool                      # a live lineage row already matches the key
    adopted: bool                     # a breakout_group row already carries this member set
    may_overlap_with: list[str] = field(default_factory=list)  # earlier cart rows sharing a value


def _validate_group_filters(gate: BreakoutGate, filters: Any,
                            ) -> dict[str, list[str]]:
    """Server-side validation of client-posted member filters (FR-018): every
    dimension is a known, eligible value dimension of the stored summary and
    every value exists in it — client JSON is never trusted. Returns the
    canonical (sorted, deduped) filter dict."""
    if not isinstance(filters, dict) or not filters:
        raise GateRefused("a breakout needs at least one dimension filter")
    canonical: dict[str, list[str]] = {}
    for code in sorted(filters):
        values = filters[code]
        d = next((d for d in gate.dimensions if d.dimension == code), None)
        if d is None:
            raise GateRefused(f"unknown breakout dimension {code!r}")
        if not d.eligible:
            raise GateRefused(d.reason or f"{d.label} is not eligible")
        if not isinstance(values, list) or not values or not all(
                isinstance(v, str) and v for v in values):
            raise GateRefused(f"no values selected for {d.noun}")
        unknown = sorted(set(values) - {v.value for v in d.values})
        if unknown:
            raise GateRefused(
                f"unknown {d.noun} value(s): {', '.join(unknown)} — the stored "
                "summary does not carry them; Sync the EDM and rebuild the breakout")
        canonical[code] = sorted(set(values))
    return canonical


def _accounts_upper_bound(gate: BreakoutGate,
                          filters: dict[str, list[str]]) -> int:
    """The P-23 preview figure. The intersection cannot exceed any single
    dimension's union, and a union cannot exceed the sum of its per-value
    counts, so min over dimensions of Σ selected counts is a true upper bound.
    Exact counts arrive in the completion outcome."""
    bounds = []
    for code, values in filters.items():
        d = next(d for d in gate.dimensions if d.dimension == code)
        by_value = {v.value: v.accounts for v in d.values}
        bounds.append(sum(by_value.get(v, 0) for v in values))
    return min(bounds) if bounds else 0


def _group_rows(portfolio_id: Any) -> dict[str, dict]:
    rows = execute(
        "SELECT id, group_key, label, name, number FROM breakout_group "
        "WHERE source_portfolio_id = :s",
        {"s": str(portfolio_id)}, connection="WORKBENCH")
    return {str(r["group_key"]): dict(r) for r in rows}


def _compose_group_number(name: str) -> str:
    """The group's ``portfolio_number`` inside 20 characters: the name verbatim
    when it fits (P-26), otherwise the first 14 characters plus 6 hex digits of
    sha256(name) — the ``_compose_number`` technique, not its scheme. Two names
    sharing their first 20 characters must not compose one number: the number
    is what adoption resolves on, and two portfolios carrying one fail both
    (FR-011)."""
    if len(name) <= PORTFOLIO_NUMBER_MAX:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest().upper()[:6]
    return name[:PORTFOLIO_NUMBER_MAX - 6].rstrip() + digest


def compose_group_cart(gate: BreakoutGate, *, edm_id: Any, portfolio_id: Any,
                       groups: Sequence[dict]) -> list[GroupPlan]:
    """Validate and compose a whole cart in submission order. Each element of
    ``groups`` is ``{"label": str, "filters": {dim: [values]}}`` — the modal's
    hidden-input JSON. Raises ``GateRefused`` on a malformed breakout, a name
    already carried by a live portfolio or an earlier cart row, or a member set
    appearing twice in one cart. A member set that already has a
    ``breakout_group`` row adopts that row (P-22) and the confirm writes the
    newest label, name, and number onto it."""
    if gate.source_name is None or gate.source_irp_id is None:
        raise GateRefused("the source portfolio has no Risk Modeler id — "
                          "Sync the EDM, then retry")
    live = {n.casefold() for n in _live_portfolio_names(edm_id)}
    taken = set(live)
    existing = _group_rows(portfolio_id)
    live_keys = _existing_breakout_values(portfolio_id, "custom")
    plans: list[GroupPlan] = []
    for g in groups:
        if not isinstance(g, dict):
            raise GateRefused("malformed breakout")
        label = g.get("label")
        if not isinstance(label, str) or not label.strip():
            raise GateRefused("every breakout needs a name")
        label = label.strip()
        if len(label) > PORTFOLIO_NAME_MAX:
            raise GateRefused(
                f"breakout names cap at {PORTFOLIO_NAME_MAX} characters")
        filters = _validate_group_filters(gate, g.get("filters"))
        key = compute_group_key(filters)
        if any(p.key == key for p in plans):
            raise GateRefused(
                f"two breakouts in the cart have the same members — a breakout "
                f"is its member set, so {label!r} duplicates an earlier row")
        row = existing.get(key)
        name = label
        own_name = (row is not None
                    and name.casefold() == str(row["name"]).casefold())
        if name.casefold() in taken and not own_name:
            where = ("in this EDM" if name.casefold() in live
                     else "in the cart")
            raise GateRefused(f"a portfolio named {name!r} already exists "
                              f"{where} — choose a different name")
        number = _compose_group_number(name)
        taken.add(name.casefold())
        overlap = [p.label for p in plans
                   if any(set(filters.get(d, ())) & set(p.filters.get(d, ()))
                          for d in filters)]
        plans.append(GroupPlan(
            label=label, filters=filters, key=key, name=name, number=number,
            accounts_upper_bound=_accounts_upper_bound(gate, filters),
            exists=(key in live_keys), adopted=(row is not None),
            may_overlap_with=overlap))
    return plans


def group_matches_no_accounts(gate: BreakoutGate,
                              filters: dict[str, list[str]]) -> bool:
    """True only when the Add must be refused: DataBridge answered, and no
    account of the source portfolio carries a selected value in every filtered
    dimension (P-29). The stored summary cannot answer this — it counts accounts
    per value, and two values that each have accounts can still share none.

    Fails open. An unreachable DataBridge, a count that could not be verified,
    or a portfolio with no Risk Modeler id returns False and the Add proceeds;
    the run's own empty-intersection guard stays the final check (FR-021).

    A one-dimension breakout skips the read entirely: its values came from the
    summary, which only carries values accounts actually have, so its selection
    cannot be empty."""
    if (len(filters) < 2 or gate.edm_name is None or gate.edm_irp_id is None
            or gate.source_irp_id is None):
        return False
    try:
        return irp_gateway.count_breakout_match(
            edm_name=gate.edm_name, exposure_irp_id=gate.edm_irp_id,
            source_portfolio_irp_id=gate.source_irp_id,
            filters=filters) == 0
    except Exception as exc:  # noqa: BLE001 — fail open; the run guard backstops
        logger.warning("breakout match count failed for portfolio %s: %s",
                       gate.source_irp_id, exc)
        return False


def check_group_name(edm_id: Any, name: str) -> CollisionCheck:
    """The immediate group-name check (P-25) behind the custom pane's
    as-you-type fragment and the Add-time block: a group's portfolio name must
    not already exist in the EDM. Workbench rows answer first (no network);
    Risk Modeler is then asked through the cached ``name_check`` leg so
    portfolios created outside the workbench are caught too. Fails open when
    Risk Modeler is unreachable — the confirm-time compose and the worker's
    duplicate-name handling are the backstops."""
    trimmed = (name or "").strip()
    if not trimmed:
        return CollisionCheck()
    if trimmed.casefold() in {n.casefold()
                              for n in _live_portfolio_names(edm_id)}:
        return CollisionCheck(names=(trimmed,))
    edm = execute_one(
        "SELECT irp_id FROM irp_edm WHERE id = :e AND deleted_at IS NULL",
        {"e": str(edm_id)}, connection="WORKBENCH")
    if edm is None or edm["irp_id"] is None:
        return CollisionCheck(checked=False)
    return name_check.check_portfolio_name(
        exposure_irp_id=str(edm["irp_id"]), name=trimmed)


def compose_group_preview(edm_id: Any, portfolio_id: Any, *,
                          carted: Sequence[dict],
                          new_group: dict) -> GroupPlan:
    """One cart-row Add, decided entirely here (FR-018/P-23): evaluate the
    gate, compose the posted group against the stored summary and the
    already-carted groups, then the two Add-time refusals — a taken portfolio
    name (P-25; the Risk Modeler leg, fail-open — compose covered the
    workbench rows and the cart; an adopted member set is exempt, since its
    own already-created portfolio may carry the very name being re-confirmed
    and the run's duplicate-name handling backstops) and a group no account
    matches (P-29 — ``group_matches_no_accounts``, fail-open). Raises
    ``GateRefused`` on every refusal; no writes. Callers run the whole call in
    a threadpool: the match count queries DataBridge over the whole portfolio
    and must not hold the event loop while the page's 3-second polls wait."""
    gate = evaluate_gate(edm_id, portfolio_id)
    plans = compose_group_cart(gate, edm_id=edm_id, portfolio_id=portfolio_id,
                               groups=[*carted, new_group])
    plan = plans[-1]
    if not plan.adopted and check_group_name(edm_id, plan.name).collides:
        raise GateRefused(f"a portfolio named {plan.name!r} already "
                          "exists in this EDM — choose a different name")
    if group_matches_no_accounts(gate, plan.filters):
        raise GateRefused(
            "no account matches every filter of this breakout — it would "
            "create nothing. Change the values and add it again")
    return plan


_INSERT_GROUP = """
    INSERT INTO breakout_group (id, source_portfolio_id, group_key, label,
        filters, name, number, cart_id, inserted_at, updated_at, inserted_by,
        updated_by)
    VALUES (:id, :s, :k, :label, :filters, :name, :number, :cart, :now, :now,
        :by, :by)
"""
_STAMP_GROUP_CART = """
    UPDATE breakout_group
    SET label = :label, name = :name, number = :number,
        cart_id = :cart, updated_at = :now, updated_by = :by
    WHERE source_portfolio_id = :s AND group_key = :k
"""


def _upsert_group_row(portfolio_id: Any, plan: GroupPlan, *, cart_id: str,
                      actor_id: Any) -> str:
    """One row per (source, member set): stamp the new cart_id and the
    approved label/name/number onto an existing row, or insert.
    UNIQUE(source_portfolio_id, group_key) absorbs the race."""
    params = {
        "id": str(uuid.uuid4()), "s": str(portfolio_id), "k": plan.key,
        "label": plan.label, "filters": json.dumps(plan.filters),
        "name": plan.name, "number": plan.number, "cart": cart_id,
        "now": _utcnow(), "by": (str(actor_id) if actor_id is not None
                                 else None),
    }
    with get_connection("WORKBENCH") as conn:
        with conn.begin():
            if conn.execute(text(_STAMP_GROUP_CART), params).rowcount:
                row = conn.execute(text(
                    "SELECT id FROM breakout_group "
                    "WHERE source_portfolio_id = :s AND group_key = :k"),
                    params).mappings().first()
                return _uid(row["id"])
            try:
                with conn.begin_nested():
                    conn.execute(text(_INSERT_GROUP), params)
                    return params["id"]
            except Exception as exc:  # noqa: BLE001 — a UNIQUE race is a dedup hit
                if not is_unique_violation(exc):
                    raise
            conn.execute(text(_STAMP_GROUP_CART), params)
            row = conn.execute(text(
                "SELECT id FROM breakout_group "
                "WHERE source_portfolio_id = :s AND group_key = :k"),
                params).mappings().first()
            return _uid(row["id"])


def request_group_breakout(edm_id: Any, portfolio_id: Any,
                           groups: Sequence[dict], summary_as_of: str | None,
                           actor_id: Any) -> list[str] | None:
    """The cart confirm (FR-018/FR-020) — the same ordered refusals as
    ``request_breakout``, then one ``breakout_group`` upsert and one
    ``run_breakout_custom`` job per group, every job stamped with the shared
    ``cart_id`` for banner aggregation. Returns the enqueued job ids, or
    ``None`` when a breakout episode is already live on the portfolio (one
    episode per portfolio, either direction). No job row exists on any
    refusal."""
    def _cart_not_empty(_gate: BreakoutGate) -> None:
        if not groups:
            raise GateRefused("the cart is empty — add at least one breakout")

    gate = _confirm_preconditions(edm_id, portfolio_id, summary_as_of,
                                  _cart_not_empty)
    if gate is None:
        return None

    # The approved plan, composed once (AGENTS.md rule 8): the group rows and
    # each job's input_data are what the worker executes verbatim.
    plans = compose_group_cart(gate, edm_id=edm_id, portfolio_id=portfolio_id,
                               groups=groups)
    cart_id = str(uuid.uuid4())
    job_ids: list[str] = []
    for plan in plans:
        group_row_id = _upsert_group_row(portfolio_id, plan, cart_id=cart_id,
                                         actor_id=actor_id)
        job_id = rwb_job_service.ensure_pending_rwb_job(
            requestor_type="breakout_group", requestor_id=group_row_id,
            rwb_job_type="run_breakout_custom",
            input_data={
                "edm_id": str(edm_id), "portfolio_id": str(portfolio_id),
                "dimension": "custom", "actor_id": str(actor_id),
                "cart_id": cart_id,
                "group": {"id": group_row_id, "key": plan.key,
                          "label": plan.label, "filters": plan.filters,
                          "name": plan.name, "number": plan.number,
                          "accounts_upper_bound": plan.accounts_upper_bound},
            },
            actor_id=str(actor_id))
        if job_id is not None:
            job_ids.append(job_id)
            dispatch.dispatch(rwb_job_id=job_id,
                              rwb_job_type="run_breakout_custom")
    # Business-event log (FR-015/P-08).
    logger.info("custom-group breakout requested for portfolio %s by analyst "
                "%s (n_groups=%d, cart=%s)", portfolio_id, actor_id,
                len(plans), cart_id)
    return job_ids


@dataclass(frozen=True)
class ApprovedGroup:
    """The worker-side view of one approved group (plan immutability — the
    exact mirror of ``load_approved_plan``)."""
    id: str
    key: str
    label: str
    filters: dict[str, list[str]]
    name: str
    number: str


def load_approved_group(input_data: dict) -> ApprovedGroup:
    """Parse ``input_data['group']`` and read NOTHING else — not the stored
    summary, not the ``breakout_group`` row, and never recompose the name
    (T-10 semantics). Anything malformed raises, which fails the job with
    nothing created."""
    raw = (input_data or {}).get("group")
    if not isinstance(raw, dict):
        raise ValueError("approved breakout is missing in input_data")
    fields = {k: raw.get(k) for k in ("id", "key", "label", "name", "number")}
    if not all(isinstance(v, str) and v for v in fields.values()):
        raise ValueError("approved breakout is missing id/key/label/name/number")
    filters_raw = raw.get("filters")
    if not isinstance(filters_raw, dict) or not filters_raw:
        raise ValueError("approved breakout carries no member filters")
    filters: dict[str, list[str]] = {}
    for dim, values in filters_raw.items():
        if not (isinstance(dim, str) and dim and isinstance(values, list)
                and values and all(isinstance(v, str) and v for v in values)):
            raise ValueError("approved breakout filters are malformed")
        filters[dim] = list(values)
    return ApprovedGroup(id=fields["id"], key=fields["key"],
                         label=fields["label"], filters=filters,
                         name=fields["name"], number=fields["number"])


# ── Worker-side plan load + outcome assembly (R10 / T-10) ────────────────────────

def load_approved_plan(input_data: dict) -> list[SubPortfolioPlan]:
    """Parse ``input_data['plan']`` and read NOTHING else — not the stored
    summary, not the current portfolio names — and never re-suffix: collision
    suffixing reads portfolio names the run itself changes (T-10). An empty or
    unparseable plan raises, which fails the job with nothing created."""
    raw = (input_data or {}).get("plan")
    if not isinstance(raw, list) or not raw:
        raise ValueError("approved plan is missing or empty in input_data")
    plan: list[SubPortfolioPlan] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"approved plan entry {i} is not an object")
        value, name, number = (entry.get("value"), entry.get("name"),
                               entry.get("number"))
        if not (isinstance(value, str) and value and isinstance(name, str)
                and name and isinstance(number, str) and number):
            raise ValueError(
                f"approved plan entry {i} is missing value/name/number")
        label = entry.get("label")
        accounts = entry.get("accounts")
        plan.append(SubPortfolioPlan(
            value=value,
            label=(label if isinstance(label, str) and label else None),
            name=name, number=number,
            accounts=(int(accounts) if isinstance(accounts, (int, float))
                      else 0),
            exists=False))
    return plan


@dataclass(frozen=True)
class SubPortfolioOutcome:
    """One executed plan entry's result — serialized into
    ``rwb_job.output_data.sub_portfolios`` (data-model §4). ``accounts`` is the
    count read back from Risk Modeler, never the add call's ``completed`` figure
    (W-9)."""
    value: str
    name: str
    number: str
    outcome: str              # created | adopted | skipped_existing | failed
    irp_id: str | None = None
    accounts: int | None = None
    error: str | None = None


def summarize_outcomes(outcomes: Sequence[SubPortfolioOutcome]) -> dict:
    """The ``output_data`` shape of data-model §4 (``backfill_enqueued`` is
    stamped by the worker after its completion enqueue)."""
    def count(kind: str) -> int:
        return sum(1 for o in outcomes if o.outcome == kind)

    sub_portfolios = []
    for o in outcomes:
        entry: dict[str, Any] = {"value": o.value, "name": o.name,
                                 "number": o.number, "outcome": o.outcome}
        if o.irp_id is not None:
            entry["irp_id"] = o.irp_id
        if o.accounts is not None:
            entry["accounts"] = o.accounts
        if o.error is not None:
            entry["error"] = o.error
        sub_portfolios.append(entry)
    return {
        "planned": len(outcomes),
        "created": count("created"),
        "adopted": count("adopted"),
        "skipped_existing": count("skipped_existing"),
        "failed": count("failed"),
        "sub_portfolios": sub_portfolios,
    }


__all__ = [
    "PORTFOLIO_NAME_MAX", "PORTFOLIO_NUMBER_MAX", "LARGE_FANOUT_THRESHOLD",
    "MISSING_SUMMARY_REASON", "REFRESH_IN_FLIGHT_REASON",
    "BreakoutRefused", "GateRefused", "SummaryRewritten", "StaleSummary",
    "display_value",
    "BreakoutValue", "DimensionCoverage", "DimensionEligibility",
    "BreakoutGate", "evaluate_gate",
    "SubPortfolioPlan", "build_breakout_plan", "compose_plan",
    "Overlap", "compute_overlap",
    "BreakoutModal", "modal_context",
    "BreakoutFlight", "BreakoutRowError", "BreakoutBanner",
    "BreakoutPageState", "page_state",
    "request_breakout",
    "load_approved_plan", "SubPortfolioOutcome", "summarize_outcomes",
    "compute_group_key", "GroupPlan", "compose_group_cart",
    "check_group_name", "group_matches_no_accounts",
    "request_group_breakout", "ApprovedGroup", "load_approved_group",
]
