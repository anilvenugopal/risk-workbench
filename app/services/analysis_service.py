"""Analysis service — the broker-analysis read models (spec 004 US3).

Surfaces the ``irp_analysis`` rows captured by ``backfill_rdm_analyses``:

  • ``list_broker_analyses(rdm_id)`` — the RDM page (FR-030/FR-031, R8):
    grouped by ``rdm_id``; each with its parsed
    ``settings_metadata`` (missing/partial → blank, never error) and
    ``is_group`` (FR-035).
  • ``list_edm_analyses(edm_id)`` — the context-free EDM page, which has no
    submission RDM context and therefore returns no groups.
  • ``list_executed_analyses(edm_id)`` — the EDM detail page's user-executed
    section (spec 010 US2, FR-013): every analysis the workbench itself
    submitted against this EDM (``execution_id`` set), with live status
    derived from the latest tracked ``irp_job`` per analysis (T-07) and the
    same curated ``AnalysisSettings`` view once ``settings_metadata`` is
    backfilled. No RDM grouping — this is exactly the portfolio the trust
    rule (8/4 D8) exempts, since the workbench submitted these itself.

**No analysis is attributed to a portfolio** (8/4 D8): there is no trustworthy
way to tie an RDM analysis to an EDM portfolio, and every analysis here is
broker-provided (``rdm_id`` NOT NULL). ``irp_analysis.exposure_resource_id`` is
still captured by the worker — it is defensible only for analyses CIC runs
itself — but nothing reads or displays it.

The curated ``AnalysisSettings`` view model reads the documented RM payload
fields defensively (``analysisType``/``engineType``/``engineVersion``/
``peril``/``subperil``/``region``/``currencyCode``/… — IRP knowledge base
2026-07-24); term / PLA / event-rate fields have NO documented source and stay
blank until the sandbox confirms their spelling (IRP_INTEGRATION_FOLLOWUPS.md).

Broker rows carry the stored spec-011 results extract (``results_state`` /
``results``) once ``retrieve_analysis_results`` lands it — read from
``loss_results``, never from Risk Modeler. No row scoping (Article 6). The one write is
``delete_executed_analyses`` (spec 010 P-19): a synchronous request-path delete
of terminal own-executed analyses — Risk Modeler first, then a local soft
delete — everything else here is read-only.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.services import irp_gateway
from app.services._common import _parse_json_dict, _rm_ui_root, _uid, _utcnow
from db import execute, execute_command, get_connection

logger = logging.getLogger(__name__)


@dataclass
class AnalysisSettings:
    """The curated FR-031 settings view — every field blank-on-missing."""
    analysis_type: str | None = None
    analysis_mode: str | None = None
    framework: str | None = None    # analysisFramework (ELT/PLT) — spec 011 FR-022
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
    """Display formatting for a stored loss number (approved preview): values
    ≥ 1M read ``4.1M``, smaller values read as thousands-separated integers,
    missing values read ``—``. Never a recomputation — the verbatim number rides
    beside it in ``title``/``data-value`` (spec non-negotiable 5)."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:,.1f}M"
    return f"{value:,.0f}"


@dataclass
class PerspectiveResults:
    """One perspective of the stored extract, display-ready (FR-011/FR-012).
    ``produced`` False = explicitly empty (FR-004) — displayed as absent, never
    as an error."""
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
    """The expanded row's Analysis settings group (O-11) from the submit-time
    snapshot ``irp_analysis.submitted_settings`` (T-09) — every field
    blank-on-missing. Broker rows have no snapshot at all, which the row renders
    as *not returned* (FR-022)."""
    currency: str | None = None                # "USD · RMS · RL25"
    min_loss_threshold: str | None = None
    franchise_deductible: str | None = None
    construction_occupancy: str | None = None


def _submitted_view(raw: Any) -> SubmittedSettings:
    p = _parse_json_dict(raw, "submitted_settings")
    if not p:
        return SubmittedSettings()
    currency = p.get("currency") or {}
    triple = " · ".join(v for v in (currency.get("code"), currency.get("scheme"),
                                    currency.get("vintage")) if v)
    unknown = p.get("treat_construction_occupancy_as_unknown")
    return SubmittedSettings(
        currency=triple or None,
        min_loss_threshold=_text(p.get("min_loss_threshold")),
        franchise_deductible=_text(p.get("franchise_deductible")),
        construction_occupancy=("Treat as unknown" if unknown
                                else _text(unknown)),
    )


