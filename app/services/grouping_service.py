"""Grouping — the compose gate, inspection view, and plan for spec 012
(contracts/routes.md).

``inspect_grouping`` reads the members' facts from Risk Modeler through the
gateway (no writes). ``request_grouping`` is the only write entry point:
validate the posted selection against stored state, compose the plan **once**
(AGENTS.md rule 8 — approved plans are immutable), persist it as the sole
``submit_grouping`` ``rwb_job`` for a fresh ``grouping_request_id``, and
dispatch. The worker (``app/workers/grouping_jobs.py``) reads nothing else at
execution time and owns the group's ``irp_analysis`` claim; this module never
touches ``irp_analysis`` beyond reads.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from app.services import irp_gateway, rwb_job_service
from app.services._common import _parse_json_dict, _uid
from app.services.analysis_execution_service import (
    ExecutionGateError,
    _validate_currency,
)
from app.workers import dispatch
from app.workers.analysis_jobs import name_attempt
from db import execute

KIND_LABELS = {"own": "Own", "broker": "Broker", "group": "Group"}

# Risk Modeler's group simulation periods dropdown (FR-019), also offered per
# partition of a PLT group; an ELT group submits 1 without a choice.
SIMULATION_PERIOD_OPTIONS = (
    3125, 6250, 12500, 25000, 50000, 100000, 200000, 400000, 800000)
DEFAULT_SIMULATION_PERIODS = 50000


@dataclass(frozen=True)
class GroupMember:
    """One eligible pick-list member (contracts/routes.md — GET context)."""
    id: str
    irp_id: int | None        # Platform analysisId — the grouping member key (T-10)
    name: str | None          # the ≤64-char name known to Risk Modeler
    display_name: str | None  # full_name where one exists
    kind: str                 # own | broker | group
    engine: str | None        # pick-list disclosure column
    # Run currency code: own rows and groups from submitted_settings, broker
    # rows from the Risk Modeler metadata (the FR-005 rule); None when unknown.
    currency: str | None = None
    app_analysis_id: str | None = None  # RM appAnalysisId — the web UI's id

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]


@dataclass(frozen=True)
class GroupingInspectionView:
    """The inspect fragment's context: the package inspection, the picked
    members keyed by Platform id, and the currency the members share — the
    group currency prefill, None when the codes differ or one is unknown
    (FR-004)."""
    inspection: irp_gateway.GroupingInspection
    members: dict[int, GroupMember]
    common_currency: str | None = None

    @property
    def member_currencies(self) -> tuple[str, ...]:
        """Distinct known member currency codes, in member order."""
        return tuple(dict.fromkeys(
            m.currency for m in self.members.values() if m.currency))

    @property
    def currency_unknown(self) -> bool:
        return any(m.currency is None for m in self.members.values())


_ELIGIBLE_SELECT = """
    SELECT a.id, a.name, a.full_name, a.is_group, a.settings_metadata,
           a.submitted_settings, a.irp_app_analysis_id,
           a.rdm_id, a.irp_id, a.inserted_at
    FROM irp_analysis a
    JOIN submission_edm se ON se.edm_id = a.edm_id
    JOIN irp_edm e ON e.id = a.edm_id AND e.deleted_at IS NULL
    WHERE se.submission_id = :sid AND a.rdm_id IS NULL
      AND a.status_code = 'ready' AND a.deleted_at IS NULL
    UNION ALL
    SELECT a.id, a.name, a.full_name, a.is_group, a.settings_metadata,
           a.submitted_settings, a.irp_app_analysis_id,
           a.rdm_id, a.irp_id, a.inserted_at
    FROM irp_analysis a
    JOIN submission_rdm sr ON sr.rdm_id = a.rdm_id
    JOIN irp_rdm r ON r.id = a.rdm_id AND r.deleted_at IS NULL
    WHERE sr.submission_id = :sid AND a.deleted_at IS NULL
    UNION ALL
    SELECT a.id, a.name, a.full_name, a.is_group, a.settings_metadata,
           a.submitted_settings, a.irp_app_analysis_id,
           a.rdm_id, a.irp_id, a.inserted_at
    FROM irp_analysis a
    WHERE a.submission_id = :sid AND a.is_group = 1
      AND a.status_code = 'ready' AND a.deleted_at IS NULL
    ORDER BY inserted_at DESC
