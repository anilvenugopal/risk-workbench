"""Shared HTTP handling for EDM and RDM notes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.services import entity_note_service
from app.services.errors import NoteConflict


def save_notes(
    request: Request, *, kind: str, entity_id: str, notes: str,
    original_notes: str, csrf_token: str, action: str, return_url: str,
    get_entity: Callable[[str], Any | None],
):
    if not validate_csrf_token(csrf_token):
        return Response("Invalid CSRF token", status_code=403)
    try:
        entity_note_service.update_notes(
            kind=kind, entity_id=entity_id, notes=notes,
            original_notes=original_notes, actor_id=request.state.user.id)
    except LookupError:
        return HTMLResponse(f"That {kind.upper()} does not exist.", status_code=404)
    except (ValueError, NoteConflict) as exc:
        entity = get_entity(entity_id)
        conflict = isinstance(exc, NoteConflict)
        current = exc.current_note if conflict else original_notes
        return _partial(
            request, entity=entity, action=action, value=notes,
            original=current or "", error=None if conflict else str(exc),
            conflict=conflict, status_code=409 if conflict else 422)
    if request.headers.get("HX-Request") == "true":
        return _partial(request, entity=get_entity(entity_id), action=action)
    return RedirectResponse(return_url, status_code=303)


def _partial(
    request: Request, *, entity: Any, action: str, value: str | None = None,
    original: str | None = None, error: str | None = None,
    conflict: bool = False, status_code: int = 200,
):
    return request.app.state.templates.TemplateResponse(
        request, "partials/entity_note.html", {
            "current_user": request.state.user,
            "entity_notes": entity.notes,
            "note_action": action,
            "note_value": (entity.notes or "") if value is None else value,
            "note_original": (entity.notes or "") if original is None else original,
            "note_error": error,
            "note_conflict": original if conflict else None,
            "note_conflict_active": conflict,
            "note_editing": error is not None or conflict,
        }, status_code=status_code)