# The perspective every results view opens on (FR-012, design note 20 D9).
# The label repeats the analysis_perspective_kind seed so the merged analyses
# grid can name the perspective its AAL column holds without a query per render.
DEFAULT_PERSPECTIVE = "RL"
DEFAULT_PERSPECTIVE_LABEL = "Pre-Cat Net"


def list_analysis_perspectives() -> list[dict]:
    """The five perspective codes/labels in dropdown order (T-06, Article 3).
    Order is not the default — ``DEFAULT_PERSPECTIVE`` names that (FR-012)."""
    return [dict(r) for r in execute(
        "SELECT code, label FROM analysis_perspective_kind ORDER BY sort_order",
        {}, connection="WORKBENCH")]


def _perspective_results(loss_results_raw: Any, perspectives: list[dict],
                         return_periods: tuple | None = None,
                         ) -> list[PerspectiveResults]:
    """The stored extract as display-ready perspectives, filtered to
    ``return_periods`` — the condensed 50/100/250/500/1000/10000 subset by
    default (FR-005); the dedicated page passes the full stored set. Empty
    when not fetched yet."""
    doc = _parse_json_dict(loss_results_raw, "loss_results")
    if not doc:
        return []
    if return_periods is None:
        # lazy: the worker module owns the return-period sets (data-model.md §4)
        from app.workers.analysis_jobs import CONDENSED_RETURN_PERIODS  # noqa: PLC0415
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
    edm_name: str | None         # representative handle's EDM
    edm_names: list[str] = field(default_factory=list)  # every EDM it spans
    is_group: bool = False
    settings: dict | None = None            # parsed raw snapshot (R2)
    display: AnalysisSettings = field(default_factory=AnalysisSettings)
    rm_url: str | None = None    # Risk Modeler link-out from the snapshot's
                                 # appAnalysisId, as own rows build theirs (FR-025)
    created_at: Any = None       # RM createDate — the broker's own run date (FR-024)
    # ── spec 011 results (FR-008/SC-005) ──────────────────────────────────────
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

    @property
    def edm_count(self) -> int:
        return len({n for a in self.analyses for n in (a.edm_names or []) if n})


@dataclass
class ExecutedAnalysis:
    """One workbench-submitted analysis for the EDM detail page's user-executed
    section (spec 010 US2, FR-013). Status is derived from the latest tracked
    ``irp_job`` (T-07) rather than stored as its own label — ``irp_analysis.
    status_code`` keeps only the three coarse lifecycle codes."""
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
    # ── spec 011 results (FR-008/SC-005) ──────────────────────────────────────
    results_state: str = "pending"      # pending | failed | ready
    results_error: str | None = None    # failed retrieval's error_detail
    results: list[PerspectiveResults] = field(default_factory=list)  # [] until ready
    submitted: SubmittedSettings = field(default_factory=SubmittedSettings)

    @property
    def is_live(self) -> bool:
        """Drives the EDM page's 3s self-poll (T-11): still moving toward a
        terminal outcome. ``pending`` is the only in-flight run status — every
        write that leaves it is terminal. A ready run whose retrieval is still
        pending keeps polling so the loss numbers land with zero analyst actions
        (SC-001); a failed retrieval is terminal (O-06)."""
        return (self.status_code == "pending"
                or (self.status_code == "ready"
                    and self.results_state == "pending"))

    @property
    def status_label(self) -> str:
        if self.job_status is None:
            return "Submitting…"
        if self.job_status == "SUBMISSION FAILED":
            return (f"Failed to submit · attempt {self.submission_attempt_count}/"
                    f"{settings.irp_submission_max_retries}")
        return self.job_status.capitalize()

    @property
    def status_chip(self) -> str:
        """One of the existing import-status chip variants (submissions.css) —
        no new CSS, keyed off the derived label rather than a stored status."""
        if self.job_status in (None, "QUEUED", "RUNNING", "SUBMISSION RETRYING"):
            return "importing"
        if self.job_status == "FINISHED":
            return "ready"
        if self.job_status == "SUBMISSION FAILED":
            return "submission-failed"
        return "error"  # FAILED, CANCELLED

    @property
    def group_key(self) -> str:
        """The Analyses grid's group: ``failed`` / ``in_progress`` / ``ready``.
        Derived, not raw ``status_code`` — a failed-to-submit row is
        ``status_code='pending'`` but belongs under Failed."""
        if self.status_code == "error" or self.status_chip in (
                "error", "submission-failed"):
            return "failed"
        if self.status_code == "ready":
            return "ready"
        return "in_progress"

    @property
    def is_deletable(self) -> bool:
        """Terminal rows only. Deliberately NOT ``status_chip == 'ready'``: the
        chip turns ready at job FINISHED, seconds before the backfill writes
        ``irp_id`` — deleting in that window would orphan the RM analysis."""
        return (self.job_status != "SUBMISSION RETRYING"
                and (self.status_code in ("ready", "error")
                     or self.status_chip == "submission-failed"))


