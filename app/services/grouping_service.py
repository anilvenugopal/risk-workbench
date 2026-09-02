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


@dataclass(frozen=True)
class GroupMember:
    """One eligible pick-list member (contracts/routes.md — GET context)."""
    id: str
    irp_id: int | None        # Platform analysisId — the grouping member key (T-10)
    name: str | None          # the ≤64-char name known to Risk Modeler
    display_name: str | None  # full_name where one exists
    kind: str                 # own | broker | group
    engine: str | None        # pick-list disclosure column

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]


@dataclass(frozen=True)
class GroupingInspectionView:
    """The inspect fragment's context: the package inspection, the picked
    members keyed by Platform id (display names), and the simulation-count
    prefill — the largest member PLT length for a PLT group, 1 for an ELT
    group (FR-019)."""
    inspection: irp_gateway.GroupingInspection
    members: dict[int, GroupMember]
    suggested_num_of_simulations: int


_ELIGIBLE_SELECT = """
    SELECT a.id, a.name, a.full_name, a.is_group, a.settings_metadata,
           a.rdm_id, a.irp_id, a.inserted_at
    FROM irp_analysis a
    JOIN submission_edm se ON se.edm_id = a.edm_id
    JOIN irp_edm e ON e.id = a.edm_id AND e.deleted_at IS NULL
    WHERE se.submission_id = :sid AND a.rdm_id IS NULL
      AND a.status_code = 'ready' AND a.deleted_at IS NULL
    UNION ALL
    SELECT a.id, a.name, a.full_name, a.is_group, a.settings_metadata,
           a.rdm_id, a.irp_id, a.inserted_at
    FROM irp_analysis a
    JOIN submission_rdm sr ON sr.rdm_id = a.rdm_id
    JOIN irp_rdm r ON r.id = a.rdm_id AND r.deleted_at IS NULL
    WHERE sr.submission_id = :sid AND a.deleted_at IS NULL
    UNION ALL
    SELECT a.id, a.name, a.full_name, a.is_group, a.settings_metadata,
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
    from app.services.analysis_service import (
        _to_display,  # noqa: PLC0415 — display only; avoids a cycle
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
        display = _to_display(_parse_json_dict(r["settings_metadata"],
                                               "settings_metadata"))
        members.append(GroupMember(
            id=_uid(r["id"]),
            irp_id=(int(r["irp_id"]) if r["irp_id"] is not None else None),
            name=r["name"],
            display_name=r["full_name"] or r["name"], kind=kind,
            engine=("Group" if is_group else display.engine)))
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
    periods = [r.periods for m in inspection.members for r in m.regions
               if r.framework == "PLT" and r.periods]
    suggested = (max(periods)
                 if inspection.output_loss_table == "PLT" and periods else 1)
    return GroupingInspectionView(
        inspection=inspection, members={m.irp_id: m for m in picked},
        suggested_num_of_simulations=suggested)


_SELECTION_KEY = ("peril_code", "region_code", "model_version")


def _parse_selections(raw: list[str]) -> list[dict] | None:
    """The posted ``event_rate_selection`` option values as plan entries, or
    ``None`` when any is malformed or two name the same partition."""
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
        scheme_id = data.get("event_rate_scheme_id")
        if (not all(isinstance(k, str) and k for k in key)
                or not isinstance(scheme_id, int) or isinstance(scheme_id, bool)
                or key in seen):
            return None
        seen.add(key)
        selections.append({**dict(zip(_SELECTION_KEY, key, strict=True)),
                           "event_rate_scheme_id": scheme_id})
    return selections


def request_grouping(
    *, submission_id: Any, submission_name: str, member_ids: list[str],
    group_name: str, currency_code: str = "", currency_scheme: str = "",
    currency_vintage: str = "", propagate_detailed_output: bool = True,
    num_of_simulations: str, event_rate_selections: list[str],
    expected_inspection_fingerprint: str, inspected_analysis_ids: list[str],
    actor_id: Any,
) -> str:
    """Validate the posted selection, compose the plan once, persist it on a
    fresh ``submit_grouping`` ``rwb_job`` and dispatch. Raises
    ``ExecutionGateError`` on any validation failure — no partial persistence
    (SC-005). Returns the new ``grouping_request_id``.

    Which partitions require an event-rate selection is not re-derived here
    (that needs another inspection); the package validates it at submit and
    the worker records the structured reason."""
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
    if simulations <= 0:
        errors.append("Enter a simulation count greater than zero.")
    selections = _parse_selections(event_rate_selections)
    if selections is None:
        errors.append("Choose an event-rate scheme for every conflicting partition.")
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
    "GroupMember",
    "GroupingInspectionView",
    "build_group_name",
    "grouping_request_is_live",
    "inspect_grouping",
    "list_eligible_members",
    "request_grouping",
]
