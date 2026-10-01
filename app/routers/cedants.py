"""The cedant list at /cedants (issue 129). Every logged-in role maintains it
(P-09). A cedant a submission uses cannot be deleted."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.services import cedant_service
from app.services.cedant_service import CedantValidationError

router = APIRouter(prefix="/cedants")


def _cedants_page(request: Request, *, q: str = "", status_code: int = 200, **errors):
    from app.nav import get_nav_context
    current_user = request.state.user
    return request.app.state.templates.TemplateResponse(request, "pages/cedants.html", {
        "current_user": current_user,
        "nav": get_nav_context(current_user, "submissions.cedants"),
        "cedants": cedant_service.list_cedants(name=q),
        "q": q,
        **errors,
    }, status_code=status_code)


@router.get("", response_class=HTMLResponse)
def cedant_list(request: Request, q: str = ""):
    return _cedants_page(request, q=q)


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


@router.post("/delete")
def delete_cedants(request: Request, csrf_token: str = Form(...),
                   cedant_ids: Annotated[list[str], Form()] = []):
    return _delete(request, cedant_ids, csrf_token)


@router.post("/{cedant_id}/delete")
def delete_cedant(request: Request, cedant_id: str, csrf_token: str = Form(...)):
    return _delete(request, [cedant_id], csrf_token)


def _delete(request: Request, cedant_ids: list[str], csrf_token: str):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/cedants", status_code=303)
    kept = cedant_service.delete_cedants(cedant_ids)
    if kept:
        return _cedants_page(request, status_code=409, delete_error=(
            "Not deleted, because submissions use them: " + ", ".join(kept) + "."))
    return RedirectResponse("/cedants", status_code=303)