def _parse_settings(raw: Any) -> dict | None:
    return _parse_json_dict(raw, "settings_metadata")


def _first(payload: dict, *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return None


def _text(value: Any) -> str | None:
    """A display string from a defensive read: dicts collapse to their code/
    name (the live payload's ``currency`` object is keyed ``currencyCode``/
    ``currencyName`` — confirmed 2026-07-24); lists join, empty → blank
    (``eventRateSchemeNames``); bools to On/Off."""
    if value is None:
        return None
    if isinstance(value, dict):
        return (value.get("code") or value.get("name")
                or value.get("currencyCode") or value.get("currencyName")
                or None)
    if isinstance(value, (list, tuple)):
        parts = [t for t in (_text(v) for v in value) if t]
        return ", ".join(parts) or None
    if isinstance(value, bool):
        return "On" if value else "Off"
    return str(value)


def _to_display(settings: dict | None) -> AnalysisSettings:
    """The curated FR-031 view from the raw RM payload — documented camelCase
    fields first, plausible fallbacks second, blank when absent (US3 acc. 3)."""
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
        # eventRateSchemeNames (a LIST) is the live spelling (2026-07-24);
        # the scalar guesses stay first so a truthy scalar wins if both appear.
        event_rate_scheme=_text(_first(p, "eventRateScheme", "rateScheme",
                                       "eventRateSchemeNames")),
        rate_vintage=_text(_first(p, "rateVintage", "eventRateSchemeVersion")),
    )


# One row per (RDM×EDM) handle.
_HANDLE_SELECT = """
    SELECT a.id, a.rdm_id, a.irp_id, a.name, a.is_group, a.settings_metadata,
           a.loss_results, e.name AS edm_name,
           r.name AS rdm_name, r.irp_id AS rdm_irp_id
    FROM irp_analysis a
    LEFT JOIN irp_edm e ON e.id = a.edm_id
    LEFT JOIN irp_rdm r ON r.id = a.rdm_id
    WHERE a.deleted_at IS NULL AND a.rdm_id IS NOT NULL
"""


def _mark_failed_retrievals(analyses: list) -> None:
    """Flip still-pending rows whose retrieval ``rwb_job`` ended ``failed`` to
    failed + reason (SC-005) while the run status stays untouched. A terminal
    failed row is never resurrected by the dedup key, so the join is exact,
    not latest-of-many."""
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
    display row (R8): the representative handle is the first seen;
    ``edm_names`` collects every EDM spanned; settings and results come from
    any handle that has them (both snapshots are per-analysis)."""
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
                edm_name=r["edm_name"],
                edm_names=[r["edm_name"]] if r["edm_name"] else [],
                is_group=bool(r["is_group"]), settings=settings,
                display=_to_display(settings),
                rm_url=_rm_analysis_url((settings or {}).get("appAnalysisId")),
                created_at=(settings or {}).get("createDate"),
                results_state=("ready" if results else "pending"),
                results=results)
            by_key[key] = entry
            out.append(entry)
            continue
        if r["edm_name"] and r["edm_name"] not in existing.edm_names:
            existing.edm_names.append(r["edm_name"])
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
    """The RDM page's read (FR-030/FR-031/R8): this RDM's broker analyses,
    deduped across their M EDM handles (shown once), each with parsed settings
    and ``is_group``. No scoping (Article 6)."""
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
            results_state=("ready" if results else "pending"), results=results,
            submitted=_submitted_view(r["submitted_settings"])))
    _mark_failed_retrievals(analyses)
    return analyses


def list_executed_analyses(*, edm_id: Any) -> list[ExecutedAnalysis]:
    """The EDM detail page's user-executed section (FR-013): every analysis the
    workbench submitted against this EDM, newest first, each with its live
    status derived from its latest tracked ``irp_job`` (T-07)."""
    rows = execute(_EXECUTED_SELECT, {"edm_id": str(edm_id)}, connection="WORKBENCH")
    return _executed_models([dict(r) for r in rows])


_SUBMISSION_EXECUTED_SELECT = f"""
    SELECT a.id, a.name, a.full_name, a.status_code, a.failure_reason,
           a.settings_metadata, a.inserted_at, a.irp_id, a.irp_app_analysis_id,
           a.loss_results, a.submitted_settings,
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
    ORDER BY a.inserted_at DESC