"""


def list_eligible_members(submission_id: Any) -> list[GroupMember]:
    """Every analysis of the submission a group may contain (FR-003): own
    analyses at ``ready``, captured broker analyses (every capture is a
    finished run), and finished groups (nesting, FR-018). Running/failed rows
    never appear. Broker handles are deduped by RM ``analysisId`` — the same
    analysis captured under two of the submission's RDMs is one member."""
    from app.services.analysis_service import (  # noqa: PLC0415 — display only; avoids a cycle
        _submitted_view,
        _to_display,
    )

    rows = execute(_ELIGIBLE_SELECT, {"sid": str(submission_id)},
                   connection="WORKBENCH")
    members: list[GroupMember] = []
    seen_broker_irp_ids: set[str] = set()
    for r in rows:
        is_group = bool(r["is_group"])
        if r["rdm_id"] is not None:
            if r["irp_id"] is not None and str(r["irp_id"]) in seen_broker_irp_ids:
                continue
            if r["irp_id"] is not None:
                seen_broker_irp_ids.add(str(r["irp_id"]))
            kind = "group" if is_group else "broker"
        else:
            kind = "group" if is_group else "own"
        settings = _parse_json_dict(r["settings_metadata"], "settings_metadata")
        display = _to_display(settings)
        currency = (display.currency if r["rdm_id"] is not None
                    else _submitted_view(r["submitted_settings"]).currency)
        app_analysis_id = (r["irp_app_analysis_id"]
                           or (settings or {}).get("appAnalysisId"))
        members.append(GroupMember(
            id=_uid(r["id"]),
            irp_id=(int(r["irp_id"]) if r["irp_id"] is not None else None),
            name=r["name"],
            display_name=r["full_name"] or r["name"], kind=kind,
            engine=("Group" if is_group else display.engine),
            currency=currency,
            app_analysis_id=(str(app_analysis_id) if app_analysis_id is not None
                             else None)))
    return members


def _free_group_name(submission_id: Any, full_name: str) -> tuple[str, str]:
    """The first ``(full_name, submitted_name)`` attempt whose submitted name is
    free among the submission's live group names (T-09)."""
    attempt = 0
    while True:
        full, name = name_attempt(full_name, attempt)
        taken = execute(
            "SELECT 1 FROM irp_analysis WHERE submission_id = :sid "
            "AND name = :n AND deleted_at IS NULL",
            {"sid": str(submission_id), "n": name}, connection="WORKBENCH")
        if not taken:
            return full, name
        attempt += 1


def build_group_name(submission_id: Any, submission_name: str) -> str:
    """The dialog's prefill: ``CRE_<submission name>_Group``, already
    collision-suffixed and ≤64 (FR-002, T-09)."""
    return _free_group_name(submission_id, f"CRE_{submission_name}_Group")[1]


def _pick_members(submission_id: Any, member_ids: list[str],
                  errors: list[str]) -> list[GroupMember]:
    """The posted members that are eligible, in posted order, collecting the
    gate failures shared by inspect and submit."""
    eligible = {m.id: m for m in list_eligible_members(submission_id)}
    picked_ids = [i for i in dict.fromkeys(_uid(m) for m in member_ids) if i]
    if any(i not in eligible for i in picked_ids):
        errors.append("A selected analysis is no longer eligible for grouping.")
    picked = [eligible[i] for i in picked_ids if i in eligible]
    if len(picked) < 2:
        errors.append("Pick at least two analyses to group.")
    for m in picked:
        if m.irp_id is None:
            errors.append(f"{m.display_name} has no Risk Modeler analysis id yet.")
    return picked


def inspect_grouping(*, submission_id: Any,
                     member_ids: list[str]) -> GroupingInspectionView:
    """Run the package inspection for the posted members (Platform reads
    only, nothing persisted). Raises ``ExecutionGateError`` for gate failures
    and for a failed Platform read, so the router handles one type."""
    errors: list[str] = []
    picked = _pick_members(submission_id, member_ids, errors)
    if errors:
        raise ExecutionGateError(errors)
    try:
        inspection = irp_gateway.inspect_grouping(
            analysis_ids=[m.irp_id for m in picked])
    except irp_gateway.IRPIntegrationError as exc:
        raise ExecutionGateError([f"Inspection failed: {exc}"]) from exc
    currencies = {m.currency for m in picked}
    return GroupingInspectionView(
        inspection=inspection, members={m.irp_id: m for m in picked},
        common_currency=(currencies.pop()
                         if len(currencies) == 1 and None not in currencies
                         else None))


