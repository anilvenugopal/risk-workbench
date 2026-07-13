"""Shared-drive browse route — a live, read-only HTMX directory listing.

``GET /browse`` returns the ``shared_drive_browse.html`` fragment: subfolders are
navigation links (``hx-get`` back into this endpoint) and files carry multi-select
checkboxes the import/package modals collect. Read-only — no state change, so no
CSRF (Article 13 applies to mutations only). Authentication is enforced globally by
SessionMiddleware.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.services import shared_drive
from app.services.errors import InvalidSourceFile

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


@router.get("/browse", response_class=HTMLResponse)
def browse(request: Request, path: str | None = None):
    """Render the live listing for ``path`` (defaults to the shared-drive root)."""
    ctx: dict = {"current_user": request.state.user}
    try:
        ctx["listing"] = shared_drive.browse(path)
    except InvalidSourceFile as exc:
        ctx["listing"] = None
        ctx["error"] = str(exc)
    return _templates(request).TemplateResponse(
        request, "partials/shared_drive_browse.html", ctx)


__all__ = ["router"]
