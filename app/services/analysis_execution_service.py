"""Analysis execution — the Execute Suite / Execute Template gate + plan (spec 010).

``request_execution`` is the only entry point: validate the posted selection against
stored state (never Risk Modeler — Article 11), compose the run's plan **once**
(AGENTS.md rule 8 — approved plans are immutable), persist it as the sole
``execute_analysis_batch`` ``rwb_job`` for a fresh ``execution_id``, and dispatch.
The worker (``app/workers/analysis_jobs.py``) reads nothing else at execution time.

``build_full_name``/``name_attempt`` are pure naming helpers (T-04/T-05): the
worker calls them in its per-work-unit loop, where the live-name collision check
against ``irp_analysis`` actually happens (this module never touches that table).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.services import (
    edm_service,
    portfolio_service,
    rwb_job_service,
    template_service,
    treaty_service,
)
from app.services._common import _uid
from app.workers import dispatch
from db import execute

NAME_MAX_LEN = 64


# ── currency picker reference data (modal presentation) ─────────────────────────

def currency_options() -> list[dict]:
    return execute("SELECT code, name FROM irp_currency ORDER BY name",
                   connection="WORKBENCH")


def currency_scheme_options() -> list[dict]:
    return execute("SELECT code, name FROM irp_currency_scheme ORDER BY name",
                   connection="WORKBENCH")


def vintage_options(scheme_code: str) -> list[dict]:
    if not scheme_code:
        return []
    rows = execute(
        "SELECT vintage, effective_date FROM irp_currency_scheme_vintage "
        "WHERE currency_scheme_code = :s ORDER BY effective_date DESC",
        {"s": scheme_code}, connection="WORKBENCH")
    return [{**row, "effective_date": str(row["effective_date"])[:10]} for row in rows]


def currency_defaults() -> dict:
    """The pinned ``DEFAULT_ANALYSIS_CURRENCY_*`` settings (T-19), each cleared to
    empty when it isn't in the synced cache — an unset or cache-absent default
    pre-selects nothing (FR-020)."""
    code = settings.default_analysis_currency_code.strip()
    scheme = settings.default_analysis_currency_scheme.strip()
    vintage = settings.default_analysis_currency_vintage.strip()
    if code and not execute("SELECT 1 FROM irp_currency WHERE code = :c",
                            {"c": code}, connection="WORKBENCH"):
        code = ""
    if scheme and not execute("SELECT 1 FROM irp_currency_scheme WHERE code = :s",
                              {"s": scheme}, connection="WORKBENCH"):
        scheme = ""
    if not scheme or not vintage or not execute(
            "SELECT 1 FROM irp_currency_scheme_vintage "
            "WHERE currency_scheme_code = :s AND vintage = :v",
            {"s": scheme, "v": vintage}, connection="WORKBENCH"):
        vintage = ""
    return {"code": code, "scheme": scheme, "vintage": vintage}


class ExecutionGateError(ValueError):
    """One or more validation failures — the router re-renders the modal at 422."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class SuitePick:
    """One chosen suite from the POST: its still-checked templates and its own
    confirmed currency block (P-15) — empty/incomplete fields fail the gate."""
    suite_id: str
    template_ids: list[str] = field(default_factory=list)
    currency_code: str = ""
    currency_scheme: str = ""
    currency_vintage: str = ""


# ── naming helpers (T-04/T-05) — pure; the worker owns the live-collision loop ──

def build_full_name(portfolio_name: str, template_name: str) -> str:
    return f"{portfolio_name} {template_name}"


def name_attempt(full_name: str, attempt: int) -> tuple[str, str]:
    """The (full_name, submitted_name) pair for collision attempt ``attempt``
    (0 = no suffix). ``submitted_name`` is right-truncated to
    ``NAME_MAX_LEN``, the suffix re-clipping the base so it always fits."""
    if attempt == 0:
        return full_name, full_name[:NAME_MAX_LEN]
    suffix = f" ({attempt})"
    return (full_name + suffix,
            full_name[:NAME_MAX_LEN - len(suffix)] + suffix)


# ── template snapshot reads (gate membership + plan-item values) ───────────────

def _template_rows(template_ids: list[str]) -> dict[str, dict]:
    """Live ``analysis_template`` rows (+ tags) keyed by lowercase id — the gate's
    "posted template is live" check and the plan's per-item value snapshot share
    this one read."""
    ids = list(dict.fromkeys(template_ids))
    if not ids:
        return {}
    params = {f"t{i}": v for i, v in enumerate(ids)}
    marks = ", ".join(f":t{i}" for i in range(len(ids)))
    rows = execute(
        f"""
        SELECT id, name, analysis_profile_name, output_profile_name,
               event_rate_scheme_name, min_loss_threshold, num_max_loss_event,
               franchise_deductible, treat_construction_occupancy_as_unknown
        FROM analysis_template
        WHERE deleted_at IS NULL AND id IN ({marks})
        """, params, connection="WORKBENCH")
    by_id = {_uid(r["id"]): dict(r) for r in rows}
    if by_id:
        tag_rows = execute(
            f"SELECT template_id, tag_name FROM analysis_template_tag "
            f"WHERE template_id IN ({marks}) ORDER BY tag_name",
            params, connection="WORKBENCH")
        tags_by_id: dict[str, list[str]] = {}
        for t in tag_rows:
            tags_by_id.setdefault(_uid(t["template_id"]), []).append(t["tag_name"])
        for tid, row in by_id.items():
            row["tags"] = tags_by_id.get(tid, [])
    return by_id


