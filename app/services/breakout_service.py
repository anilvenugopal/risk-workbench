"""Breakout service — the one testable home for the portfolio-breakout op (spec 005).

Owns, per contracts/data-access.md §1:
  • the prerequisite gate (``evaluate_gate`` — FR-002/FR-003, the Article 12
    named must-test),
  • the pure name/number plan builder (``build_breakout_plan`` — P-11/T-05),
  • the overlap arithmetic (``compute_overlap`` — FR-007/P-13),
  • the confirm path (``request_breakout`` — five ordered steps, no ``rwb_job``
    row until all five pass),
  • the worker-side plan load + outcome assembly (``load_approved_plan`` /
    ``summarize_outcomes`` — R10/T-10).

All SQL through ``db.execute*`` (Article 7). The ONLY Risk Modeler call on the
request path is ``request_breakout``'s freshness read via
``irp_gateway.fetch_portfolio_stamp`` — the Article 2 submit-time pattern
(FR-002a). Value enumeration reads the STORED spec-004 summary only; no
DataBridge or RM read anywhere else (Article 11).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Collection, Sequence

from app.services import irp_gateway, rwb_job_service
from app.services._common import _parse_json_dict, _uid
from app.workers import dispatch
from db import execute, execute_one

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

# The dimension letter inside the generated portfolio_number (R4).
_DIMENSION_LETTER = {"lob": "L", "state": "S"}
# Analyst-facing noun per dimension for disabled-with-reason copy.
_DIMENSION_NOUN = {"lob": "line of business", "state": "state"}

MISSING_SUMMARY_REASON = "exposure summary not available — run Sync"
REFRESH_IN_FLIGHT_REASON = ("this EDM is syncing — the exposure summary is "
                            "being rewritten")


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
class DimensionEligibility:
    dimension: str            # breakout_dimension_kind.code
    label: str                # breakout_dimension_kind.label (display)
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
    """The stored summary's values for one dimension. ``None`` means ABSENT —
    no summary, no ``breakout_values`` key (every pre-005 summary), or a
    malformed container/entry; the gate points at Sync and there is NO fallback
    to the mixed-vocabulary ``states`` list (P-12/R11). A present container
    whose dimension key is missing or empty reads as zero values, not absent."""
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
    """The dimension code of a pending|running ``run_breakout_*`` job for this
    portfolio, or ``None``. The enqueue key is (analyst_request, portfolio id,
    run_breakout_{dimension}) — data-model §3."""
    row = execute_one(
        "SELECT rwb_job_type FROM rwb_job "
        "WHERE requestor_type = 'analyst_request' AND requestor_id = :p "
        "AND rwb_job_type LIKE 'run_breakout_%' "
        "AND status_code IN ('pending', 'running')",
        {"p": str(portfolio_id)}, connection="WORKBENCH")
    if row is None:
        return None
    return str(row["rwb_job_type"]).removeprefix("run_breakout_")


def _backfill_in_flight(edm_id: Any) -> bool:
    """True while a ``backfill_edm_detail`` for this EDM is pending|running,
    under ANY of its three enqueue keys — the poller's import-keyed head (joins
    through irp_job), the manual Sync's EDM-keyed head, and a completed
    breakout's auto-fired head keyed on the ``run_breakout_*`` job row (FR-013)
    — the same condition ``edm_service.sync_detail`` applies to itself (P-16)."""
    row = execute_one(
        "SELECT rj.id FROM rwb_job rj "
        "LEFT JOIN irp_job ij ON rj.requestor_type = 'irp_job' "
        "AND rj.requestor_id = ij.id "
        "WHERE rj.rwb_job_type = 'backfill_edm_detail' "
        "AND rj.status_code IN ('pending', 'running') "
        "AND (ij.irp_edm_id = :e "
        "     OR (rj.requestor_type = 'analyst_request' AND rj.requestor_id = :e) "
        "     OR (rj.requestor_type = 'rwb_job' AND rj.requestor_id IN ("
        "         SELECT bj.id FROM rwb_job bj "
        "         JOIN irp_portfolio p ON bj.requestor_id = p.id "
        "         WHERE bj.rwb_job_type LIKE 'run_breakout_%' "
        "         AND p.edm_id = :e)))",
        {"e": str(edm_id)}, connection="WORKBENCH")
    return row is not None


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
        noun = _DIMENSION_NOUN.get(code, label.lower())
        values = _parse_breakout_values(summary, code)
        if values is None:
            dimensions.append(DimensionEligibility(
                dimension=code, label=label, eligible=False, values=[],
                reason=MISSING_SUMMARY_REASON))
        elif len(values) < 2:
            dim_reason = (f"only one {noun} present" if len(values) == 1
                          else f"no {noun} values present")
            dimensions.append(DimensionEligibility(
                dimension=code, label=label, eligible=False, values=values,
                reason=dim_reason))
        else:
            dimensions.append(DimensionEligibility(
                dimension=code, label=label, eligible=portfolio_eligible,
                values=values, reason=None))

    account_total = None
    if isinstance(summary, dict):
        raw_total = summary.get("account_total")
        if isinstance(raw_total, (int, float)):
            account_total = int(raw_total)

    return BreakoutGate(
        portfolio_eligible=portfolio_eligible, reason=reason,
        dimensions=dimensions, in_flight=in_flight,
        refresh_in_flight=refresh_in_flight, summary_as_of=summary_as_of,
        account_total=account_total)


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
    letter = _DIMENSION_LETTER.get(dimension, dimension[:1].upper())
    prefix = f"P{source_portfolio_irp_id}-{letter}-"
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
        # "cbhu - 200"); the value stays the filter, the stored
        # breakout_value, and the number token.
        name = _compose_name(source_name, v.label or v.value, taken)
        taken.add(name.casefold())
        plan.append(SubPortfolioPlan(
            value=v.value, label=v.label, name=name,
            number=_compose_number(source_portfolio_irp_id, dimension, v.value),
            accounts=v.accounts, exists=(v.value in already)))
    return plan


# ── Overlap arithmetic (FR-007 / P-13) ───────────────────────────────────────────

@dataclass(frozen=True)
class Overlap:
    account_total: int | None   # summary.account_total (the denominator)
    summed: int                 # Σ accounts over the dimension's values
    repeats: int | None         # summed − account_total, floored at 0; None when the total is absent
    partition: bool             # repeats == 0 — the sub-portfolios partition the source cleanly


def compute_overlap(values: Sequence[BreakoutValue],
                    account_total: int | None) -> Overlap:
    """Pure arithmetic over the stored summary. A missing ``account_total``
    yields ``repeats=None`` and the preview falls back to the qualitative
    disclosure alone (data-model §6)."""
    summed = sum(v.accounts for v in values)
    if account_total is None:
        return Overlap(account_total=None, summed=summed, repeats=None,
                       partition=False)
    repeats = max(summed - account_total, 0)
    return Overlap(account_total=account_total, summed=summed, repeats=repeats,
                   partition=(repeats == 0))


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
    edm, portfolio = _load_rows(edm_id, portfolio_id)
    if (edm is None or edm["deleted_at"] is not None or portfolio is None
            or portfolio["deleted_at"] is not None):
        return None
    gate = evaluate_gate(edm_id, portfolio_id)

    eligible = [d.dimension for d in gate.dimensions if d.eligible]
    selected = (dimension if dimension in eligible
                else (eligible[0] if eligible else None))
    # A portfolio without its RM id cannot compose portfolio numbers; in
    # practice it also has no summary (both come from the same backfill), so
    # this guard only closes the theoretical gap.
    if portfolio["irp_id"] is None:
        selected = None

    plan: list[SubPortfolioPlan] = []
    overlap: Overlap | None = None
    if selected is not None:
        plan = compose_plan(gate, edm_id=edm_id, portfolio_id=portfolio_id,
                            source_name=portfolio["name"],
                            source_portfolio_irp_id=str(portfolio["irp_id"]),
                            dimension=selected)
        values = next(d.values for d in gate.dimensions
                      if d.dimension == selected)
        overlap = compute_overlap(values, gate.account_total)

    return BreakoutModal(
        gate=gate, portfolio_name=portfolio["name"],
        portfolio_irp_id=portfolio["irp_id"], dimension=selected,
        noun=(_DIMENSION_NOUN.get(selected) if selected else None),
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
    code = str(rwb_job_type).removeprefix("run_breakout_")
    return code, _DIMENSION_NOUN.get(code, code)


def _plan_values(input_data_raw: Any) -> list[str]:
    data = _parse_json_dict(input_data_raw, "input_data") or {}
    raw = data.get("plan")
    if not isinstance(raw, list):
        return []
    return [e["value"] for e in raw
            if isinstance(e, dict) and isinstance(e.get("value"), str)]


def page_state(edm_id: Any) -> BreakoutPageState:
    """The EDM body's breakout read model, attached by
    ``edm_service.get_edm_detail`` — pure WORKBENCH reads."""
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

    terminal = execute(
        "SELECT rj.id, rj.requestor_id, rj.rwb_job_type, rj.status_code, "
        "rj.output_data, rj.error_detail, p.name AS source_name "
        "FROM rwb_job rj JOIN irp_portfolio p ON rj.requestor_id = p.id "
        "WHERE p.edm_id = :e AND rj.requestor_type = 'analyst_request' "
        "AND rj.rwb_job_type LIKE 'run_breakout_%' "
        "AND rj.status_code IN ('succeeded', 'failed') "
        "ORDER BY rj.updated_at DESC, rj.id",
        {"e": str(edm_id)}, connection="WORKBENCH")

    errors: dict[str, list[BreakoutRowError]] = {}
    banner: BreakoutBanner | None = None
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(terminal):
        pid = _uid(row["requestor_id"])
        code, noun = _noun_for_job_type(row["rwb_job_type"])
        if (pid, code) in seen:
            continue                     # only the LATEST terminal run counts
        seen.add((pid, code))
        output = _parse_json_dict(row["output_data"], "output_data") or {}

        def _count(key: str) -> int:
            v = output.get(key)  # noqa: B023 — consumed within this iteration
            return int(v) if isinstance(v, (int, float)) else 0

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

        if i == 0:                       # the banner is the NEWEST terminal
            failed = _count("failed")    # job's summary, or nothing at all
            ok = row["status_code"] == "succeeded" and failed == 0
            follow_up = execute_one(
                "SELECT status_code FROM rwb_job "
                "WHERE requestor_type = 'rwb_job' AND requestor_id = :j "
                "AND rwb_job_type = 'backfill_edm_detail'",
                {"j": str(row["id"])}, connection="WORKBENCH")
            filling_in = (follow_up is not None
                          and follow_up["status_code"] in ("pending", "running"))
            if filling_in or failed or row["status_code"] == "failed":
                banner = BreakoutBanner(
                    source_name=str(row["source_name"]), noun=noun,
                    created=_count("created"), adopted=_count("adopted"),
                    skipped_existing=_count("skipped_existing"), failed=failed,
                    ok=ok, filling_in=filling_in,
                    error=(str(row["error_detail"])
                           if row["status_code"] == "failed"
                           and row["error_detail"] else None))

    return BreakoutPageState(running=bool(flights), banner=banner,
                             flights=flights, errors=errors)


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


def request_breakout(edm_id: Any, portfolio_id: Any, dimension: str,
                     summary_as_of: str | None, actor_id: Any) -> str | None:
    """Five steps, in order; each gates the next, and **no rwb_job row exists
    until all five pass** (contracts/data-access.md §1): gate re-check →
    summary-unchanged check (FR-002b) → freshness check (FR-002a) → build and
    persist the approved plan → idempotent enqueue. Returns the job id, or
    ``None`` when a live job already exists (UI: "already running")."""
    # 1. Gate re-check.
    gate = evaluate_gate(edm_id, portfolio_id)
    if not gate.portfolio_eligible:
        raise GateRefused(gate.reason or "breakout is not available")
    if gate.in_flight is not None:
        return None  # already running — the router renders the 409 variant
    eligibility = next((d for d in gate.dimensions if d.dimension == dimension),
                       None)
    if eligibility is None:
        raise GateRefused(f"unknown breakout dimension {dimension!r}")
    if not eligibility.eligible:
        raise GateRefused(eligibility.reason or "dimension is not eligible")

    # 2. Summary-unchanged check (FR-002b): the stored summary must be the one
    # the preview rendered from. A detail refresh that landed mid-preview
    # changes the value set the analyst judged from, and FR-002a cannot see it
    # (an untouched RM portfolio writes back an equal stampDate).
    if gate.summary_as_of != (str(summary_as_of) if summary_as_of else None):
        raise SummaryRewritten(
            "This EDM was synced while you were reviewing — here is the "
            "current breakout.")

    # 3. Freshness check (FR-002a): the flow's one web-layer RM call.
    edm, portfolio = _load_rows(edm_id, portfolio_id)
    stored_stamp = _stored_stamp(portfolio["exposure_detail"])
    if stored_stamp is None:
        raise StaleSummary(
            "Portfolio data has changed in Risk Modeler since the last sync — "
            "Sync the EDM, then retry.")
    if edm["irp_id"] is None or portfolio["irp_id"] is None:
        raise StaleSummary("couldn't verify freshness — Sync the EDM, then retry.")
    try:
        current_stamp = irp_gateway.fetch_portfolio_stamp(
            exposure_irp_id=int(edm["irp_id"]),
            portfolio_irp_id=str(portfolio["irp_id"]))
    except Exception as exc:  # noqa: BLE001 — unverifiable freshness refuses, never proceeds
        logger.warning("breakout freshness read failed for portfolio %s: %s",
                       portfolio_id, exc)
        raise StaleSummary(
            "couldn't verify freshness — try again or Sync the EDM.") from exc
    if current_stamp is None or str(current_stamp) != stored_stamp:
        raise StaleSummary(
            "Portfolio data has changed in Risk Modeler since the last sync — "
            "Sync the EDM, then retry.")

    # 4. Build and persist the approved plan — composed ONCE, here; from this
    # point the plan is authoritative and the worker executes it verbatim
    # (AGENTS.md rule 8 / R10 / P-14).
    plan = compose_plan(gate, edm_id=edm_id, portfolio_id=portfolio_id,
                        source_name=portfolio["name"],
                        source_portfolio_irp_id=str(portfolio["irp_id"]),
                        dimension=dimension)
    input_data = {
        "edm_id": str(edm_id), "portfolio_id": str(portfolio_id),
        "dimension": dimension, "actor_id": str(actor_id),
        "plan": [{"value": p.value, "label": p.label, "name": p.name,
                  "number": p.number, "accounts": p.accounts} for p in plan],
    }

    # 5. Idempotent enqueue — one live-job slot per (portfolio, dimension).
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
    return job_id


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
    "BreakoutValue", "DimensionEligibility", "BreakoutGate", "evaluate_gate",
    "SubPortfolioPlan", "build_breakout_plan", "compose_plan",
    "Overlap", "compute_overlap",
    "BreakoutModal", "modal_context",
    "BreakoutFlight", "BreakoutRowError", "BreakoutBanner",
    "BreakoutPageState", "page_state",
    "request_breakout",
    "load_approved_plan", "SubPortfolioOutcome", "summarize_outcomes",
]
