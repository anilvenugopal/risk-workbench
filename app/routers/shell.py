"""Shell route handlers — all rail destinations and sidebar pages.

Each handler requires an authenticated CurrentUser (enforced by SessionMiddleware).
Nav context is built via get_nav_context() and passed to templates.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.nav import get_nav_context

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


@router.get("/workflows/irp-jobs", response_class=HTMLResponse)
def workflows_irp_jobs(request: Request):
    return _render(request, "pages/workflows_irp_jobs.html", "workflows.irp_jobs")


@router.get("/workflows/rwb-jobs", response_class=HTMLResponse)
def workflows_rwb_jobs(request: Request):
    return _render(request, "pages/workflows_rwb_jobs.html", "workflows.rwb_jobs")


@router.get("/workflows/exceptions", response_class=HTMLResponse)
def workflows_exceptions(request: Request):
    return _render(request, "pages/workflows_exceptions.html", "workflows.exceptions")