def finish_blockers(view: GroupingInspectionView, *,
                    currency_defaults: dict) -> list[str]:
    """Why Finish — inspect and submit in one step with every setting
    defaulted (FR-025) — must stop at the inspection instead. Empty when the
    group can be submitted in the members' currency and the env scheme and
    vintage (``currency_defaults``, cache-checked) with no choice left to the
    analyst; a PLT group then takes ``DEFAULT_SIMULATION_PERIODS`` for the
    group and every partition. Treaty mismatches never stop it (FR-020)."""
    inspection = view.inspection
    reasons: list[str] = []
    if not (currency_defaults["scheme"] and currency_defaults["vintage"]):
        reasons.append("The default currency scheme or vintage is not set.")
    if inspection.blocking_problems:
        reasons.append("The members cannot be grouped.")
    if any(p.event_rate_selection_required for p in inspection.partitions):
        reasons.append("A partition needs an event-rate scheme choice.")
    if any(p.simulation_set_selection_required for p in inspection.partitions):
        reasons.append("A partition needs a simulation set choice.")
    if view.common_currency is None:
        reasons.append("The members did not all run in one known currency.")
    return reasons


def default_simulation_periods_selections(view: GroupingInspectionView) -> list[str]:
    """Finish's per-partition simulation periods for a PLT group: the posted
    ``simulation_periods_selection`` value of every partition at
    ``DEFAULT_SIMULATION_PERIODS`` (FR-025)."""
    return [json.dumps({"peril_code": p.key.peril_code,
                        "region_code": p.key.region_code,
                        "model_version": p.key.model_version,
                        "simulation_periods": DEFAULT_SIMULATION_PERIODS})
            for p in view.inspection.partitions]


_SELECTION_KEY = ("peril_code", "region_code", "model_version")


def _parse_selections(raw: list[str], value_key: str) -> list[dict] | None:
    """The posted ``event_rate_selection``, ``simulation_set_selection``, or
    ``simulation_periods_selection`` option values as plan entries — the
    partition key plus ``value_key`` — or ``None`` when any is malformed or
    two name the same partition."""
    selections: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for value in raw:
        try:
            data = json.loads(value)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        key = tuple(data.get(k) for k in _SELECTION_KEY)
        chosen = data.get(value_key)
        if (not all(isinstance(k, str) and k for k in key)
                or not isinstance(chosen, int) or isinstance(chosen, bool)
                or key in seen):
            return None
        seen.add(key)
        selections.append({**dict(zip(_SELECTION_KEY, key, strict=True)),
                           value_key: chosen})
    return selections


