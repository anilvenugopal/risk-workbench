"""Analysis template and template-suite routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.nav import get_nav_context

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, nav_key: str):
    current_user = request.state.user
    return _templates(request).TemplateResponse(
        request,
        template,
        {"current_user": current_user,
         "nav": get_nav_context(current_user, nav_key)},
    )


# Literal template and metadata routes precede the parameterized routes added by
# the administration story.
@router.get("/templates", response_class=HTMLResponse)
def suites_page(request: Request):
    return _render(request, "pages/templates.html", "templates.suites")


__all__ = ["router"]
