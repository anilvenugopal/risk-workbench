"""Shared HTTP handling for EDM and RDM notes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.services import entity_note_service
from app.services.errors import NoteConflict


@dataclass(frozen=True)
class NoteOutcome:
    """``update_notes`` mapped to HTTP: 200 saved, 422 rejected, 409 conflict."""
    status_code: int
    saved: str | None = None      # the note now stored (200 only)
    error: str | None = None      # validation message (422 only)
    conflict: str | None = None   # the newer stored note (409 only)


def check_csrf(csrf_token: str) -> HTMLResponse | None:
    if not validate_csrf_token(csrf_token):
        return HTMLResponse("Invalid CSRF token", status_code=403)
    return None


def apply_notes(
    request: Request, *, kind: str, entity_id: str, notes: str,
    original_notes: str,
) -> NoteOutcome | HTMLResponse:
    """Run ``update_notes``; a missing entity comes back as the 404 response."""
    try:
        saved = entity_note_service.update_notes(
            kind=kind, entity_id=entity_id, notes=notes,
            original_notes=original_notes, actor_id=request.state.user.id)
    except LookupError:
        return HTMLResponse(f"That {kind.upper()} does not exist.", status_code=404)
    except ValueError as exc:
        return NoteOutcome(status_code=422, error=str(exc))
    except NoteConflict as exc:
        return NoteOutcome(status_code=409, conflict=exc.current_note)
    return NoteOutcome(status_code=200, saved=saved)


def note_context(
    outcome: NoteOutcome, *, entity_notes: str | None, notes: str,
    original_notes: str,
) -> dict[str, Any]:
    """The ``note_*`` keys ``entity_note.html`` and
    ``submission_entity_note_cell.html`` share. A conflict re-bases the hidden
    original on the newer stored note so the next Save replaces it."""
    saved = outcome.status_code == 200
    if outcome.status_code == 409:
        original_notes = outcome.conflict or ""
    return {
        "note_value": (entity_notes or "") if saved else notes,
        "note_original": (entity_notes or "") if saved else original_notes,
        "note_error": outcome.error,
        "note_conflict": outcome.conflict,
        "note_conflict_active": outcome.status_code == 409,
        "note_editing": not saved,
    }


def save_notes(
    request: Request, *, kind: str, entity_id: str, notes: str,
    original_notes: str, csrf_token: str, action: str, return_url: str,
    get_entity: Callable[[str], Any | None],
):
    denied = check_csrf(csrf_token)
    if denied is not None:
        return denied
    outcome = apply_notes(
        request, kind=kind, entity_id=entity_id, notes=notes,
        original_notes=original_notes)
    if isinstance(outcome, Response):
        return outcome
    if outcome.status_code == 200 and request.headers.get("HX-Request") != "true":
        return RedirectResponse(return_url, status_code=303)
    entity = get_entity(entity_id)
    return request.app.state.templates.TemplateResponse(
        request, "partials/entity_note.html", {
            "current_user": request.state.user,
            "entity_notes": entity.notes,
            "note_action": action,
            **note_context(outcome, entity_notes=entity.notes, notes=notes,
                           original_notes=original_notes),
        }, status_code=outcome.status_code)