def request_grouping(
    *, submission_id: Any, submission_name: str, member_ids: list[str],
    group_name: str, currency_code: str = "", currency_scheme: str = "",
    currency_vintage: str = "", propagate_detailed_output: bool = True,
    num_of_simulations: str, event_rate_selections: list[str],
    simulation_set_selections: list[str], simulation_periods_selections: list[str],
    expected_inspection_fingerprint: str, inspected_analysis_ids: list[str],
    actor_id: Any,
) -> str:
    """Validate the posted selection, compose the plan once, persist it on a
    fresh ``submit_grouping`` ``rwb_job`` and dispatch. Raises
    ``ExecutionGateError`` on any validation failure — no partial persistence
    (SC-005). Returns the new ``grouping_request_id``.

    Which partitions require an event-rate or simulation-set selection, and
    whether the group is PLT and so takes per-partition simulation periods, is
    not re-derived here (that needs another inspection); the package validates
    it at submit and the worker records the structured reason. The same goes
    for the simulation count: ``1`` (the ELT group's hidden value) or one of
    ``SIMULATION_PERIOD_OPTIONS`` passes; whether the group is ELT or PLT is
    the package's check."""
    errors: list[str] = []
    picked = _pick_members(submission_id, member_ids, errors)
    irp_ids = sorted(m.irp_id for m in picked if m.irp_id is not None)
    try:
        inspected = sorted(int(i) for i in inspected_analysis_ids)
    except ValueError:
        inspected = []
    if irp_ids != inspected:
        errors.append("Members changed since inspection. Inspect again.")
    if not expected_inspection_fingerprint.strip():
        errors.append("Inspect the members before grouping.")
    try:
        simulations = int(num_of_simulations.strip())
    except ValueError:
        simulations = 0
    if simulations != 1 and simulations not in SIMULATION_PERIOD_OPTIONS:
        errors.append("Choose one of the offered simulation period counts.")
    selections = _parse_selections(event_rate_selections, "event_rate_scheme_id")
    if selections is None:
        errors.append("Choose an event-rate scheme for every conflicting partition.")
    simulation_sets = _parse_selections(simulation_set_selections, "simulation_set_id")
    if simulation_sets is None:
        errors.append("Choose a simulation set for every partition converted "
                      "from ELT to PLT.")
    simulation_periods = _parse_selections(simulation_periods_selections,
                                           "simulation_periods")
    if simulation_periods is None or any(
            s["simulation_periods"] not in SIMULATION_PERIOD_OPTIONS
            for s in simulation_periods):
        errors.append("Choose one of the offered simulation period counts for "
                      "every partition.")
    group_name = group_name.strip()
    if not group_name:
        errors.append("Enter a group name.")
    currency, currency_error = _validate_currency(
        currency_code, currency_scheme, currency_vintage)
    if currency_error:
        errors.append(currency_error)
    if errors:
        raise ExecutionGateError(errors)

    # A collision with a live group name is not an error — the ``_n`` suffix
    # applies automatically (contracts/routes.md); the worker re-checks under
    # its own claim anyway.
    group_full_name, _ = _free_group_name(submission_id, group_name)
    grouping_request_id = str(uuid.uuid4())
    plan = {
        "grouping_request_id": grouping_request_id,
        "group_analysis_id": str(uuid.uuid4()),
        "submission_id": str(submission_id),
        "submission_name": submission_name,
        "group_full_name": group_full_name,
        "actor_id": (str(actor_id) if actor_id is not None else None),
        "currency": currency,
        "propagate_detailed_losses": bool(propagate_detailed_output),
        "num_of_simulations": simulations,
        "event_rate_selections": selections,
        "simulation_set_selections": simulation_sets,
        "simulation_periods_selections": simulation_periods,
        "expected_inspection_fingerprint": expected_inspection_fingerprint.strip(),
        "members": [
            {"analysis_id": m.id, "irp_id": m.irp_id, "name": m.name,
             "display_name": m.display_name, "kind": m.kind}
            for m in picked
        ],
    }
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=grouping_request_id,
        rwb_job_type="submit_grouping", input_data=plan, actor_id=actor_id)
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="submit_grouping")
    return grouping_request_id


def requested_group_name(grouping_request_id: str) -> str:
    """The full group name the plan carries — the posted name with any ``_n``
    collision suffix applied — for the Finish confirmation (FR-025)."""
    row = execute(
        "SELECT input_data FROM rwb_job WHERE requestor_type = 'analyst_request' "
        "AND requestor_id = :id AND rwb_job_type = 'submit_grouping'",
        {"id": grouping_request_id}, connection="WORKBENCH")
    return json.loads(row[0]["input_data"])["group_full_name"]


def grouping_request_is_live(grouping_request_id: Any | None) -> bool:
    """Whether the named ``submit_grouping`` head is still pending/running —
    keeps the merged grid's 3s poll alive between the compose POST and the
    worker's claim (the group row does not exist yet, so nothing else reads as
    live). The id rides the section's poll URL, exactly as ``execution_id``
    does for the analysis batch."""
    if not grouping_request_id:
        return False
    rows = execute(
        "SELECT 1 FROM rwb_job WHERE requestor_type = 'analyst_request' "
        "AND requestor_id = :id AND rwb_job_type = 'submit_grouping' "
        "AND status_code IN ('pending', 'running')",
        {"id": str(grouping_request_id)}, connection="WORKBENCH")
    return bool(rows)


__all__ = [
    "DEFAULT_SIMULATION_PERIODS",
    "SIMULATION_PERIOD_OPTIONS",
    "GroupMember",
    "GroupingInspectionView",
    "build_group_name",
    "finish_blockers",
    "grouping_request_is_live",
    "inspect_grouping",
    "list_eligible_members",
    "request_grouping",
    "requested_group_name",
]
