"""The cedant list at /cedants (issue 129). Every logged-in role maintains it
(P-09). No delete: submissions reference a cedant by id, so a cedant leaves the
Create picker by deactivation (P-03)."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.services import cedant_service
from app.services.cedant_service import CedantValidationError

router = APIRouter(prefix="/cedants")


def _cedants_page(request: Request, *, status_code: int = 200, **errors):
    from app.nav import get_nav_context
    current_user = request.state.user
    return request.app.state.templates.TemplateResponse(request, "pages/cedants.html", {
        "current_user": current_user,
        "nav": get_nav_context(current_user, "submissions.cedants"),
        "cedants": cedant_service.list_cedants(include_inactive=True),
        **errors,
    }, status_code=status_code)


@router.get("", response_class=HTMLResponse)
def cedant_list(request: Request):
    return _cedants_page(request)


@router.post("")
def add_cedant(request: Request, name: str = Form(""), csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/cedants", status_code=303)
    try:
        cedant_service.add_cedant(name, actor_id=request.state.user.id)
    except CedantValidationError as exc:
        return _cedants_page(request, status_code=422, add_error=str(exc), add_value=name)
    return RedirectResponse("/cedants", status_code=303)


@router.post("/{cedant_id}/rename")
def rename_cedant(request: Request, cedant_id: str, name: str = Form(""),
                  csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/cedants", status_code=303)
    try:
        cedant_service.rename_cedant(cedant_id, name, actor_id=request.state.user.id)
    except CedantValidationError as exc:
        return _cedants_page(request, status_code=422, rename_error=str(exc),
                             rename_id=cedant_id.lower(), rename_value=name)
    return RedirectResponse("/cedants", status_code=303)


@router.post("/{cedant_id}/deactivate")
def deactivate_cedant(request: Request, cedant_id: str, csrf_token: str = Form(...)):
    return _set_cedant_active(request, cedant_id, False, csrf_token)


@router.post("/{cedant_id}/reactivate")
def reactivate_cedant(request: Request, cedant_id: str, csrf_token: str = Form(...)):
    return _set_cedant_active(request, cedant_id, True, csrf_token)


def _set_cedant_active(request: Request, cedant_id: str, active: bool, csrf_token: str):
    if validate_csrf_token(csrf_token):
        cedant_service.set_cedant_active(cedant_id, active, actor_id=request.state.user.id)
    return RedirectResponse("/cedants", status_code=303)