"""


def list_submission_executed_analyses(
    *, submission_id: Any,
) -> list[ExecutedAnalysis]:
    """The submission Results section's own rows (spec 011 FR-009): every own
    analysis across every EDM of the submission, newest first, each with its
    EDM name for the section's EDM column. Origin is derived — own is
    ``rdm_id IS NULL``; broker rows come from ``list_submission_rdms``."""
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


def delete_executed_analyses(*, edm_id: Any, analysis_ids: list[Any],
                             actor_id: Any) -> DeleteOutcome:
    """Delete terminal own-executed analyses (spec 010 P-19): validate the
    whole batch up front (every posted id must resolve on this EDM and be
    ``is_deletable``, else ``ValueError``), then per row cascade to Risk
    Modeler first and soft-delete locally on success. A row whose RM delete
    fails is recorded in ``failed`` and kept visible for retry; a row the poller
    claimed for a submission retry mid-batch is recorded in ``retrying`` and left
    alone. Neither aborts the batch — the rows already deleted stay deleted, and
    the caller reports all three counts. RM-first order: a crash between the two
    calls leaves a visible row with a dangling ``irp_id`` — recoverable by
    retrying — rather than a hidden RM analysis."""
    ids = [i for i in dict.fromkeys(_uid(a) for a in analysis_ids) if i]
    if not ids:
        raise ValueError("No analyses selected.")
    rows = {a.id: a for a in list_executed_analyses(edm_id=edm_id)}
    picked = []
    for analysis_id in ids:
        row = rows.get(analysis_id)
        if row is None:
            raise ValueError(
                "A selected analysis no longer belongs to this EDM.")
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


@dataclass
class ResultsColumn:
    """One analysis column on the dedicated results page (spec 011 US4,
    FR-015): the expanded extract (all 11 return periods), the display name,
    currency, and the same results state the merged table derives. Neither
    origin carries a portfolio here (FR-020)."""
    id: str
    name: str | None
    currency: str | None
    results_state: str = "pending"      # pending | failed | ready
    results_error: str | None = None
    results: list[PerspectiveResults] = field(default_factory=list)

    def for_code(self, code: str) -> PerspectiveResults | None:
        return next((p for p in self.results if p.code == code), None)


def expanded_return_periods() -> list[str]:
    """The dedicated page's row labels — the 11 stored return periods, largest
    first (FR-005/FR-015), matching the row order ``_perspective_results``
    builds."""
    from app.workers.analysis_jobs import STORED_RETURN_PERIODS  # noqa: PLC0415
    return [f"{rp:,}" for rp in sorted(STORED_RETURN_PERIODS, reverse=True)]


def list_results_columns(*, analysis_ids: list[Any],
                         ) -> tuple[list[ResultsColumn], int]:
    """The dedicated page's columns (contracts/routes.md §3): one per resolved
    id, in the caller's order, both origins in one read. Returns the columns
    and the count of ids that did not resolve — an unknown or deleted id is a
    notice on the page, never an error."""
    from app.workers.analysis_jobs import STORED_RETURN_PERIODS  # noqa: PLC0415
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
            f"SELECT a.id, a.name, a.full_name, "
            f"a.settings_metadata, a.loss_results "
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
        columns.append(ResultsColumn(
            id=_uid(row["id"]),
            name=row["full_name"] or row["name"],
            currency=_to_display(_parse_settings(row["settings_metadata"])).currency,
            results_state=("ready" if results else "pending"),
            results=results))
    _mark_failed_retrievals(columns)
    return columns, missing


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
    "DEFAULT_PERSPECTIVE", "DEFAULT_PERSPECTIVE_LABEL",
    "AnalysisSettings", "BrokerAnalysis", "BrokerAnalysisGroup", "DeleteOutcome",
    "ExecutedAnalysis", "PerspectiveResults", "ResultsColumn",
    "SubmittedSettings",
    "delete_executed_analyses", "execution_batch_is_live",
    "expanded_return_periods", "list_analysis_perspectives",
    "list_broker_analyses", "list_edm_analyses",
    "list_executed_analyses", "list_results_columns",
    "list_submission_executed_analyses",
    "list_submission_rdms", "list_submission_rdm_analyses",
]
