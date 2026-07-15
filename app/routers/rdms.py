"""RDM routes — import (applied to ≥1 EDM), detail, recovery, name check (US2).

Mirrors ``edms.py``. The import body carries ``applied_edm_ids`` — **≥1 required**;
every apply targets an EDM (review-only import is deferred, D3/FR-016). CSRF on every
POST (Article 13); no Risk Modeler call on any route (Article 11); no row scoping
(Article 6). Literal paths precede ``/rdms/{rdm_id}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.services import edm_service, rdm_service
from app.services.errors import (
    ConcurrencyConflict,
    EmptyPackageError,
    InvalidMemberName,
    InvalidSourceFile,
)

router = APIRouter()

_NAV_KEY = "irp.rdm_library"  # list / import / detail all activate this node (T060)


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


# ── Library list (literal path — declared before /rdms/{rdm_id}) ─────────────────

@router.get("/rdms", response_class=HTMLResponse)
def library(request: Request):
    """Global RDM library — every RDM across all submissions, any analyst (no row
    scoping, FR-037/SC-009), narrowable by a name search + status filter (US7). GET,
    no CSRF."""
    q = (request.query_params.get("q") or "").strip() or None
    status = (request.query_params.get("status") or "").strip() or None
    return _render(request, "pages/rdm_library.html", {
        "rows": rdm_service.list_rdms(name=q, status=status),
        "filter_values": {"q": request.query_params.get("q", ""),
                          "status": request.query_params.get("status", "")},
        "statuses": rdm_service.STATUSES,
    })


# ── Import form + name check ─────────────────────────────────────────────────────

@router.get("/rdms/import", response_class=HTMLResponse)
def import_form(request: Request):
    return _render(request, "pages/rdm_import.html",
                   {"form": {"name": ""}, "errors": [], "collision": [],
                    "edms": edm_service.list_edms()})


@router.get("/rdms/name-check", response_class=HTMLResponse)
def name_check(request: Request):
    name = request.query_params.get("name", "")
    return _partial(request, "partials/name_collision.html",
                    {"collision": rdm_service.check_name_collision(name),
                     "kind": "RDM"})


@router.post("/rdms/import")
def create_import(
    request: Request,
    name: str = Form(...),
    source_paths: list[str] = Form(default=[]),
    applied_edm_ids: list[str] = Form(default=[]),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/rdms/import", status_code=303)

    source = source_paths[0] if source_paths else ""
    edm_ids = [e for e in applied_edm_ids if e]
    form = {"name": name}
    if not name.strip() or not source:
        return _render(request, "pages/rdm_import.html", {
            "form": form, "edms": edm_service.list_edms(),
            "errors": ["A name and a source file selection are required."],
            "collision": []}, status_code=422)
    if not edm_ids:
        return _render(request, "pages/rdm_import.html", {
            "form": form, "edms": edm_service.list_edms(),
            "errors": ["Select at least one EDM to apply the RDM to."],
            "collision": []}, status_code=422)
    try:
        result = rdm_service.import_rdm(
            name=name.strip(), source_file_path=source,
            applied_edm_ids=edm_ids, actor_id=request.state.user.id)
    except (InvalidSourceFile, EmptyPackageError, InvalidMemberName) as exc:
        return _render(request, "pages/rdm_import.html",
                       {"form": form, "edms": edm_service.list_edms(),
                        "errors": [str(exc)], "collision": []}, status_code=422)
    return RedirectResponse(f"/rdms/{result.entity_id}", status_code=303)


# ── Detail + recovery ────────────────────────────────────────────────────────────

def _detail(request: Request, rdm_id: str, status_code: int = 200):
    rdm = rdm_service.get_rdm(rdm_id)
    if rdm is None:
        return _render(request, "base/error.html",
                       {"status_code": 404, "title": "Not found",
                        "detail": "That RDM does not exist."}, status_code=404)
    return _render(request, "pages/rdm_detail.html", {"rdm": rdm},
                   status_code=status_code)


@router.get("/rdms/{rdm_id}", response_class=HTMLResponse)
def detail(request: Request, rdm_id: str):
    return _detail(request, rdm_id)


@router.post("/rdms/{rdm_id}/retry")
def retry(request: Request, rdm_id: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/rdms/{rdm_id}", status_code=303)
    rdm_service.retry_import(rdm_id=rdm_id, actor_id=request.state.user.id)
    return _detail(request, rdm_id)


@router.post("/rdms/{rdm_id}/replace-file")
def replace_file(
    request: Request,
    rdm_id: str,
    source_paths: list[str] = Form(default=[]),
    updated_at: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/rdms/{rdm_id}", status_code=303)
    source = source_paths[0] if source_paths else ""
    if not source:
        return _detail(request, rdm_id, status_code=422)
    try:
        rdm_service.replace_source_file(
            rdm_id=rdm_id, new_source_file_path=source,
            expected_updated_at=updated_at, actor_id=request.state.user.id)
    except InvalidSourceFile:
        return _detail(request, rdm_id, status_code=422)
    except ConcurrencyConflict:
        return _detail(request, rdm_id, status_code=409)
    return _detail(request, rdm_id)


__all__ = ["router"]
