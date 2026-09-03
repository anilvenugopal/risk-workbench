"""Analysis service — the read models over ``irp_analysis``.

**No analysis is attributed to a portfolio**: there is no trustworthy way to tie
an RDM analysis to an EDM portfolio, and every broker-provided analysis carries
``rdm_id`` NOT NULL. ``irp_analysis.exposure_resource_id`` is still captured by
the worker — it is defensible only for analyses CIC runs itself — but nothing
reads or displays it.

The curated ``AnalysisSettings`` view model reads the documented RM payload
fields defensively (``analysisType``/``engineType``/``engineVersion``/
``peril``/``subperil``/``region``/``currencyCode``/…); term / PLA / event-rate
fields have NO documented source and stay blank until the sandbox confirms
their spelling.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.services import irp_gateway, rwb_job_service
from app.services._common import (
    CONDENSED_RETURN_PERIODS,
    STORED_RETURN_PERIODS,
    _parse_json_dict,
    _rm_ui_root,
    _uid,
    _utcnow,
)
from app.workers import dispatch
from db import execute, execute_command, execute_one, get_connection

logger = logging.getLogger(__name__)


@dataclass
class AnalysisSettings:
    analysis_type: str | None = None
    analysis_mode: str | None = None
    framework: str | None = None
    engine_type: str | None = None
    engine_version: str | None = None
    peril: str | None = None
    peril_secondary: str | None = None
    region: str | None = None
    currency: str | None = None
    construction: str | None = None
    line_of_business: str | None = None
    term: str | None = None
    pla: str | None = None
    event_rate_scheme: str | None = None
    rate_vintage: str | None = None

    @property
    def engine(self) -> str | None:
        """The compact Engine column: ``DLM · 23.0`` (either half optional)."""
        parts = [p for p in (self.engine_type, self.engine_version) if p]
        return " · ".join(parts) if parts else None


def _fmt_loss(value: Any) -> str:
    """Display formatting for a stored loss number: values ≥ 1M read ``4.1M``,
    smaller values read as thousands-separated integers, missing values read
    ``—``. Never a recomputation — the verbatim number rides beside it in
    ``title``/``data-value``."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:,.1f}M"
    return f"{value:,.0f}"


@dataclass
class PerspectiveResults:
    code: str
    label: str
    produced: bool
    aal: float | None = None
    std_dev: float | None = None
    # condensed rows, largest return period first:
    # {rp, oep, aep, oep_display, aep_display}
    rows: list[dict] = field(default_factory=list)

    @property
    def aal_display(self) -> str:
        return _fmt_loss(self.aal)

    @property
    def std_dev_display(self) -> str:
        return _fmt_loss(self.std_dev)


@dataclass
class SubmittedSettings:
    """The expanded row's Analysis settings group, read from the submit-time
    snapshot ``irp_analysis.submitted_settings``. Broker rows have no snapshot
    at all, which the row renders as *not returned*."""
    construction_occupancy: str | None = None
    # submitted_settings.currency.code — the own row's pairing-guard value.
    currency: str | None = None
    # spec 012 — the group row's member analyses, in approved-plan order.
    member_names: list[str] = field(default_factory=list)


def _submitted_view(raw: Any) -> SubmittedSettings:
    p = _parse_json_dict(raw, "submitted_settings")
    if not p:
        return SubmittedSettings()
    unknown = p.get("treat_construction_occupancy_as_unknown")
    currency = p.get("currency")
    members = p.get("members")
    if not isinstance(members, list):
        members = []
    names = [m.get("display_name") or m.get("name") for m in members
             if isinstance(m, dict)]
    return SubmittedSettings(
        construction_occupancy=("Treat as unknown" if unknown
                                else _text(unknown)),
        currency=(_text(currency.get("code"))
                  if isinstance(currency, dict) else None),
        member_names=[n for n in names if n],
    )


# The perspective every results view opens on. The label repeats the
# analysis_perspective_kind seed so the merged analyses grid can name the
# perspective its AAL column holds without a query per render.
DEFAULT_PERSPECTIVE = "RL"
DEFAULT_PERSPECTIVE_LABEL = "Pre-Cat Net"


def list_analysis_perspectives() -> list[dict]:
    """The five perspective codes/labels in dropdown order. ``sort_order`` is
    that dropdown order, not the default — ``DEFAULT_PERSPECTIVE`` names it."""
    return [dict(r) for r in execute(
        "SELECT code, label FROM analysis_perspective_kind ORDER BY sort_order",
        {}, connection="WORKBENCH")]


def _perspective_results(loss_results_raw: Any, perspectives: list[dict],
                         return_periods: tuple | None = None,
                         ) -> list[PerspectiveResults]:
    doc = _parse_json_dict(loss_results_raw, "loss_results")
    if not doc:
        return []
    if return_periods is None:
        return_periods = CONDENSED_RETURN_PERIODS
    stored = doc.get("perspectives") or {}
    out: list[PerspectiveResults] = []
    for p in perspectives:
        data = stored.get(p["code"])
        if not data:
            out.append(PerspectiveResults(code=p["code"], label=p["label"],
                                          produced=False))
            continue
        oep, aep = data.get("oep") or {}, data.get("aep") or {}
        rows = [{"rp": f"{rp:,}",
                 "oep": oep.get(str(rp)), "aep": aep.get(str(rp)),
                 "oep_display": _fmt_loss(oep.get(str(rp))),
                 "aep_display": _fmt_loss(aep.get(str(rp)))}
                for rp in sorted(return_periods, reverse=True)]
        out.append(PerspectiveResults(
            code=p["code"], label=p["label"], produced=True,
            aal=data.get("aal"), std_dev=data.get("std_dev"), rows=rows))
    return out