def _suite_template_ids(suite_id: str) -> set[str] | None:
    """Live template ids belonging to a suite, or ``None`` when the suite itself
    doesn't exist / is deleted."""
    suite = template_service.get_suite(suite_id)
    if suite is None:
        return None
    return {
        item["template_id"] for item in suite["items"]
        if item["template_name"] is not None and item["template_deleted_at"] is None
    }


def _validate_currency(code: str, scheme: str, vintage: str) -> tuple[dict | None, str | None]:
    """A complete, cache-valid currency block, or an error message (FR-019/FR-020).
    The membership of currency *in* scheme is deliberately unvalidated (edge case
    list) — only that each of the three values resolves on its own."""
    code, scheme, vintage = code.strip(), scheme.strip(), vintage.strip()
    if not code or not scheme or not vintage:
        return None, "Choose a currency, currency scheme, and scheme vintage."
    if not execute("SELECT 1 FROM irp_currency WHERE code = :c",
                   {"c": code}, connection="WORKBENCH"):
        return None, f"Currency '{code}' is not in the synced cache."
    if not execute("SELECT 1 FROM irp_currency_scheme WHERE code = :s",
                   {"s": scheme}, connection="WORKBENCH"):
        return None, f"Currency scheme '{scheme}' is not in the synced cache."
    row = execute(
        "SELECT effective_date FROM irp_currency_scheme_vintage "
        "WHERE currency_scheme_code = :s AND vintage = :v",
        {"s": scheme, "v": vintage}, connection="WORKBENCH")
    if not row:
        return None, f"Vintage '{vintage}' is not in the synced cache for scheme '{scheme}'."
    return {"code": code, "scheme": scheme, "vintage": vintage,
            "asOfDate": str(row[0]["effective_date"])[:10]}, None


@dataclass(frozen=True)
class _ValidSuiteItem:
    suite_id: str | None
    suite_name: str | None
    currency: dict
    template_ids: list[str]


def _validate(
    *, edm_id: str, kind: str, portfolio_ids: list[str], treaty_names: list[str],
    suite_picks: list[SuitePick], template_ids: list[str],
    currency_code: str, currency_scheme: str, currency_vintage: str,
) -> tuple[edm_service.EdmRow, list, list[_ValidSuiteItem], dict[str, dict]]:
    """Common gate for both kinds. Returns the validated EDM row, the requested
    portfolios (``portfolio_service.PortfolioRow``), the resolved suite items
    (empty for ``kind=template``), and the template snapshot rows to compose
    from. Raises ``ExecutionGateError`` with every failure found."""
    errors: list[str] = []

    edm = edm_service.get_edm(edm_id)
    if edm is None or edm.status != edm_service.READY:
        raise ExecutionGateError(["This EDM is not ready for execution."])

    all_portfolios = {p.id: p for p in portfolio_service.list_portfolios(edm_id=edm_id)}
    portfolio_ids = [_uid(p) for p in dict.fromkeys(portfolio_ids)]
    if not portfolio_ids:
        errors.append("Select at least one portfolio.")
    unknown_portfolios = [p for p in portfolio_ids if p not in all_portfolios]
    if unknown_portfolios:
        errors.append("One or more selected portfolios no longer belong to this EDM.")
    portfolios = [all_portfolios[p] for p in portfolio_ids if p in all_portfolios]

    valid_treaty_names = {t.name for t in treaty_service.list_treaties(edm_id=edm_id)}
    unknown_treaties = [t for t in treaty_names if t not in valid_treaty_names]
    if unknown_treaties:
        errors.append("One or more selected treaties no longer exist on this EDM.")

    suite_items: list[_ValidSuiteItem] = []
    template_rows: dict[str, dict] = {}

    if kind == "suite":
        any_templates_selected = False
        for pick in suite_picks:
            suite_template_ids = _suite_template_ids(pick.suite_id)
            if suite_template_ids is None:
                errors.append("A selected suite no longer exists.")
                continue
            picked = [_uid(t) for t in dict.fromkeys(pick.template_ids)]
            foreign = [t for t in picked if t not in suite_template_ids]
            if foreign:
                errors.append("A selected template no longer belongs to its suite.")
                picked = [t for t in picked if t in suite_template_ids]
            currency, currency_error = _validate_currency(
                pick.currency_code, pick.currency_scheme, pick.currency_vintage)
            if currency_error:
                errors.append(currency_error)
            if picked:
                any_templates_selected = True
                if currency is not None:
                    suite = template_service.get_suite(pick.suite_id)
                    suite_items.append(_ValidSuiteItem(
                        suite_id=pick.suite_id, suite_name=suite["name"],
                        currency=currency, template_ids=picked))
                    template_rows.update(_template_rows(picked))
        if not suite_picks or not any_templates_selected:
            errors.append("Choose at least one suite with at least one template.")
    elif kind == "template":
        picked = [_uid(t) for t in dict.fromkeys(template_ids)]
        template_rows = _template_rows(picked)
        missing = [t for t in picked if t not in template_rows]
        if missing:
            errors.append("A selected template is no longer live.")
        if not picked:
            errors.append("Choose at least one template.")
        currency, currency_error = _validate_currency(
            currency_code, currency_scheme, currency_vintage)
        if currency_error:
            errors.append(currency_error)
        if picked and currency is not None:
            suite_items.append(_ValidSuiteItem(
                suite_id=None, suite_name=None, currency=currency,
                template_ids=[t for t in picked if t in template_rows]))
    else:
        errors.append(f"Unknown execution kind '{kind}'.")

    if errors:
        raise ExecutionGateError(errors)
    return edm, portfolios, suite_items, template_rows


