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

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.services import name_check, package_service
from app.services import package_sync_service as sync
from app.services.errors import (
    ConcurrencyConflict, EmptyPackageError, InvalidMemberName,
    InvalidSourceFile, MemberNotAttachable, NameCollisionError)
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
                  error: str | None = None, notice: str | None = None):
    # `error` renders a .form-banner--error inside the card so the global toast
    # scraper surfaces the specific reason (HTMX drops non-2xx bodies otherwise).
    # `notice` is the neutral counterpart (plain .form-banner) for an action that
    # SUCCEEDED but whose consequence needs stating — e.g. an attach submitted nothing
    # to Risk Modeler (issue #22). Deliberately not the error variant: the toast scraper
    # picks that up, and a success would read as a failure.
    card = sync.get_package_card(package_id, with_counts=True)
    return _partial(request, "partials/package_card.html",
                    {"card": card, "is_active": True, "error": error,
                     "notice": notice},
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

def _new_modal_context(submission_id: str, **extra) -> dict:
    """Create-modal context. ``candidates_url`` wires the attach disclosure (issue #22) —
    a URL, not data: the candidate list loads only when the analyst opens the disclosure,
    and reports its own empty state from there.

    Deliberately NO candidate count here. Counting would put a DB read on every render of
    this modal including the 422 error re-renders, which otherwise need none — the price
    of a nicer summary line is not worth a new dependency on an error path."""
    return {"submission_id": submission_id, "closed": False,
            "candidates_url": _candidates_url(None), **extra}


@router.get("/submissions/{submission_id}/packages/new", response_class=HTMLResponse)
def new_modal(request: Request, submission_id: str):
    if not _submission_active(submission_id):
        return _partial(request, "partials/package_modal.html",
                        {"submission_id": submission_id, "closed": True},
                        status_code=409)
    return _partial(request, "partials/package_modal.html",
                    _new_modal_context(submission_id))


# ── Attach an existing EDM/RDM (issue #22) ────────────────────────────────────

def _picks(edm_ids, rdm_ids, *, drop: str = "") -> list[sync.ExistingMember]:
    """Turn the picker's two id lists into ordered, de-duplicated picks.

    De-duplication is load-bearing, not defensive: the tick request includes the whole
    picker, so an id could arrive from both a tray chip and a checked row. The template
    is built so that cannot happen (a chip emits its hidden input only when its row is
    off-page), but the count and the attach tally must not depend on that template detail
    holding — a duplicate would silently report attaching two members when there is one.

    ``drop`` is the ``kind:id`` a chip's ✕ asked to remove; it is excluded here rather
    than client-side, so the removal works whichever input carried the id."""
    picks: list[sync.ExistingMember] = []
    seen: set[tuple[str, str]] = set()
    for kind, ids in (("edm", edm_ids), ("rdm", rdm_ids)):
        for raw in ids:
            mid = (raw or "").strip()
            if not mid or (kind, mid) in seen or f"{kind}:{mid}" == drop:
                continue
            seen.add((kind, mid))
            picks.append(sync.ExistingMember(kind=kind, id=mid))
    return picks


def _candidates_url(package_id: str | None) -> str:
    """Where the picker re-renders itself from. The New-package modal has no package yet,
    so it uses the package-less variant — the candidate set does not depend on the
    package either way (it is every entity attached to *no* package)."""
    return (f"/packages/{package_id}/members/candidates" if package_id
            else "/packages/members/candidates")


def _picker_context(picks, *, package_id: str | None, q: str, page: int) -> dict:
    """Everything ``partials/member_picker.html`` needs. ``total_unfiltered`` is a second,
    unfiltered read used only to tell "your search matched nothing" apart from "there is
    nothing to attach" — two states that must not share one message. It is skipped when
    no search is active, where the filtered total already is the unfiltered one."""
    candidates = sync.list_unattached_members(name=q or None, page=page)
    picked = sync.resolve_picks(
        edm_ids=[p.id for p in picks if p.kind == "edm"],
        rdm_ids=[p.id for p in picks if p.kind == "rdm"])
    return {
        "page": candidates,
        "picked": picked,
        "picked_ids": {f"{c.kind}:{c.id}" for c in picked},
        "page_ids": {f"{c.kind}:{c.id}" for c in candidates.rows},
        "total_unfiltered": (sync.list_unattached_members().total if q
                             else candidates.total),
        "candidates_url": _candidates_url(package_id),
        "q": q,
        "package_id": package_id,
    }


@router.get("/packages/members/candidates", response_class=HTMLResponse)
def new_package_candidates(
    request: Request,
    q: str = "",
    page: int = 1,
    drop: str = "",
    existing_edm_ids: list[str] = Query(default=[]),
    existing_rdm_ids: list[str] = Query(default=[]),
):
    """The same picker for the New-package modal, which has no package id yet. A literal
    path, declared BEFORE ``/packages/{package_id}/...`` so the parameter route never
    shadows it (the ordering convention in ``edms.py``)."""
    picks = _picks(existing_edm_ids, existing_rdm_ids, drop=drop.strip())
    return _partial(request, "partials/member_picker.html",
                    _picker_context(picks, package_id=None, q=q.strip(),
                                    page=max(1, page)))


@router.get("/packages/{package_id}/members/add", response_class=HTMLResponse)
def add_members_modal(request: Request, package_id: str):
    """The attach modal. GET, no CSRF — it writes nothing."""
    if not _package_actionable(package_id):
        return _partial(request, "partials/package_members_modal.html",
                        {"package_id": package_id, "readonly": True},
                        status_code=409)
    return _partial(request, "partials/package_members_modal.html",
                    {"readonly": False,
                     **_picker_context([], package_id=package_id, q="", page=1)})


@router.get("/packages/{package_id}/members/candidates", response_class=HTMLResponse)
def member_candidates(
    request: Request,
    package_id: str,
    q: str = "",
    page: int = 1,
    drop: str = "",
    existing_edm_ids: list[str] = Query(default=[]),
    existing_rdm_ids: list[str] = Query(default=[]),
):
    """Re-render the picker for a search, a page turn, a tick, or a chip removal — one
    endpoint for all four, differing only in what the caller targets (the whole picker,
    or ``#picker-tray`` alone via ``hx-select``).

    The current selection round-trips in the query string, which is why the tray survives
    a filter that hides its rows. A read, so no CSRF; out-of-range pages are clamped by
    the service rather than rejected, because the candidate set shrinks under the analyst
    whenever anyone else attaches something."""
    picks = _picks(existing_edm_ids, existing_rdm_ids, drop=drop.strip())
    return _partial(request, "partials/member_picker.html",
                    _picker_context(picks, package_id=package_id,
                                    q=q.strip(), page=max(1, page)))


@router.post("/packages/{package_id}/members")
def add_members(
    request: Request,
    package_id: str,
    existing_edm_ids: list[str] = Form(default=[]),
    existing_rdm_ids: list[str] = Form(default=[]),
    csrf_token: str = Form(...),
):
    """Attach the picked entities. Nothing is submitted to Risk Modeler (Article 5) —
    the analyst's separate Save & Sync click applies the package's RDMs to its EDMs.

    A partial attach returns **200 with the card**, not 422: htmx drops non-2xx bodies, so
    a 422 would leave the modal open over a stale candidate list inviting a re-submit
    (the same reasoning as ``save()`` above). The banner names what was skipped, which
    matters more the more the analyst picked."""
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/submissions", status_code=303)
    if not _package_actionable(package_id):
        return _card_partial(request, package_id, status_code=409)
    picks = _picks(existing_edm_ids, existing_rdm_ids)
    if not picks:
        return _card_partial(request, package_id)
    result = sync.attach_existing_members(
        package_id=package_id, picks=picks, actor_id=request.state.user.id)
    note = f"Attached {result.attached} member(s)."
    if result.skipped:
        return _card_partial(request, package_id, error=(
            f"{note} Skipped {', '.join(result.skipped)} — "
            "it may have been deleted, already belong to another package, or be on its "
            "way out of Risk Modeler."))
    # Plain .form-banner, not --error: the toast scraper only picks up the error variant,
    # so this reads as information rather than a failure.
    return _card_partial(request, package_id, notice=(
        f"{note} Nothing was submitted to Risk Modeler — click Save & Sync to apply "
        "this package's RDMs to its EDMs."))


@router.post("/packages/{package_id}/members/{member_id}/remove")
def remove_member(
    request: Request,
    package_id: str,
    member_id: str,
    member_kind: str = Form("edm"),
    csrf_token: str = Form(...),
):
    """Detach a member: clear its ``package_id`` and leave it in Risk Modeler, back in
    the standalone library. **Not** the card's Delete, which removes every member FROM
    Risk Modeler. Emptying a package this way soft-deletes the package row (R5/FR-027),
    so the card comes back in its deleted state — which is why this returns the card
    rather than removing the row client-side."""
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/submissions", status_code=303)
    if not _package_actionable(package_id):
        return _card_partial(request, package_id, status_code=409)
    try:
        package_service.remove_member(
            package_id=package_id, member_id=member_id,
            member_kind="rdm" if member_kind == "rdm" else "edm",
            actor_id=request.state.user.id)
    except MemberNotAttachable as exc:
        return _card_partial(request, package_id, error=str(exc))
    return _card_partial(request, package_id)


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
    existing_edm_ids: list[str] = Form(default=[]),
    existing_rdm_ids: list[str] = Form(default=[]),
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
    # Already-imported picks from the modal's disclosure (issue #22). They make the
    # package non-empty on their own, so an attach-only package is legal — it has no
    # names to collision-check, since every pick already exists in Risk Modeler under a
    # name RM itself accepted. The empty-package guard therefore has to consider both.
    picks = _picks(existing_edm_ids, existing_rdm_ids)
    try:
        result = sync.save_package(
            package_id=None, name=name.strip() or None, members=members,
            existing=picks, actor_id=request.state.user.id)
    except EmptyPackageError:
        return _partial(request, "partials/package_modal.html",
                        _new_modal_context(submission_id,
                                           error="Add at least one EDM or RDM."),
                        status_code=422)
    except (InvalidSourceFile, InvalidMemberName, MemberNotAttachable,
            NameCollisionError) as exc:
        # MemberNotAttachable rolls the whole create back (save_package attaches picks
        # in-transaction), so nothing was saved and re-showing the modal is honest.
        return _partial(request, "partials/package_modal.html",
                        _new_modal_context(submission_id, error=str(exc)),
                        status_code=422)
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