@dataclass
class BrokerAnalysis:
    """One broker analysis (deduped across its M (RDM×EDM) handle rows)."""
    id: str                      # workbench row id of the representative handle
    irp_id: str                  # Moody's analysisId
    name: str | None
    rdm_id: str
    rdm_name: str | None         # source-RDM name
    is_group: bool = False
    settings: dict | None = None            # parsed raw snapshot (R2)
    display: AnalysisSettings = field(default_factory=AnalysisSettings)
    rm_url: str | None = None    # Risk Modeler link-out from the snapshot's
                                 # appAnalysisId, as own rows build theirs (FR-025)
    created_at: Any = None       # RM createDate — the broker's own run date (FR-024)
    results_state: str = "pending"      # pending | failed | ready
    results_error: str | None = None    # failed retrieval's error_detail
    results: list[PerspectiveResults] = field(default_factory=list)  # [] until ready


@dataclass
class BrokerAnalysisGroup:
    """Analyses under one source-RDM divider (both pages render per-RDM groups)."""
    rdm_id: str
    rdm_name: str | None
    rdm_irp_id: Any
    status: str | None = None
    analysis_count: int = 0
    analyses: list[BrokerAnalysis] = field(default_factory=list)
    # A backfill_rdm_analyses head is pending/running for this RDM — the EDM
    # detail page's contextual broker-analyses section polls while any group
    # carries this, so an RDM's own capture finishing after the EDM's own
    # backfill still lands without a manual refresh.
    sync_running: bool = False


# ExecutedAnalysis.run_state -> the status-chip variant that renders it.
_CHIP_BY_RUN_STATE = {
    "submitting": "importing",
    "running": "importing",
    "retrying": "importing",
    "finished": "ready",
    "submit_failed": "submission-failed",
    "failed": "error",
}


@dataclass
class ExecutedAnalysis:
    id: str
    name: str | None            # the ≤64-char name submitted to Risk Modeler
    full_name: str | None       # untruncated portfolio + template (+ suffix)
    portfolio_name: str | None
    status_code: str            # pending | ready | error
    failure_reason: str | None
    template_name: str | None = None
    run_by: str | None = None   # app_user.display_name of the submitting analyst
    edm_name: str | None = None  # submission-wide reads only (FR-009 EDM column)
    inserted_at: Any = None     # submit request time (Submitted column)
    irp_id: str | None = None   # RM analysisId; backfilled after FINISHED
    irp_app_analysis_id: str | None = None  # RM appAnalysisId; web-UI id
    rm_url: str | None = None   # Risk Modeler link-out; None without irp_app_analysis_id
    settings: dict | None = None
    display: AnalysisSettings = field(default_factory=AnalysisSettings)
    irp_job_id: str | None = None       # latest linked irp_job
    job_status: str | None = None       # latest irp_job.status; None before submit
    submission_attempt_count: int = 0
    is_group: bool = False              # spec 012 — Engine cell reads "Group"
    results_state: str = "pending"      # pending | failed | ready
    results_error: str | None = None    # failed retrieval's error_detail
    results: list[PerspectiveResults] = field(default_factory=list)  # [] until ready
    submitted: SubmittedSettings = field(default_factory=SubmittedSettings)
    # The submit-time run currency (submitted_settings.currency.code, FR-005).
    run_currency: str | None = None

    @property
    def is_live(self) -> bool:
        """Drives the EDM page's 3s self-poll: still moving toward a terminal
        outcome. ``pending`` is the only in-flight run status — every write that
        leaves it is terminal. A ready run whose retrieval is still pending
        keeps polling so the loss numbers land with no analyst action; a failed
        retrieval is terminal."""
        return (self.status_code == "pending"
                or (self.status_code == "ready"
                    and self.results_state == "pending"))

    @property
    def run_state(self) -> str:
        """Where the run stands, derived from the mirrored ``irp_job.status``:

        ``submitting``     no ``irp_job`` row yet
        ``running``        Risk Modeler accepted it (PENDING/QUEUED/RUNNING)
        ``retrying``       a submit attempt failed, attempts remain
        ``submit_failed``  the submit attempts ran out
        ``finished``       the run finished in Risk Modeler
        ``failed``         FAILED or CANCELLED in Risk Modeler

        Read this, not ``status_chip`` — the chip is one rendering of it.
        Note ``finished`` is not the same as deletable or grouped under Ready:
        both of those also wait on the backfill (``status_code``)."""
        if self.job_status is None:
            return "submitting"
        if self.job_status in ("PENDING", "QUEUED", "RUNNING"):
            return "running"
        if self.job_status == "SUBMISSION RETRYING":
            return "retrying"
        if self.job_status == "SUBMISSION FAILED":
            return "submit_failed"
        if self.job_status == "FINISHED":
            return "finished"
        return "failed"  # FAILED, CANCELLED

    @property
    def status_label(self) -> str:
        if self.run_state == "submitting":
            return "Submitting…"
        if self.run_state == "submit_failed":
            return (f"Failed to submit · attempt {self.submission_attempt_count}/"
                    f"{settings.irp_submission_max_retries}")
        return self.job_status.capitalize()

    @property
    def status_chip(self) -> str:
        """The ``status-chip--*`` modifier for ``run_state``. Analyses reuse the
        EDM/RDM import chip variants in submissions.css rather than adding a
        second set of colors, so the class names do not match the states."""
        return _CHIP_BY_RUN_STATE[self.run_state]

    @property
    def group_key(self) -> str:
        """The Analyses grid's group: ``failed`` / ``in_progress`` / ``ready``.
        A run out of submit attempts is ``status_code='pending'`` but belongs
        under Failed; a retrying one still belongs under In progress."""
        if self.status_code == "error" or self.run_state in (
                "failed", "submit_failed"):
            return "failed"
        if self.status_code == "ready":
            return "ready"
        return "in_progress"

    @property
    def is_deletable(self) -> bool:
        """Terminal rows only. Deliberately NOT ``run_state == 'finished'``: the
        run finishes seconds before the backfill writes ``irp_id`` and flips
        ``status_code`` — deleting in that window would orphan the RM
        analysis. A retrying row is still in flight, whatever ``status_code``
        says."""
        return (self.run_state != "retrying"
                and (self.status_code in ("ready", "error")
                     or self.run_state == "submit_failed"))