def _compose_plan(
    *, edm, portfolios, suite_items: list[_ValidSuiteItem],
    template_rows: dict[str, dict], treaty_names: list[str],
    actor_id: Any, submission_id: Any | None, submission_name: str | None,
) -> dict:
    execution_id = str(uuid.uuid4())
    items = []
    item_no = 0
    for suite_item in suite_items:
        for template_id in suite_item.template_ids:
            t = template_rows[template_id]
            tag_names = list(t.get("tags") or [])
            if submission_name:
                tag_names.append(submission_name)
            items.append({
                "item_no": item_no,
                "suite_id": suite_item.suite_id,
                "suite_name": suite_item.suite_name,
                "template_id": template_id,
                "template_name": t["name"],
                "analysis_profile_name": t["analysis_profile_name"],
                "output_profile_name": t["output_profile_name"],
                "event_rate_scheme_name": t["event_rate_scheme_name"],
                "currency": dict(suite_item.currency),
                "min_loss_threshold": float(t["min_loss_threshold"]),
                "num_max_loss_event": int(t["num_max_loss_event"]),
                "franchise_deductible": bool(t["franchise_deductible"]),
                "treat_construction_occupancy_as_unknown": bool(
                    t["treat_construction_occupancy_as_unknown"]),
                "tag_names": tag_names,
            })
            item_no += 1
    return {
        "execution_id": execution_id,
        "edm_id": edm.id,
        "edm_name": edm.name,
        "submission_id": (str(submission_id) if submission_id is not None else None),
        "actor_id": (str(actor_id) if actor_id is not None else None),
        "treaty_names": list(treaty_names),
        "portfolios": [{"id": p.id, "name": p.name} for p in portfolios],
        "items": items,
    }


def request_execution(
    *, edm_id: str, kind: str, portfolio_ids: list[str], treaty_names: list[str],
    suite_picks: list[SuitePick] | None = None, template_ids: list[str] | None = None,
    currency_code: str = "", currency_scheme: str = "", currency_vintage: str = "",
    actor_id: Any, submission_id: Any | None = None, submission_name: str | None = None,
) -> str:
    """Validate the posted selection, compose the plan once, persist it on a fresh
    ``execute_analysis_batch`` ``rwb_job`` and dispatch (FR-012). Raises
    ``ExecutionGateError`` on any validation failure — no partial persistence.
    Returns the new ``execution_id``."""
    edm, portfolios, suite_items, template_rows = _validate(
        edm_id=edm_id, kind=kind, portfolio_ids=portfolio_ids,
        treaty_names=treaty_names, suite_picks=suite_picks or [],
        template_ids=template_ids or [], currency_code=currency_code,
        currency_scheme=currency_scheme, currency_vintage=currency_vintage)
    plan = _compose_plan(
        edm=edm, portfolios=portfolios, suite_items=suite_items,
        template_rows=template_rows, treaty_names=treaty_names,
        actor_id=actor_id, submission_id=submission_id,
        submission_name=submission_name)
    execution_id = plan["execution_id"]
    job_id = rwb_job_service.enqueue_rwb_job(
        requestor_type="analyst_request", requestor_id=execution_id,
        rwb_job_type="execute_analysis_batch", input_data=plan, actor_id=actor_id)
    dispatch.dispatch(rwb_job_id=job_id, rwb_job_type="execute_analysis_batch")
    return execution_id


__all__ = [
    "NAME_MAX_LEN",
    "ExecutionGateError",
    "SuitePick",
    "build_full_name",
    "name_attempt",
    "currency_options",
    "currency_scheme_options",
    "vintage_options",
    "currency_defaults",
    "request_execution",
]
