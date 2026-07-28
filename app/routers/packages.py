"""Package routes — assemble, save, sync, per-member retry (US3).

The package modal lives on the submission detail. Save persists the package + members
and attaches it to the submission (submits nothing); Save-and-Sync enqueues the member
work and returns the card in a queued state. The only Risk Modeler touch on any
handler is the cached name-collision *read* (a permitted request-path read, Article
11 / issue #17) — a colliding member name rejects with 422 before anything is saved
or enqueued, and submits stay worker-side (FR-042). CSRF on every POST (Article 13).
Create/sync/retry are gated by the submission's read-only state (FR-025 / SC-011): a
package attached only to COMPLETED/CANCELLED submissions rejects with 409. Delete is
added in US4 (T041).
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.services import name_check, package_service
from app.services import package_sync_service as sync
from app.services.errors import (
    ConcurrencyConflict, EmptyPackageError, InvalidMemberName,
    InvalidSourceFile, NameCollisionError)
from db import execute_scalar

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _partial(request: Request, template: str, ctx: dict, status_code: int = 200):
    return _templates(request).TemplateResponse(
        request, template, {"current_user": request.state.user, **ctx},
        status_code=status_code,
    )


def _card_partial(request: Request, package_id: str, status_code: int = 200,
                  error: str | None = None):
    # `error` renders a .form-banner--error inside the card so the global toast
    # scraper surfaces the specific reason (HTMX drops non-2xx bodies otherwise).
    card = sync.get_package_card(package_id, with_counts=True)
    return _partial(request, "partials/package_card.html",
                    {"card": card, "is_active": True, "error": error},
                    status_code=status_code)


def _with_unchecked_toast(response, unchecked_names: list[str]):
    """Attach the fail-open warning toast (issue #17): the save went through but
    these names couldn't be checked against Risk Modeler. htmx re-dispatches the
    HX-Trigger event on the requesting element; app.js listens for ``rwb:toast``."""
    if unchecked_names:
        unique = ", ".join(dict.fromkeys(unchecked_names))
        response.headers["HX-Trigger"] = json.dumps({"rwb:toast": {
            "message": f"Couldn't reach Risk Modeler to check {unique} for "
                       "duplicates — the import will fail if a name is already "
                       "taken.",
            "type": "warning"}})
    return response


def _package_actionable(package_id: str) -> bool:
    """Read-only gate (FR-025): a package is actionable unless every submission it is
    attached to is closed. Attached to none (a library package) → actionable."""
    attached = execute_scalar(
        "SELECT COUNT(*) FROM submission_package WHERE package_id = :p",
        {"p": package_id}, connection="WORKBENCH")
    if not attached:
        return True
    active = execute_scalar(
        "SELECT COUNT(*) FROM submission_package sp "
        "JOIN submission s ON s.id = sp.submission_id "
        "WHERE sp.package_id = :p AND s.status_code = 'ACTIVE'",
        {"p": package_id}, connection="WORKBENCH")
    return bool(active)


def _submission_active(submission_id: str) -> bool:
    status = execute_scalar("SELECT status_code FROM submission WHERE id = :id",
                            {"id": submission_id}, connection="WORKBENCH")
    return status == "ACTIVE"


def _default_name(path: str) -> str:
    """Fallback member name when the client sent none: the source filename with its
    trailing extension dropped (``…/PORTFOLIO.BAK`` → ``PORTFOLIO``). Mirrors the
    modal's client-side default; server-side validation still guards the result."""
    base = os.path.basename(path.replace("\\", "/"))
    stem, _dot, _ext = base.rpartition(".")
    return stem or base


def _parse_members(kinds, names, paths) -> list[sync.MemberSpec]:
    members: list[sync.MemberSpec] = []
    for kind, name, path in zip(kinds, names, paths):
        if not path:
            continue
        members.append(sync.MemberSpec(
            kind="rdm" if kind == "rdm" else "edm",
            name=(name or "").strip() or _default_name(path),
            source_file_path=path))
    return members


# ── Modal ─────────────────────────────────────────────────────────────────────

@router.get("/submissions/{submission_id}/packages/new", response_class=HTMLResponse)
def new_modal(request: Request, submission_id: str):
    if not _submission_active(submission_id):
        return _partial(request, "partials/package_modal.html",
                        {"submission_id": submission_id, "closed": True},
                        status_code=409)
    return _partial(request, "partials/package_modal.html",
                    {"submission_id": submission_id, "closed": False})


# ── As-you-type member name check ─────────────────────────────────────────────

@router.get("/packages/member-name-check", response_class=HTMLResponse)
def member_name_check(request: Request, member_kind: str = "",
                      member_name: str = ""):
    """Collision fragment for a modal member row (issue #17). A static URL on
    purpose: htmx 1.9 captures ``hx-get`` at element-processing time, so an
    Alpine-bound URL would not follow EDM↔RDM kind flips — instead the row's
    hidden ``member_kind`` input rides along via ``hx-include`` and is read fresh
    per request. Cached RM read only (Article 11)."""
    check = name_check.check_member_name(member_kind, member_name)
    return _partial(request, "partials/name_collision.html",
                    {"check": check, "name": member_name,
                     "kind": "RDM" if member_kind == "rdm" else "EDM"})


# ── Save ────────────────────────────────────────────────────────────────────────

@router.post("/submissions/{submission_id}/packages")
def save(
    request: Request,
    submission_id: str,
    name: str = Form(""),
    member_kind: list[str] = Form(default=[]),
    member_name: list[str] = Form(default=[]),
    member_path: list[str] = Form(default=[]),
    action: str = Form("save"),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
    if not _submission_active(submission_id):
        return _partial(request, "partials/package_modal.html",
                        {"submission_id": submission_id, "closed": True},
                        status_code=409)
    members = _parse_members(member_kind, member_name, member_path)
    try:
        result = sync.save_package(package_id=None, name=name.strip() or None,
                                   members=members, actor_id=request.state.user.id)
    except EmptyPackageError:
        return _partial(request, "partials/package_modal.html",
                        {"submission_id": submission_id, "closed": False,
                         "error": "Add at least one EDM or RDM."}, status_code=422)
    except (InvalidSourceFile, InvalidMemberName, NameCollisionError) as exc:
        return _partial(request, "partials/package_modal.html",
                        {"submission_id": submission_id, "closed": False,
                         "error": str(exc)}, status_code=422)
    package_service.attach_to_submission(
        submission_id=submission_id, package_id=result.package_id,
        actor_id=request.state.user.id)
    # "Save & Sync" (action=sync) also enqueues the member import; plain "Save" just
    # persists + attaches. Submits stay worker-side (Article 11).
    unchecked = list(result.unchecked_names)
    if action == "sync":
        try:
            unchecked += sync.save_and_sync(package_id=result.package_id,
                                            actor_id=request.state.user.id)
        except NameCollisionError as exc:
            # Vanishingly rare (the save-time check just passed and is cached),
            # but the package IS saved+attached by now — return the card WITH
            # 200, not 422: htmx drops non-2xx bodies, so a 422 would leave the
            # saved package invisible and the modal open over stale members
            # (inviting a duplicate save). 200 appends the card — whose banner
            # names what actually happened — and closes the modal.
            return _card_partial(request, result.package_id, error=str(exc))
    return _with_unchecked_toast(
        _card_partial(request, result.package_id), unchecked)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.post("/packages/{package_id}")
def edit(
    request: Request,
    package_id: str,
    name: str = Form(""),
    member_kind: list[str] = Form(default=[]),
    member_name: list[str] = Form(default=[]),
    member_path: list[str] = Form(default=[]),
    updated_at: str = Form(...),
    csrf_token: str = Form(...),
):
    # UNUSED this iteration (review item 6, verified): no template posts here — the
    # modal is create-only (GET .../packages/new → POST /submissions/{id}/packages with
    # package_id=None); the card wires only /sync, /delete, /retry. Kept as the wired
    # half of the FR-039/SC-010 package-edit concurrency guard (expected_updated_at).
    # HAZARD before wiring an edit modal: save_package's edit path (package_id set) is
    # INSERT-only for members — it never removes/reconciles — so posting existing
    # members here would DUPLICATE them. A future edit flow MUST reconcile/replace
    # members (or send none) before this route is reached with any member rows.
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/submissions", status_code=303)
    if not _package_actionable(package_id):
        return _card_partial(request, package_id, status_code=409)
    members = _parse_members(member_kind, member_name, member_path)
    try:
        sync.save_package(package_id=package_id, name=name.strip() or None,
                          members=members, actor_id=request.state.user.id,
                          expected_updated_at=updated_at)
    except ConcurrencyConflict:
        return _card_partial(request, package_id, status_code=409)
    except (EmptyPackageError, InvalidSourceFile, InvalidMemberName,
            NameCollisionError):
        return _card_partial(request, package_id, status_code=422)
    return _card_partial(request, package_id)


# ── Save and Sync ─────────────────────────────────────────────────────────────

@router.post("/packages/{package_id}/sync")
def sync_package(request: Request, package_id: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/submissions", status_code=303)
    if not _package_actionable(package_id):
        return _card_partial(request, package_id, status_code=409)
    try:
        unchecked = sync.save_and_sync(package_id=package_id,
                                       actor_id=request.state.user.id)
    except EmptyPackageError:
        return _card_partial(request, package_id, status_code=422)
    except NameCollisionError as exc:
        # A stale draft's name got taken since save (issue #17) — nothing was
        # enqueued; the card banner carries the specific names for the toast.
        return _card_partial(request, package_id, status_code=422, error=str(exc))
    return _with_unchecked_toast(_card_partial(request, package_id), unchecked)


# ── Live card poll (self-terminating) ───────────────────────────────────────────

@router.get("/packages/{package_id}/card", response_class=HTMLResponse)
def card(request: Request, package_id: str):
    """Read-only card render for HTMX polling. The template emits the ``every 3s``
    trigger only while a member is still in flight (pending_import / importing /
    delete_pending) or a job is active, so the browser keeps the EDM/RDM chips and
    job counts fresh without a page reload — and polling stops on its own once every
    member reaches a terminal status. No writes, no Risk Modeler call (Article 11)."""
    card = sync.get_package_card(package_id, with_counts=True)
    if card is None:
        # Package hard-gone: an empty outerHTML swap removes the card and ends polling.
        return HTMLResponse("")
    return _partial(request, "partials/package_card.html",
                    {"card": card, "is_active": _package_actionable(package_id)})


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/packages/{package_id}/delete")
def delete(request: Request, package_id: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/submissions", status_code=303)
    if not _package_actionable(package_id):
        return _card_partial(request, package_id, status_code=409)
    sync.delete_package(package_id=package_id, actor_id=request.state.user.id)
    return _card_partial(request, package_id)


# ── Per-member retry ──────────────────────────────────────────────────────────

@router.post("/packages/{package_id}/members/{member_id}/retry")
def retry_member(
    request: Request,
    package_id: str,
    member_id: str,
    member_kind: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/submissions", status_code=303)
    if not _package_actionable(package_id):
        return _card_partial(request, package_id, status_code=409)
    sync.retry_member(package_id=package_id, member_id=member_id,
                      member_kind=member_kind, actor_id=request.state.user.id)
    return _card_partial(request, package_id)


__all__ = ["router"]
