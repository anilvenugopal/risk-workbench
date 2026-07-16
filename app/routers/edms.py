"""EDM routes — import, detail, recovery, and the non-blocking name check (US1).

Server-rendered FastAPI + Jinja2 + HTMX (Article 8). Every state-changing route
validates a CSRF token (Article 13); no route calls Risk Modeler (Article 11) —
import only *enqueues* the upload work and returns. No row scoping (Article 6):
every analyst may load and act on every EDM.

Route order matters: the literal ``/edms/import`` and ``/edms/name-check`` paths are
declared before ``/edms/{edm_id}`` so the parameter route never shadows them.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.services import edm_service
from app.services.errors import (
    ConcurrencyConflict, InvalidMemberName, InvalidSourceFile)

router = APIRouter()

_NAV_KEY = "irp.edm_library"  # list / import / detail all activate this node (T060)


def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, extra: dict, status_code: int = 200):
    current_user = request.state.user
    nav = get_nav_context(current_user, _NAV_KEY)
    return _templates(request).TemplateResponse(
        request, template,
        {"current_user": current_user, "nav": nav, **extra},
        status_code=status_code,
    )


def _partial(request: Request, template: str, ctx: dict, status_code: int = 200):
    return _templates(request).TemplateResponse(
        request, template, {"current_user": request.state.user, **ctx},
        status_code=status_code,
    )


# ── Library list (literal path — declared before /edms/{edm_id}) ─────────────────

@router.get("/edms", response_class=HTMLResponse)
def library(request: Request):
    """Global EDM library — every EDM across all submissions, any analyst (no row
    scoping, FR-037/SC-009), narrowable by a name search + status filter (US7). GET,
    no CSRF."""
    q = (request.query_params.get("q") or "").strip() or None
    status = (request.query_params.get("status") or "").strip() or None
    return _render(request, "pages/edm_library.html", {
        "rows": edm_service.list_edms(name=q, status=status),
        "filter_values": {"q": request.query_params.get("q", ""),
                          "status": request.query_params.get("status", "")},
        "statuses": edm_service.STATUSES,
    })


# ── Import form + name check (literal paths first) ───────────────────────────────

@router.get("/edms/import", response_class=HTMLResponse)
def import_form(request: Request):
    return _render(request, "pages/edm_import.html",
                   {"form": {"name": ""}, "errors": [], "collision": []})


@router.get("/edms/name-check", response_class=HTMLResponse)
def name_check(request: Request):
    name = request.query_params.get("name", "")
    return _partial(request, "partials/name_collision.html",
                    {"collision": edm_service.check_name_collision(name),
                     "kind": "EDM"})


@router.post("/edms/import")
def create_import(
    request: Request,
    name: str = Form(...),
    source_paths: list[str] = Form(default=[]),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/edms/import", status_code=303)

    source = source_paths[0] if source_paths else ""
    form = {"name": name}
    if not name.strip() or not source:
        return _render(request, "pages/edm_import.html", {
            "form": form,
            "errors": ["A name and a source file selection are required."],
            "collision": []}, status_code=422)
    try:
        result = edm_service.import_edm(
            name=name.strip(), source_file_path=source, actor_id=request.state.user.id)
    except (InvalidSourceFile, InvalidMemberName) as exc:
        return _render(request, "pages/edm_import.html",
                       {"form": form, "errors": [str(exc)], "collision": []},
                       status_code=422)
    return RedirectResponse(f"/edms/{result.entity_id}", status_code=303)


# ── Detail + recovery ────────────────────────────────────────────────────────────

def _detail(request: Request, edm_id: str, status_code: int = 200):
    edm = edm_service.get_edm(edm_id)
    if edm is None:
        return _render(request, "base/error.html",
                       {"status_code": 404, "title": "Not found",
                        "detail": "That EDM does not exist."}, status_code=404)
    return _render(request, "pages/edm_detail.html",
                   {"edm": edm}, status_code=status_code)


@router.get("/edms/{edm_id}", response_class=HTMLResponse)
def detail(request: Request, edm_id: str):
    return _detail(request, edm_id)


@router.post("/edms/{edm_id}/retry")
def retry(request: Request, edm_id: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    edm_service.retry_import(edm_id=edm_id, actor_id=request.state.user.id)
    return _detail(request, edm_id)


@router.post("/edms/{edm_id}/replace-file")
def replace_file(
    request: Request,
    edm_id: str,
    source_paths: list[str] = Form(default=[]),
    updated_at: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/edms/{edm_id}", status_code=303)
    source = source_paths[0] if source_paths else ""
    if not source:
        return _detail(request, edm_id, status_code=422)
    try:
        edm_service.replace_source_file(
            edm_id=edm_id, new_source_file_path=source,
            expected_updated_at=updated_at, actor_id=request.state.user.id)
    except InvalidSourceFile:
        return _detail(request, edm_id, status_code=422)
    except ConcurrencyConflict:
        return _detail(request, edm_id, status_code=409)
    return _detail(request, edm_id)


__all__ = ["router"]