def _parse_settings(raw: Any) -> dict | None:
    return _parse_json_dict(raw, "settings_metadata")


def _first(payload: dict, *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return None


def _text(value: Any) -> str | None:
    """A display string from a defensive read: a reference object collapses to
    its ``name``, then its ``code``; the ``currency`` object is keyed
    ``currencyCode``/``currencyName`` and collapses to its code. Lists join,
    empty → blank; bools to On/Off."""
    if value is None:
        return None
    if isinstance(value, dict):
        return (value.get("name") or value.get("code")
                or value.get("currencyCode") or value.get("currencyName")
                or None)
    if isinstance(value, (list, tuple)):
        parts = [t for t in (_text(v) for v in value) if t]
        return ", ".join(parts) or None
    if isinstance(value, bool):
        return "On" if value else "Off"
    return str(value)


def _event_rate_scheme(p: dict) -> str | None:
    """Own analyses list their scheme in ``eventRateSchemeNames``. A group's
    list is empty; its schemes (one per member region/peril) sit in
    ``additionalProperties`` under key ``eventRateSchemes``, each property's
    ``value`` an object with ``eventRateSchemeName``."""
    named = _text(p.get("eventRateSchemeNames"))
    if named:
        return named
    for prop in p.get("additionalProperties") or []:
        if isinstance(prop, dict) and prop.get("key") == "eventRateSchemes":
            names = []
            for entry in prop.get("properties") or []:
                value = entry.get("value") if isinstance(entry, dict) else None
                name = value.get("eventRateSchemeName") if isinstance(value, dict) else None
                if name and name not in names:
                    names.append(name)
            return ", ".join(names) or None
    return None


def _to_display(settings: dict | None) -> AnalysisSettings:
    p = settings or {}
    return AnalysisSettings(
        analysis_type=_text(_first(p, "analysisType", "type")),
        analysis_mode=_text(_first(p, "analysisMode", "mode")),
        framework=_text(_first(p, "analysisFramework")),
        engine_type=_text(_first(p, "engineType")),
        engine_version=_text(_first(p, "engineVersion", "modelVersion")),
        peril=_text(_first(p, "perilCode", "peril")),
        peril_secondary=_text(_first(p, "subperil", "subPeril", "secondaryPeril")),
        region=_text(_first(p, "regionCode", "region")),
        currency=_text(_first(p, "currencyCode", "currencyName", "currency")),
        construction=_text(_first(p, "construction")),
        line_of_business=_text(_first(p, "lineOfBusiness", "lob")),
        term=_text(_first(p, "term", "timeDependency", "rateTimeDependency")),
        pla=_text(_first(p, "lossAmplification", "pla", "plaEnabled")),
        event_rate_scheme=_event_rate_scheme(p),
        rate_vintage=_text(_first(p, "rateVintage", "eventRateSchemeVersion")),
    )


# One row per (RDM×EDM) handle.
_HANDLE_SELECT = """
    SELECT a.id, a.rdm_id, a.irp_id, a.name, a.is_group, a.settings_metadata,
           a.loss_results, r.name AS rdm_name, r.irp_id AS rdm_irp_id
    FROM irp_analysis a
    LEFT JOIN irp_rdm r ON r.id = a.rdm_id
    WHERE a.deleted_at IS NULL AND a.rdm_id IS NOT NULL
"""


def _mark_failed_retrievals(analyses: list) -> None:
    """Flip still-pending rows whose retrieval ``rwb_job`` ended ``failed`` to
    failed + reason while the run status stays untouched. A terminal failed row
    is never resurrected by the dedup key, so the join is exact, not
    latest-of-many."""
    pending = [a for a in analyses if a.results_state == "pending"]
    if not pending:
        return
    params = {f"a{i}": a.id for i, a in enumerate(pending)}
    placeholders = ", ".join(f":a{i}" for i in range(len(pending)))
    failed = execute(
        f"SELECT requestor_id, error_detail FROM rwb_job "
        f"WHERE requestor_type = 'irp_analysis' "
        f"AND rwb_job_type = 'retrieve_analysis_results' "
        f"AND status_code = 'failed' "
        f"AND requestor_id IN ({placeholders})",
        params, connection="WORKBENCH")
    by_id = {_uid(row["requestor_id"]): row["error_detail"] for row in failed}
    for a in pending:
        if a.id in by_id:
            a.results_state = "failed"
            a.results_error = by_id[a.id]


def _dedup_handles(rows: list[dict]) -> list[BrokerAnalysis]:
    """Collapse the M (RDM×EDM) handle rows sharing one ``irp_id`` into ONE
    display row (R8): the representative handle is the first seen; settings and
    results come from any handle that has them (both snapshots are
    per-analysis). No EDM is read off the handles — an RDM is related to an EDM
    only through a submission, so a broker analysis names no EDM."""
    out: list[BrokerAnalysis] = []
    by_key: dict[tuple, BrokerAnalysis] = {}
    perspectives = list_analysis_perspectives() if rows else []
    for r in rows:
        key = (_uid(r["rdm_id"]), str(r["irp_id"]))
        existing = by_key.get(key)
        if existing is None:
            settings = _parse_settings(r["settings_metadata"])
            results = _perspective_results(r["loss_results"], perspectives)
            entry = BrokerAnalysis(
                id=_uid(r["id"]), irp_id=str(r["irp_id"]), name=r["name"],
                rdm_id=_uid(r["rdm_id"]), rdm_name=r["rdm_name"],
                is_group=bool(r["is_group"]), settings=settings,
                display=_to_display(settings),
                rm_url=_rm_analysis_url((settings or {}).get("appAnalysisId")),
                created_at=(settings or {}).get("createDate"),
                results_state=("ready" if results else "pending"),
                results=results)
            by_key[key] = entry
            out.append(entry)
            continue
        if existing.settings is None:
            settings = _parse_settings(r["settings_metadata"])
            if settings is not None:
                existing.settings = settings
                existing.display = _to_display(settings)
                existing.rm_url = _rm_analysis_url(settings.get("appAnalysisId"))
                existing.created_at = settings.get("createDate")
        if not existing.results:
            results = _perspective_results(r["loss_results"], perspectives)
            if results:
                existing.results = results
                existing.results_state = "ready"
    _mark_failed_retrievals(out)
    return out


def _group_by_rdm(rows: list[dict]) -> list[BrokerAnalysisGroup]:
    analyses = _dedup_handles(rows)
    rdm_meta = {_uid(r["rdm_id"]): (r["rdm_name"], r["rdm_irp_id"])
                for r in rows}
    groups: dict[str, BrokerAnalysisGroup] = {}
    ordered: list[BrokerAnalysisGroup] = []
    for a in analyses:
        g = groups.get(a.rdm_id)
        if g is None:
            name, irp = rdm_meta.get(a.rdm_id, (None, None))
            g = BrokerAnalysisGroup(rdm_id=a.rdm_id, rdm_name=name,
                                    rdm_irp_id=irp)
            groups[a.rdm_id] = g
            ordered.append(g)
        g.analyses.append(a)
    return ordered


def list_broker_analyses(*, rdm_id: Any) -> list[BrokerAnalysisGroup]:
    rows = execute(
        f"{_HANDLE_SELECT} AND a.rdm_id = :r ORDER BY a.name, a.irp_id, a.id",
        {"r": str(rdm_id)}, connection="WORKBENCH")
    return _group_by_rdm([dict(r) for r in rows])


def list_edm_analyses(*, edm_id: Any) -> list[BrokerAnalysisGroup]:
    """Direct library EDM pages have no submission context and show no RDM list."""
    return []


# The analysis' latest tracked irp_job (T-07) — the row that carries the status
# label and the submission attempt count. Joined by both own-executed reads.
_LATEST_JOB_JOIN = """
    LEFT JOIN (
        SELECT id, irp_analysis_id, status, submission_attempt_count,
               ROW_NUMBER() OVER (
                   PARTITION BY irp_analysis_id
                   ORDER BY inserted_at DESC, id DESC
               ) AS row_num
        FROM irp_job
        WHERE irp_analysis_id IS NOT NULL
    ) j ON j.irp_analysis_id = a.id AND j.row_num = 1
"""

_EXECUTED_SELECT = f"""
    SELECT a.id, a.name, a.full_name, a.status_code, a.failure_reason,
           a.settings_metadata, a.inserted_at, a.irp_id, a.irp_app_analysis_id,
           a.loss_results, a.submitted_settings,
           p.name AS portfolio_name, t.name AS template_name,
           u.display_name AS run_by,
           j.id AS irp_job_id, j.status AS job_status,
           j.submission_attempt_count
    FROM irp_analysis a
    LEFT JOIN irp_portfolio p ON p.id = a.irp_portfolio_id
    LEFT JOIN analysis_template t ON t.id = a.analysis_template_id
    LEFT JOIN app_user u ON u.id = a.inserted_by
    {_LATEST_JOB_JOIN}
    WHERE a.edm_id = :edm_id AND a.execution_id IS NOT NULL AND a.deleted_at IS NULL
    ORDER BY a.inserted_at DESC
"""


def _rm_analysis_url(irp_app_analysis_id: Any) -> str | None:
    """The Risk Modeler web UI page for one analysis — plain navigation, never
    an API call (Article 11). The RM UI route takes ``appAnalysisId``, not the
    API ``analysisId``. ``None`` without an ``irp_app_analysis_id`` or when the RM
    UI origin is not configured. The trailing ``/0`` is part of the RM UI
    route."""
    if not irp_app_analysis_id:
        return None
    root = _rm_ui_root()
    if root is None:
        return None
    return f"{root}/riskmodeler/datasources/analysis/{irp_app_analysis_id}/0"


def _executed_models(rows: list[dict]) -> list[ExecutedAnalysis]:
    """Own-executed rows as display models: parsed settings, the stored
    extract, the latest tracked ``irp_job`` status (T-07), and the failed
    retrieval join (SC-005)."""
    analyses = []
    perspectives = list_analysis_perspectives() if rows else []
    for r in rows:
        parsed = _parse_settings(r["settings_metadata"])
        irp_id = str(r["irp_id"]) if r["irp_id"] is not None else None
        irp_app_analysis_id = (str(r["irp_app_analysis_id"])
                               if r["irp_app_analysis_id"] is not None else None)
        results = _perspective_results(r["loss_results"], perspectives)
        submitted = _submitted_view(r["submitted_settings"])
        analyses.append(ExecutedAnalysis(
            id=_uid(r["id"]), name=r["name"], full_name=r["full_name"],
            portfolio_name=r["portfolio_name"], status_code=r["status_code"],
            failure_reason=r["failure_reason"],
            template_name=r["template_name"], run_by=r["run_by"],
            inserted_at=r["inserted_at"],
            edm_name=r.get("edm_name"),
            irp_id=irp_id, irp_app_analysis_id=irp_app_analysis_id,
            rm_url=_rm_analysis_url(irp_app_analysis_id), settings=parsed,
            display=_to_display(parsed),
            irp_job_id=(_uid(r["irp_job_id"]) if r["irp_job_id"] else None),
            job_status=r["job_status"],
            submission_attempt_count=int(r["submission_attempt_count"] or 0),
            is_group=bool(r.get("is_group")),
            results_state=("ready" if results else "pending"), results=results,
            submitted=submitted, run_currency=submitted.currency))
    _mark_failed_retrievals(analyses)
    return analyses


def list_executed_analyses(*, edm_id: Any) -> list[ExecutedAnalysis]:
    rows = execute(_EXECUTED_SELECT, {"edm_id": str(edm_id)}, connection="WORKBENCH")
    return _executed_models([dict(r) for r in rows])


_SUBMISSION_EXECUTED_SELECT = f"""
    SELECT a.id, a.name, a.full_name, a.status_code, a.failure_reason,
           a.settings_metadata, a.inserted_at, a.irp_id, a.irp_app_analysis_id,
           a.loss_results, a.submitted_settings, a.is_group,
           p.name AS portfolio_name, t.name AS template_name,
           e.name AS edm_name, u.display_name AS run_by,
           j.id AS irp_job_id, j.status AS job_status,
           j.submission_attempt_count
    FROM irp_analysis a
    JOIN submission_edm se ON se.edm_id = a.edm_id
    JOIN irp_edm e ON e.id = a.edm_id
    LEFT JOIN irp_portfolio p ON p.id = a.irp_portfolio_id
    LEFT JOIN analysis_template t ON t.id = a.analysis_template_id
    LEFT JOIN app_user u ON u.id = a.inserted_by
    {_LATEST_JOB_JOIN}
    WHERE se.submission_id = :submission_id AND a.rdm_id IS NULL
      AND e.deleted_at IS NULL AND a.deleted_at IS NULL
    UNION ALL
    SELECT a.id, a.name, a.full_name, a.status_code, a.failure_reason,
           a.settings_metadata, a.inserted_at, a.irp_id, a.irp_app_analysis_id,
           a.loss_results, a.submitted_settings, a.is_group,
           NULL AS portfolio_name, NULL AS template_name,
           NULL AS edm_name, u.display_name AS run_by,
           j.id AS irp_job_id, j.status AS job_status,
           j.submission_attempt_count
    FROM irp_analysis a
    LEFT JOIN app_user u ON u.id = a.inserted_by
    {_LATEST_JOB_JOIN}
    WHERE a.submission_id = :submission_id AND a.is_group = 1
      AND a.deleted_at IS NULL
    ORDER BY inserted_at DESC
"""


def list_submission_executed_analyses(
    *, submission_id: Any,
) -> list[ExecutedAnalysis]:
    rows = execute(_SUBMISSION_EXECUTED_SELECT,
                   {"submission_id": str(submission_id)}, connection="WORKBENCH")
    return _executed_models([dict(r) for r in rows])


def execution_batch_is_live(execution_id: Any | None) -> bool:
    if not execution_id:
        return False
    rows = execute(
        "SELECT status_code FROM rwb_job "
        "WHERE requestor_type = 'analyst_request' AND requestor_id = :id "
        "AND rwb_job_type = 'execute_analysis_batch' "
        "AND status_code IN ('pending', 'running')",
        {"id": str(execution_id)}, connection="WORKBENCH")
    return bool(rows)


@dataclass(frozen=True)
class DeleteOutcome:
    deleted: int
    failed: list[str]  # display names whose Risk Modeler delete failed
    retrying: list[str]  # display names the poller claimed for a submission retry


_SOFT_DELETE_ANALYSIS = (
    "UPDATE irp_analysis SET deleted_at = :now, updated_at = :now, "
    "updated_by = :by WHERE id = :id AND deleted_at IS NULL"
)


def _delete_analyses(rows: list[ExecutedAnalysis], analysis_ids: list[Any],
                     actor_id: Any, foreign_message: str) -> DeleteOutcome:
    """Delete terminal analyses (spec 010 P-19): validate the whole batch up
    front (every posted id must resolve among ``rows`` and be ``is_deletable``,
    else ``ValueError``), then per row cascade to Risk Modeler first and
    soft-delete locally on success. A row whose RM delete fails is recorded in
    ``failed`` and kept visible for retry; a row the poller claimed for a
    submission retry mid-batch is recorded in ``retrying`` and left alone.
    Neither aborts the batch — the rows already deleted stay deleted, and the
    caller reports all three counts. RM-first order: a crash between the two
    calls leaves a visible row with a dangling ``irp_id`` — recoverable by
    retrying — rather than a hidden RM analysis."""
    ids = [i for i in dict.fromkeys(_uid(a) for a in analysis_ids) if i]
    if not ids:
        raise ValueError("No analyses selected.")
    by_id = {a.id: a for a in rows}
    picked = []
    for analysis_id in ids:
        row = by_id.get(analysis_id)
        if row is None:
            raise ValueError(foreign_message)
        if not row.is_deletable:
            raise ValueError(
                f"'{row.full_name or row.name}' is still in progress "
                "and cannot be deleted.")
        picked.append(row)

    deleted = 0
    failed: list[str] = []
    retrying: list[str] = []
    for row in picked:
        if row.irp_id is not None:
            # Outside any transaction (Article 11 — never hold a txn across
            # a Risk Modeler round-trip).
            try:
                irp_gateway.delete_analysis(row.irp_id)
            except Exception:  # noqa: BLE001 — per-row isolation; row stays for retry
                logger.exception("Risk Modeler delete failed for analysis %s "
                                 "(irp_id=%s)", row.id, row.irp_id)
                failed.append(row.full_name or row.name or row.id)
                continue
        soft_delete = {"now": _utcnow(), "id": row.id,
                       "by": (str(actor_id) if actor_id is not None else None)}
        if row.job_status == "SUBMISSION FAILED" and row.irp_job_id:
            with get_connection("WORKBENCH") as conn, conn.begin():
                locked = conn.execute(text(
                    "UPDATE irp_job SET updated_at = :now "
                    "WHERE id = :job_id AND status = 'SUBMISSION FAILED' "
                    "AND EXISTS (SELECT 1 FROM irp_analysis "
                    "WHERE id = :analysis_id AND deleted_at IS NULL)"
                ), {"now": soft_delete["now"], "job_id": row.irp_job_id,
                    "analysis_id": row.id}).rowcount
                if locked != 1:
                    # The poller claimed this submit for a retry after the read
                    # above. Nothing was deleted for this row — its irp_id is
                    # NULL, so Risk Modeler was never called — and raising here
                    # would discard the rows already deleted earlier in the loop.
                    retrying.append(row.full_name or row.name or row.id)
                    continue
                conn.execute(text(_SOFT_DELETE_ANALYSIS), soft_delete)
        else:
            execute_command(_SOFT_DELETE_ANALYSIS, soft_delete,
                            connection="WORKBENCH")
        deleted += 1
    return DeleteOutcome(deleted=deleted, failed=failed, retrying=retrying)


def delete_executed_analyses(*, edm_id: Any, analysis_ids: list[Any],
                             actor_id: Any) -> DeleteOutcome:
    """The EDM page's Analyses grid: own analyses executed from one EDM."""
    return _delete_analyses(
        list_executed_analyses(edm_id=edm_id), analysis_ids, actor_id,
        "A selected analysis no longer belongs to this EDM.")


def delete_submission_analyses(*, submission_id: Any, analysis_ids: list[Any],
                               actor_id: Any) -> DeleteOutcome:
    """The submission page's Results grid: own analyses across every EDM of the
    deal, plus its group rows (spec 012 contracts/routes.md — a group carries
    ``submission_id`` and no ``edm_id``, so this is the only grid it can be
    deleted from). Broker rows are not in the candidate set, so posting one
    raises the same ``ValueError`` an unrelated id does."""
    return _delete_analyses(
        list_submission_executed_analyses(submission_id=submission_id),
        analysis_ids, actor_id,
        "A selected analysis no longer belongs to this deal.")


def retry_results_retrieval(*, analysis_id: Any, actor_id: Any) -> str | None:
    """The row's Retry (spec 011 FR-007, T-11): revive the analysis's own
    ``retrieve_analysis_results`` job in place. The key is the one
    ``finalize_analysis`` and ``backfill_rdm_analyses`` enqueue under, so the
    failed row itself goes back to ``pending`` and ``_mark_failed_retrievals``
    stops matching it. Raises ``LookupError`` for an unknown or deleted analysis
    and ``ValueError`` when results are already stored; returns ``None`` when
    the retrieval is already pending or running."""
    aid = _uid(analysis_id)
    row = execute_one(
        "SELECT loss_results FROM irp_analysis "
        "WHERE id = :id AND deleted_at IS NULL",
        {"id": aid}, connection="WORKBENCH")
    if row is None:
        raise LookupError(aid)
    if row["loss_results"] is not None:
        raise ValueError("Results are already stored.")
    job_id = rwb_job_service.ensure_pending_rwb_job(
        requestor_type="irp_analysis", requestor_id=aid,
        rwb_job_type="retrieve_analysis_results",
        input_data={"analysis_id": aid}, actor_id=actor_id)
    dispatch.dispatch(rwb_job_id=job_id,
                      rwb_job_type="retrieve_analysis_results")
    return job_id


@dataclass
class ResultsColumn:
    id: str
    name: str | None
    currency: str | None
    results_state: str = "pending"      # pending | failed | ready
    results_error: str | None = None
    results: list[PerspectiveResults] = field(default_factory=list)
    # The extract's engine snapshot (spec 011 FR-021), e.g. "DLM · 23.0".
    engine: str | None = None
    # Own rows: submitted_settings.currency.code; broker rows: the
    # settings_metadata currency (FR-005). The pairing guard's value.
    run_currency: str | None = None

    def for_code(self, code: str) -> PerspectiveResults | None:
        return next((p for p in self.results if p.code == code), None)


def expanded_return_periods() -> list[str]:
    """The dedicated page's row labels — the 11 stored return periods, largest
    first, matching the row order ``_perspective_results`` builds."""
    return [f"{rp:,}" for rp in sorted(STORED_RETURN_PERIODS, reverse=True)]


def list_results_columns(*, analysis_ids: list[Any],
                         ) -> tuple[list[ResultsColumn], int]:
    """The dedicated page's columns: one per resolved id, in the caller's
    order, both origins in one read. Returns the columns and the count of ids
    that did not resolve — an unknown or deleted id is a notice on the page,
    never an error."""
    requested: list[str | None] = []
    for raw in analysis_ids:
        try:
            requested.append(str(uuid.UUID(str(raw))))
        except (ValueError, AttributeError, TypeError):
            requested.append(None)
    valid = [i for i in requested if i]
    rows_by_id: dict[str, dict] = {}
    if valid:
        params = {f"a{n}": i for n, i in enumerate(dict.fromkeys(valid))}
        placeholders = ", ".join(f":a{n}" for n in range(len(params)))
        rows = execute(
            f"SELECT a.id, a.name, a.full_name, a.rdm_id, "
            f"a.settings_metadata, a.submitted_settings, a.loss_results "
            f"FROM irp_analysis a "
            f"WHERE a.deleted_at IS NULL AND a.id IN ({placeholders})",
            params, connection="WORKBENCH")
        rows_by_id = {_uid(r["id"]): dict(r) for r in rows}
    perspectives = list_analysis_perspectives() if rows_by_id else []
    columns: list[ResultsColumn] = []
    missing = 0
    for rid in requested:
        row = rows_by_id.get(rid) if rid else None
        if row is None:
            missing += 1
            continue
        results = _perspective_results(row["loss_results"], perspectives,
                                       STORED_RETURN_PERIODS)
        parsed = _parse_settings(row["settings_metadata"])
        display = _to_display(parsed)
        extract = _parse_json_dict(row["loss_results"], "loss_results") or {}
        columns.append(ResultsColumn(
            id=_uid(row["id"]),
            name=row["full_name"] or row["name"],
            currency=display.currency,
            results_state=("ready" if results else "pending"),
            results=results,
            engine=AnalysisSettings(
                engine_type=_text(extract.get("engine_type")),
                engine_version=_text(extract.get("engine_version"))).engine,
            run_currency=(display.currency if row["rdm_id"] is not None
                          else _submitted_view(
                              row["submitted_settings"]).currency)))
    _mark_failed_retrievals(columns)
    return columns, missing


@dataclass
class PairPercent:
    """One pair's percent changes for one perspective — (second − base) / base
    per stored return period, plus AAL and standard deviation. ``None`` cells
    where the base is zero or either value is missing (division undefined —
    never ``inf``)."""
    aal: float | None
    std_dev: float | None
    rows: list[dict]  # {rp, oep, aep} aligned with each side's stored rows


def _pct(base: float | None, second: float | None) -> float | None:
    if base is None or base == 0 or second is None:
        return None
    return (second - base) / base


@dataclass
class ComparisonPair:
    """One rendered pair (data-model.md) — built by ``list_comparison_pairs``,
    never stored. ``base`` is the first-picked analysis (FR-003). ``pct`` holds
    the percent changes for the perspective the page renders."""
    base: ResultsColumn
    second: ResultsColumn
    pct: PairPercent | None = None


def _pair_percent(base_col: ResultsColumn, second_col: ResultsColumn,
                  code: str) -> PairPercent | None:
    """Percent changes for one perspective; ``None`` when either side did not
    produce it (FR-014 — the template renders the em dash)."""
    base = base_col.for_code(code)
    second = second_col.for_code(code)
    if not (base and base.produced and second and second.produced):
        return None
    rows = [{"rp": b["rp"],
             "oep": _pct(b["oep"], s["oep"]),
             "aep": _pct(b["aep"], s["aep"])}
            for b, s in zip(base.rows, second.rows, strict=True)]
    return PairPercent(aal=_pct(base.aal, second.aal),
                       std_dev=_pct(base.std_dev, second.std_dev),
                       rows=rows)


def _valid_uuid(raw: str) -> str | None:
    try:
        return str(uuid.UUID(raw.strip()))
    except ValueError:
        return None


MAX_COMPARISON_PAIRS = 5  # P-02 — the cart's and the render's shared cap


def list_comparison_pairs(*, pairs: str, perspective: str,
                          ) -> tuple[list[ComparisonPair], list[dict]]:
    """Resolve a ``pairs=base:second,…`` query param into rendered pairs, each
    carrying its percent changes for ``perspective``. Only the first
    ``MAX_COMPARISON_PAIRS`` pairs the query asked for are resolved; each pair
    beyond them is dropped. Three build-time validations (T-01) then drop a
    surviving pair whole and record it — ``kind`` ``missing`` (with the
    unresolved ``ids``), ``currency`` (with both ``currencies``), or ``other``
    for equal ids, an unrecorded currency, an id that does not parse, and a
    pair past the cap."""
    parsed: list[tuple[str | None, str | None]] = []
    for token in pairs.split(","):
        left, _, right = token.partition(":")
        if not token.strip():
            continue
        parsed.append((_valid_uuid(left), _valid_uuid(right)))
    drops: list[dict] = [{"kind": "other"}
                         for _ in parsed[MAX_COMPARISON_PAIRS:]]
    parsed = parsed[:MAX_COMPARISON_PAIRS]
    valid_ids = list(dict.fromkeys(
        i for pair in parsed for i in pair if i))
    columns, _ = list_results_columns(analysis_ids=valid_ids)
    by_id = {c.id: c for c in columns}

    out: list[ComparisonPair] = []
    for left, right in parsed:
        base = by_id.get(left) if left else None
        second = by_id.get(right) if right else None
        if base is None or second is None:
            drops.append({"kind": "missing", "ids": [
                i for i, col in ((left, base), (right, second))
                if col is None and i]})
            continue
        if base.id == second.id:
            drops.append({"kind": "other"})
            continue
        if not base.run_currency or not second.run_currency:
            drops.append({"kind": "other"})
            continue
        if base.run_currency != second.run_currency:
            drops.append({"kind": "currency",
                          "currencies": (base.run_currency,
                                         second.run_currency)})
            continue
        out.append(ComparisonPair(
            base=base, second=second,
            pct=_pair_percent(base, second, perspective)))
    return out, drops


@dataclass
class ComparableAnalysis:
    """One Compare-modal row (data-model.md) — the table-at-hand's analyses in
    table order. ``run_currency`` ``None`` renders the row unpairable (P-05);
    only ``ready`` rows are tickable (FR-002)."""
    id: str
    name: str | None
    rdm_name: str | None
    run_currency: str | None
    results_state: str
    # The row's metadata line — settings_metadata via _to_display, so own and
    # broker rows read the same fields.
    event_rate_scheme: str | None = None
    peril: str | None = None
    engine: str | None = None
    submitted_at: Any = None  # own: submit request time; broker: RM createDate


def list_comparable_analyses(
    *, submission_id: Any | None = None, edm_id: Any | None = None,
) -> list[ComparableAnalysis] | None:
    """The Compare modal's list (T-05): own rows then broker rows in table
    order, composed from the table's existing reads so dedup and soft-delete
    rules are inherited. ``None`` when the scope no longer resolves — unknown
    submission or EDM, or an EDM not related to the named submission."""
    if submission_id is not None and edm_id is not None:
        related = execute_one(
            "SELECT 1 AS x FROM submission_edm se "
            "JOIN irp_edm e ON e.id = se.edm_id "
            "WHERE se.submission_id = :s AND se.edm_id = :e "
            "AND e.deleted_at IS NULL",
            {"s": str(submission_id), "e": str(edm_id)},
            connection="WORKBENCH")
        if related is None:
            return None
    elif submission_id is not None:
        if execute_one("SELECT 1 AS x FROM submission WHERE id = :s",
                       {"s": str(submission_id)},
                       connection="WORKBENCH") is None:
            return None
    else:
        if execute_one(
                "SELECT 1 AS x FROM irp_edm WHERE id = :e "
                "AND deleted_at IS NULL",
                {"e": str(edm_id)}, connection="WORKBENCH") is None:
            return None

    if edm_id is not None:
        own = list_executed_analyses(edm_id=edm_id)
    else:
        own = list_submission_executed_analyses(submission_id=submission_id)
    rows = [ComparableAnalysis(
        id=a.id, name=a.full_name or a.name, rdm_name=None,
        run_currency=a.run_currency,
        results_state=a.results_state,
        event_rate_scheme=a.display.event_rate_scheme,
        peril=a.display.peril, engine=a.display.engine,
        submitted_at=a.inserted_at) for a in own]
    if submission_id is not None:
        for group in list_submission_rdms(submission_id=submission_id):
            for a in (list_submission_rdm_analyses(
                    submission_id=submission_id, rdm_id=group.rdm_id) or []):
                rows.append(ComparableAnalysis(
                    id=a.id, name=a.name, rdm_name=group.rdm_name,
                    run_currency=a.display.currency,
                    results_state=a.results_state,
                    event_rate_scheme=a.display.event_rate_scheme,
                    peril=a.display.peril, engine=a.display.engine,
                    submitted_at=a.created_at))
    return rows


def list_submission_rdms(*, submission_id: Any) -> list[BrokerAnalysisGroup]:
    """List one submission's RDMs and stored counts without loading analyses."""
    rows = execute(
        "SELECT r.id, r.name, r.irp_id, r.status, COUNT(a.id) AS analysis_count "
        "FROM submission_rdm sr JOIN irp_rdm r ON r.id = sr.rdm_id "
        "LEFT JOIN irp_analysis a ON a.rdm_id = r.id AND a.deleted_at IS NULL "
        "WHERE sr.submission_id = :submission_id AND r.deleted_at IS NULL "
        "GROUP BY r.id, r.name, r.irp_id, r.status, r.inserted_at "
        "ORDER BY r.inserted_at, r.name",
        {"submission_id": str(submission_id)}, connection="WORKBENCH")
    return [
        BrokerAnalysisGroup(
            rdm_id=_uid(row["id"]), rdm_name=row["name"],
            rdm_irp_id=row["irp_id"], status=row["status"],
            analysis_count=int(row["analysis_count"] or 0))
        for row in rows
    ]


def list_submission_rdm_analyses(
    *, submission_id: Any, rdm_id: Any,
) -> list[BrokerAnalysis] | None:
    """Return stored analyses when the RDM belongs to the named submission.

    ``None`` distinguishes an invalid association from an associated RDM with no
    captured analyses. The query never calls Risk Modeler.
    """
    associated = execute(
        "SELECT r.id FROM submission_rdm sr "
        "JOIN irp_rdm r ON r.id = sr.rdm_id "
        "WHERE sr.submission_id = :submission_id AND sr.rdm_id = :rdm_id "
        "AND r.deleted_at IS NULL",
        {"submission_id": str(submission_id), "rdm_id": str(rdm_id)},
        connection="WORKBENCH")
    if not associated:
        return None
    rows = execute(
        f"{_HANDLE_SELECT} AND a.rdm_id = :rdm_id "
        "ORDER BY a.name, a.irp_id, a.id",
        {"rdm_id": str(rdm_id)}, connection="WORKBENCH")
    return _dedup_handles([dict(row) for row in rows])


__all__ = [
    "DEFAULT_PERSPECTIVE", "DEFAULT_PERSPECTIVE_LABEL", "MAX_COMPARISON_PAIRS",
    "AnalysisSettings", "BrokerAnalysis", "BrokerAnalysisGroup",
    "ComparableAnalysis", "ComparisonPair", "DeleteOutcome",
    "ExecutedAnalysis", "PairPercent", "PerspectiveResults", "ResultsColumn",
    "SubmittedSettings",
    "delete_executed_analyses", "delete_submission_analyses",
    "execution_batch_is_live",
    "expanded_return_periods", "list_analysis_perspectives",
    "list_broker_analyses", "list_comparable_analyses",
    "list_comparison_pairs", "list_edm_analyses",
    "list_executed_analyses", "list_results_columns",
    "list_submission_executed_analyses",
    "list_submission_rdms", "list_submission_rdm_analyses",
]
