"""Shell route handlers — all rail destinations and sidebar pages.

Each handler requires an authenticated CurrentUser (enforced by SessionMiddleware).
Nav context is built via get_nav_context() and passed to templates.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.nav import get_nav_context
from app.services import analysis_service, edm_service, irp_job_service, submission_service

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, nav_key: str, extra: dict | None = None):
    current_user = request.state.user
    nav = get_nav_context(current_user, nav_key)
    ctx = {
        "current_user": current_user,
        "nav": nav,
        **(extra or {}),
    }
    return _templates(request).TemplateResponse(request, template, ctx)


# ── Rail destinations ─────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _render(request, "pages/home.html", "home")


@router.get("/workflows", response_class=HTMLResponse)
def workflows(request: Request):
    return _render(request, "pages/workflows.html", "workflows.active")


@router.get("/results", response_class=HTMLResponse)
def results(request: Request):
    return _render(request, "pages/results.html", "results")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


EP_TYPES = ("OEP", "AEP")


def _results_analyses_url(order: list[str], submission: str, edm: str,
                          perspective: str = "", ep_type: str = "") -> str:
    params = [("ids", ",".join(order))]
    if submission:
        params.append(("submission", submission))
    if edm:
        params.append(("edm", edm))
    if perspective:
        params.append(("perspective", perspective))
    if ep_type:
        params.append(("ep_type", ep_type))
    return "/results/analyses?" + urlencode(params)


@router.get("/results/analyses", response_class=HTMLResponse)
def results_analyses(request: Request, ids: str = "", submission: str = "",
                     edm: str = "", perspective: str = "", ep_type: str = ""):
    """The dedicated results page (spec 011 US4, contracts/routes.md §3):
    one column per ``ids`` entry in param order (FR-016), all 11 return
    periods of the selected ``ep_type``, ``perspective`` screen-wide
    (FR-011/FR-012). Reads stored extracts only — no Risk Modeler call
    (Article 11). Unknown or deleted ids render a notice, never a 500."""
    id_list = [p for p in (s.strip() for s in ids.split(",")) if p]
    perspectives = analysis_service.list_analysis_perspectives()
    codes = [p["code"] for p in perspectives]
    active = (perspective if perspective in codes
              else analysis_service.DEFAULT_PERSPECTIVE)
    active_ep = ep_type if ep_type in EP_TYPES else EP_TYPES[0]
    active_label = next(
        (p["label"] for p in perspectives if p["code"] == active), active)
    columns, missing = analysis_service.list_results_columns(
        analysis_ids=id_list)

    # Entity crumbs + tab title (FR-014, T-07): edm= present → submission crumb
    # then EDM crumb; else submission crumb only. Both link back.
    extra_crumbs: list[dict] = []
    page_name = None
    sub = (submission_service.get_submission(submission)
           if submission and _is_uuid(submission) else None)
    if sub is not None:
        extra_crumbs.append({"label": sub.name,
                             "route": f"/submissions/{sub.id}"})
        page_name = sub.name
    edm_row = edm_service.get_edm(edm) if edm and _is_uuid(edm) else None
    if edm_row is not None:
        extra_crumbs.append({"label": edm_row.name,
                             "route": f"/edms/{edm_row.id}"})
        page_name = edm_row.name

    # Reorder controls rewrite the ids param and re-request (FR-016): one
    # swap-with-neighbour URL per side, built over the resolved column order.
    order = [c.id for c in columns]
    view_columns = []
    for i, col in enumerate(columns):
        left = right = None
        if i > 0:
            swapped = list(order)
            swapped[i - 1], swapped[i] = swapped[i], swapped[i - 1]
            left = _results_analyses_url(swapped, submission, edm,
                                         active, active_ep)
        if i < len(columns) - 1:
            swapped = list(order)
            swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
            right = _results_analyses_url(swapped, submission, edm,
                                          active, active_ep)
        view_columns.append({"col": col, "left": left, "right": right})

    return _render(request, "pages/results_analyses.html", "results.analyses", {
        "view_columns": view_columns,
        "missing": missing,
        "perspectives": perspectives,
        "active_perspective": active,
        "active_label": active_label,
        "ep_types": EP_TYPES,
        "active_ep": active_ep,
        "rp_labels": analysis_service.expanded_return_periods(),
        # each toolbar select adds its own value to this GET and hx-includes
        # the other's, so neither swap drops the other's choice
        "results_base_url": _results_analyses_url(order, submission, edm),
        "extra_crumbs": extra_crumbs,
        "page_name": page_name,
    })


@router.get("/account", response_class=HTMLResponse)
def account(request: Request):
    return _render(request, "pages/account.html", "account")


# ── Workflows sidebar ─────────────────────────────────────────────────────────

@router.get("/workflows/active", response_class=HTMLResponse)
def workflows_active(request: Request):
    return _render(request, "pages/workflows_active.html", "workflows.active")


@router.get("/workflows/review", response_class=HTMLResponse)
def workflows_review(request: Request):
    return _render(request, "pages/workflows_review.html", "workflows.review")


def _irp_jobs_context() -> dict:
    """Shared context for the job monitor page and its polled table fragment."""
    rows = irp_job_service.list_recent()
    return {
        "rows": rows,
        # Any row still moving at IRP → keep polling; all-terminal → the fragment
        # stops emitting the trigger and the poll ends on its own.
        "live": any(r["status"] not in irp_job_service.TERMINAL for r in rows),
    }


@router.get("/workflows/irp-jobs", response_class=HTMLResponse)
def workflows_irp_jobs(request: Request):
    return _render(request, "pages/workflows_irp_jobs.html", "workflows.irp_jobs",
                   _irp_jobs_context())


@router.get("/workflows/irp-jobs/table", response_class=HTMLResponse)
def workflows_irp_jobs_table(request: Request):
    """Read-only table render for HTMX polling — no shell, no nav (Article 11:
    display only, this issues no Risk Modeler call)."""
    return _templates(request).TemplateResponse(
        request, "partials/irp_jobs_table.html",
        {"current_user": request.state.user, **_irp_jobs_context()},
    )


@router.get("/workflows/rwb-jobs", response_class=HTMLResponse)
def workflows_rwb_jobs(request: Request):
    return _render(request, "pages/workflows_rwb_jobs.html", "workflows.rwb_jobs")


@router.get("/workflows/exceptions", response_class=HTMLResponse)
def workflows_exceptions(request: Request):
    return _render(request, "pages/workflows_exceptions.html", "workflows.exceptions")
