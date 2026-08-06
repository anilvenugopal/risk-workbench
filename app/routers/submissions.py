"""Submission routes — master-detail list, create/edit, status, CRM tags.

Server-rendered FastAPI + Jinja2 + HTMX (Article 8). Every route requires an
authenticated session (SessionMiddleware); every state-changing route validates a
CSRF token (Article 13). No route applies row-level access — any authenticated
analyst may load and act on any submission (Article 6 / FR-019).

Service errors are mapped to HTTP here:
  SubmissionClosed / ConcurrencyConflict → 409 banner (input preserved)
  SelfLinkError / UnknownLinkError       → 422
  duplicate look-alikes (unconfirmed)    → non-blocking dup-warning partial

Field-level validation returns 422 with a ``field_errors`` dict the form renders
under the offending input, plus a one-line summary banner (CR4). The unconfirmed
duplicate warning is the one re-render that stays 200: nothing the analyst typed
is wrong.
"""

from __future__ import annotations

from datetime import date
from functools import partial
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.services import package_sync_service, submission_service
from app.services.errors import (
    ConcurrencyConflict,
    SelfLinkError,
    SubmissionClosed,
    UnknownLinkError,
)
from db import execute

router = APIRouter()

TREATY_TYPES = [
    ("cat_xol", "Cat XoL"), ("quota_share", "Quota Share"), ("surplus", "Surplus"),
    ("per_risk_xol", "Per-Risk XoL"), ("aggregate_xol", "Aggregate XoL"),
    ("stop_loss", "Stop Loss"),
]

# Shown under "links to" when the posted id names no submission — the deal was
# renamed away or closed while the form sat open, or the page is stale.
_UNKNOWN_LINK_MESSAGE = "That deal was not found — pick the linked deal again."


# ── helpers ──────────────────────────────────────────────────────────────────

def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, nav_key: str,
            extra: dict | None = None, status_code: int = 200):
    current_user = request.state.user
    nav = get_nav_context(current_user, nav_key)
    ctx = {"current_user": current_user, "nav": nav, **(extra or {})}
    return _templates(request).TemplateResponse(
        request, template, ctx, status_code=status_code)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _partial(request: Request, template: str, ctx: dict, status_code: int = 200):
    ctx = {"current_user": request.state.user, **ctx}
    return _templates(request).TemplateResponse(
        request, template, ctx, status_code=status_code)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


MIN_TREATY_YEAR, MAX_TREATY_YEAR = 1900, 2999


def _validate_submission_form(
    *, name: str, cedant_name: str, treaty_type_code: str, inception_date: str,
    treaty_year: str,
) -> tuple[dict[str, str], date | None, int | None]:
    """One message per bad field (CR4), plus the parsed inception date and treaty
    year so the caller does not parse twice. An empty dict means valid."""
    errors: dict[str, str] = {}
    if not name.strip():
        errors["name"] = "Enter a name for this submission."
    if not cedant_name.strip():
        errors["cedant_name"] = "Enter a cedant."
    if not treaty_type_code:
        errors["treaty_type_code"] = "Choose a treaty type."

    parsed_inception_date = _parse_date(inception_date)
    if parsed_inception_date is None:
        errors["inception_date"] = (
            "Enter an inception date." if not inception_date.strip()
            else "Enter a valid date.")

    parsed_treaty_year = _parse_int(treaty_year)
    if treaty_year.strip() and not (
            parsed_treaty_year is not None
            and MIN_TREATY_YEAR <= parsed_treaty_year <= MAX_TREATY_YEAR):
        errors["treaty_year"] = (
            f"Enter a year between {MIN_TREATY_YEAR} and {MAX_TREATY_YEAR}.")

    return errors, parsed_inception_date, parsed_treaty_year


def _error_banner(field_errors: dict[str, str], action: str) -> list[str]:
    """Summary line above the form. The per-field messages carry the detail."""
    count = len(field_errors)
    if not count:
        return []
    subject = "One field needs" if count == 1 else f"{count} fields need"
    return [f"{subject} attention before this deal can be {action}."]


def _form_context(
    *, mode: str, form: dict, submission, links_to: str | None = None,
    errors: list[str] | None = None, field_errors: dict[str, str] | None = None,
    warnings: list | None = None,
) -> dict:
    """The render context for ``pages/submission_form.html``. ``errors``,
    ``field_errors`` and ``warnings`` are three same-shaped collections the
    template treats differently, so they are keyword-only."""
    return {
        "mode": mode,
        "treaty_types": TREATY_TYPES,
        "form": form,
        "submission": submission,
        "link_target": submission_service.get_submission(links_to),
        "errors": errors or [],
        "field_errors": field_errors or {},
        "warnings": warnings or [],
        "min_suggest_term": submission_service.MIN_SUGGEST_TERM,
        "min_treaty_year": MIN_TREATY_YEAR,
        "max_treaty_year": MAX_TREATY_YEAR,
    }


def _reshow_form(
    request: Request, *, mode: str, nav_key: str, form: dict, submission,
    links_to: str | None, errors=None, field_errors=None, warnings=None,
    status_code: int = 200,
):
    return _render(
        request, "pages/submission_form.html", nav_key,
        _form_context(mode=mode, form=form, submission=submission,
                      links_to=links_to, errors=errors,
                      field_errors=field_errors, warnings=warnings),
        status_code=status_code)


def _active_analysts() -> list[dict]:
    """Every active user, for the detail page's reassign picker and the list's
    Owner filter."""
    return execute(
        "SELECT id, display_name FROM app_user WHERE is_active = 1 "
        "ORDER BY display_name",
        {}, connection="WORKBENCH",
    )


def _detail_context(request: Request, submission_id: str) -> dict | None:
    """Assemble the full detail-view context, or None if the id is unknown."""
    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return None
    analysts = _active_analysts()
    return {
        "submission": submission,
        "status_history": submission_service.get_status_history(submission_id),
        "crm_tags": submission_service.list_crm_ids(submission_id),
        "package_cards": package_sync_service.get_package_cards(submission_id),
        "link_target": submission_service.get_submission(
            submission.links_to_submission_id),
        "analysts": analysts,
        "treaty_types": TREATY_TYPES,
        "is_active": submission.status_code == submission_service.ACTIVE,
    }


def _detail_response(request: Request, submission_id: str, status_code: int = 200):
    """Render the detail page (full-page GET or HTMX partial swap)."""
    ctx = _detail_context(request, submission_id)
    if ctx is None:
        return _not_found(request)
    nav = get_nav_context(request.state.user, "submissions.detail")
    return _templates(request).TemplateResponse(
        request, "pages/submission_detail.html",
        {"current_user": request.state.user, "nav": nav, **ctx},
        status_code=status_code,
    )


def _not_found(request: Request):
    return _templates(request).TemplateResponse(
        request, "base/error.html",
        {"status_code": 404, "title": "Not found",
         "detail": "That submission does not exist.",
         "is_htmx": _is_htmx(request), "current_user": request.state.user},
        status_code=404,
    )


# ── List (My / All) + filters ────────────────────────────────────────────────

# The id of the div the filter form and the pager links both target. A request
# naming it gets the table on its own: rebuilding the status list, the analyst
# list and the nav shell for htmx to discard is the cost of a keystroke otherwise.
_LIST_TARGET = "sub-list"


def _owner_label(analysts: list[dict], owner_id) -> str:
    """The picked analyst's display name, for the Owner box to show, or "" when no
    analyst is picked. Compared as lowercase strings because the id reaches here
    from a query string while ``app_user.id`` arrives from the driver."""
    return next((a["display_name"] for a in analysts
                 if str(a["id"]).lower() == str(owner_id).lower()), "")


def _list_page(request: Request, *, owner_id, nav_key: str):
    filters = {
        "name": (request.query_params.get("q") or "").strip() or None,
        "cedant_name": (request.query_params.get("cedant") or "").strip() or None,
        "crm_id": (request.query_params.get("crm_id") or "").strip() or None,
        "treaty_type_code": (request.query_params.get("treaty_type") or "").strip() or None,
        "inception_date": _parse_date(request.query_params.get("inception")),
        "treaty_year": _parse_int(request.query_params.get("treaty_year")),
        "status_code": (request.query_params.get("status") or "").strip() or None,
    }
    # The Owner menu sends an app_user id. On /submissions/mine the route already
    # names the owner, so the picked id only applies to the All list.
    picked_owner = (request.query_params.get("owner") or "").strip() or None
    listing = submission_service.list_submissions(
        owner_id=owner_id or picked_owner,
        page=_parse_int(request.query_params.get("page")) or 1,
        **filters)
    # Echoed back into the inputs so a filtered request re-renders what was typed,
    # and read by the template to tell "nothing matches" from "nothing here yet".
    filter_values = {
        key: request.query_params.get(key, "")
        for key in ("q", "cedant", "crm_id", "owner", "treaty_type", "inception",
                    "treaty_year", "status")
    }
    list_ctx = {
        "rows": listing.rows,
        "page": listing.page,
        "has_next": listing.has_next,
        "base": "/submissions/mine" if owner_id else "/submissions",
        # The applied filters, for the pager links to carry; each link appends its
        # own page number.
        "filter_query": urlencode(
            {key: value for key, value in filter_values.items() if value}),
        "is_filtered": any(filter_values.values()),
    }
    if request.headers.get("HX-Target") == _LIST_TARGET:
        return _partial(request, "partials/submission_list.html", list_ctx)
    analysts = _active_analysts()
    return _render(request, "pages/submissions.html", nav_key, {
        **list_ctx,
        "treaty_types": TREATY_TYPES,
        "statuses": submission_service.status_kinds(),
        "analysts": analysts,
        "owner_label": _owner_label(analysts, picked_owner),
        "scope": "mine" if owner_id else "all",
        "filter_values": filter_values,
    })


@router.get("/submissions", response_class=HTMLResponse)
def list_all(request: Request):
    return _list_page(request, owner_id=None, nav_key="submissions.all")


@router.get("/submissions/mine", response_class=HTMLResponse)
def list_mine(request: Request):
    return _list_page(request, owner_id=request.state.user.id,
                      nav_key="submissions.mine")


def _suggest_menu(request: Request, options: list[dict], term: str,
                  empty_message: str, menu_id: str):
    """Render one of the two typeahead menus. ``menu_id`` is the id of the div
    htmx swaps into, and each option derives its own id from it."""
    return _partial(request, "partials/typeahead_menu.html", {
        "options": options,
        "searched": len(term.strip()) >= submission_service.MIN_SUGGEST_TERM,
        "empty_message": empty_message,
        "menu_id": menu_id,
    })


@router.get("/submissions/cedant-suggest", response_class=HTMLResponse)
def cedant_suggest(request: Request):
    """Typeahead menu for the create/edit form's CEDANT field (FR-006/R6).

    htmx sends the field under its own name, so the term arrives as
    ``cedant_name``; ``q`` (the name in the 002 contract) is still accepted for a
    hand-built call."""
    term = (request.query_params.get("cedant_name")
            or request.query_params.get("q", ""))
    return _suggest_menu(
        request,
        [{"value": cedant, "label": cedant}
         for cedant in submission_service.cedant_suggestions(term)],
        term, "No matching cedant.", "cedant-menu",
    )


@router.get("/submissions/link-suggest", response_class=HTMLResponse)
def link_suggest(request: Request):
    """Typeahead menu for the "links to" picker (CR8). Searches name and cedant;
    ``links_to_exclude`` drops the submission being edited from its own results.

    htmx sends both inputs under their own names; ``q``/``exclude`` are accepted
    for a hand-built call."""
    term = (request.query_params.get("links_to_search")
            or request.query_params.get("q", ""))
    exclude_id = (request.query_params.get("links_to_exclude")
                  or request.query_params.get("exclude") or None)
    matches = submission_service.search_submissions_for_link(
        term, exclude_id=exclude_id,
    )
    return _suggest_menu(
        request,
        [
            {
                "value": row.id,
                "label": row.name,
                "meta": " · ".join(filter(None, [
                    row.cedant_name,
                    row.treaty_type_label or row.treaty_type_code,
                    str(row.inception_date),
                ])),
            }
            for row in matches
        ],
        term, "No matching submission.", "link-menu",
    )


# ── Create ────────────────────────────────────────────────────────────────────

@router.get("/submissions/new", response_class=HTMLResponse)
def new_form(request: Request):
    return _render(request, "pages/submission_form.html", "submissions.all",
                   _form_context(mode="create", form={}, submission=None))


@router.post("/submissions")
def create(
    request: Request,
    # The four required fields are declared optional here on purpose (CR4): a
    # field FastAPI rejects itself returns raw JSON, which is the least clear
    # thing an analyst can be shown. _validate_submission_form owns every message.
    name: str = Form(""),
    cedant_name: str = Form(""),
    treaty_type_code: str = Form(""),
    inception_date: str = Form(""),
    treaty_year: str = Form(""),
    directory_path: str = Form(""),
    crm_ids: str = Form(""),
    links_to_submission_id: str = Form(""),
    confirmed: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse("/submissions/new", status_code=303)

    form = {
        "name": name, "cedant_name": cedant_name,
        "treaty_type_code": treaty_type_code, "inception_date": inception_date,
        "treaty_year": treaty_year, "directory_path": directory_path,
        "crm_ids": crm_ids,
        "links_to_submission_id": links_to_submission_id,
    }
    links_to = links_to_submission_id.strip() or None

    _reshow = partial(_reshow_form, request, mode="create",
                      nav_key="submissions.all", form=form, submission=None,
                      links_to=links_to)

    field_errors, parsed_inception_date, parsed_treaty_year = (
        _validate_submission_form(
            name=name, cedant_name=cedant_name,
            treaty_type_code=treaty_type_code, inception_date=inception_date,
            treaty_year=treaty_year,
        )
    )
    if field_errors:
        return _reshow(errors=_error_banner(field_errors, "created"),
                       field_errors=field_errors, status_code=422)

    try:
        result = submission_service.create_submission(
            name=name.strip(), cedant_name=cedant_name.strip(),
            treaty_type_code=treaty_type_code,
            inception_date=parsed_inception_date,
            treaty_year=parsed_treaty_year,
            directory_path=directory_path.strip() or None,
            crm_ids=crm_ids.split(","),
            links_to_submission_id=links_to,
            actor_id=request.state.user.id, confirmed=(confirmed == "1"),
        )
    except UnknownLinkError:
        return _reshow(field_errors={
            "links_to_submission_id": _UNKNOWN_LINK_MESSAGE}, status_code=422)
    if not result.created:
        # Non-blocking look-alike warning (FR-004): re-render form + dup list.
        # 200, not 422 — nothing the analyst typed is wrong, and "create anyway"
        # is one click away.
        return _reshow(warnings=result.warnings)
    return RedirectResponse(f"/submissions/{result.submission_id}", status_code=303)


# ── Detail ─────────────────────────────────────────────────────────────────────

@router.get("/submissions/{submission_id}", response_class=HTMLResponse)
def detail(request: Request, submission_id: str):
    return _detail_response(request, submission_id)


# ── Edit / update ──────────────────────────────────────────────────────────────

@router.get("/submissions/{submission_id}/edit", response_class=HTMLResponse)
def edit_form(request: Request, submission_id: str):
    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return _not_found(request)
    if submission.status_code != submission_service.ACTIVE:
        # Read-only gate (R3): closed deals are not editable.
        return _detail_response(request, submission_id, status_code=409)
    form = {
        "name": submission.name, "cedant_name": submission.cedant_name,
        "treaty_type_code": submission.treaty_type_code,
        "inception_date": str(submission.inception_date),
        "treaty_year": submission.treaty_year or "",
        "directory_path": submission.directory_path or "",
        "links_to_submission_id": submission.links_to_submission_id or "",
    }
    return _render(
        request, "pages/submission_form.html", "submissions.detail",
        _form_context(mode="edit", form=form, submission=submission,
                      links_to=submission.links_to_submission_id))


@router.post("/submissions/{submission_id}")
def update(
    request: Request,
    submission_id: str,
    # Optional here for the same reason as create() — see the note there.
    name: str = Form(""),
    cedant_name: str = Form(""),
    treaty_type_code: str = Form(""),
    inception_date: str = Form(""),
    treaty_year: str = Form(""),
    directory_path: str = Form(""),
    links_to_submission_id: str = Form(""),
    updated_at: str = Form(...),
    confirmed: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)

    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return _not_found(request)

    form = {
        "name": name, "cedant_name": cedant_name,
        "treaty_type_code": treaty_type_code, "inception_date": inception_date,
        "treaty_year": treaty_year, "directory_path": directory_path,
        "links_to_submission_id": links_to_submission_id,
    }
    links_to = links_to_submission_id.strip() or None

    _reshow = partial(_reshow_form, request, mode="edit",
                      nav_key="submissions.detail", form=form,
                      submission=submission, links_to=links_to)

    field_errors, parsed_inception_date, parsed_treaty_year = (
        _validate_submission_form(
            name=name, cedant_name=cedant_name,
            treaty_type_code=treaty_type_code, inception_date=inception_date,
            treaty_year=treaty_year,
        )
    )
    if field_errors:
        return _reshow(errors=_error_banner(field_errors, "saved"),
                       field_errors=field_errors, status_code=422)

    try:
        result = submission_service.update_submission(
            submission_id=submission_id, expected_updated_at=updated_at,
            actor_id=request.state.user.id, confirmed=(confirmed == "1"),
            name=name.strip(), cedant_name=cedant_name.strip(),
            treaty_type_code=treaty_type_code, inception_date=parsed_inception_date,
            treaty_year=parsed_treaty_year,
            directory_path=directory_path.strip() or None,
            links_to_submission_id=links_to,
        )
    except SelfLinkError:
        return _reshow(
            field_errors={
                "links_to_submission_id": "A submission cannot link to itself."},
            status_code=422)
    except UnknownLinkError:
        return _reshow(field_errors={
            "links_to_submission_id": _UNKNOWN_LINK_MESSAGE}, status_code=422)
    except SubmissionClosed:
        return _detail_response(request, submission_id, status_code=409)
    except ConcurrencyConflict:
        return _reshow(
            errors=["This deal changed since you opened it — reload and re-apply."],
            status_code=409)

    if not result.updated:
        return _reshow(warnings=result.warnings)  # non-blocking dup warning
    return RedirectResponse(f"/submissions/{submission_id}", status_code=303)


# ── Reassign owner ─────────────────────────────────────────────────────────────

@router.post("/submissions/{submission_id}/reassign")
def reassign(
    request: Request,
    submission_id: str,
    new_owner_id: str = Form(...),
    updated_at: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
    try:
        submission_service.reassign_owner(
            submission_id=submission_id, new_owner_id=new_owner_id,
            expected_updated_at=updated_at, actor_id=request.state.user.id,
        )
    except (SubmissionClosed, ConcurrencyConflict):
        return _detail_response(request, submission_id, status_code=409)
    if _is_htmx(request):
        return _detail_response(request, submission_id)
    return RedirectResponse(f"/submissions/{submission_id}", status_code=303)


# ── Status lifecycle ───────────────────────────────────────────────────────────

@router.post("/submissions/{submission_id}/status")
def change_status(
    request: Request,
    submission_id: str,
    to_status: str = Form(...),
    reason: str = Form(""),
    updated_at: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
    if to_status not in ("ACTIVE", "COMPLETED", "CANCELLED"):
        return _detail_response(request, submission_id, status_code=422)
    try:
        submission_service.set_status(
            submission_id=submission_id, to_status=to_status,
            reason=reason.strip() or None, expected_updated_at=updated_at,
            actor_id=request.state.user.id,
        )
    except ConcurrencyConflict:
        return _detail_response(request, submission_id, status_code=409)
    if _is_htmx(request):
        return _detail_response(request, submission_id)
    return RedirectResponse(f"/submissions/{submission_id}", status_code=303)


# ── CRM tags ────────────────────────────────────────────────────────────────────

def _crm_partial(request: Request, submission_id: str, status_code: int = 200):
    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return _not_found(request)
    return _partial(request, "partials/crm_tags.html", {
        "submission": submission,
        "crm_tags": submission_service.list_crm_ids(submission_id),
        "is_active": submission.status_code == submission_service.ACTIVE,
    }, status_code=status_code)


@router.post("/submissions/{submission_id}/crm-ids")
def add_crm(
    request: Request,
    submission_id: str,
    crm_id: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
    try:
        if crm_id.strip():
            submission_service.add_crm_id(submission_id=submission_id, crm_id=crm_id,
                           actor_id=request.state.user.id)
    except SubmissionClosed:
        return _crm_partial(request, submission_id, status_code=409)
    if _is_htmx(request):
        return _crm_partial(request, submission_id)
    return RedirectResponse(f"/submissions/{submission_id}", status_code=303)


@router.post("/submissions/{submission_id}/crm-ids/{tag_id}/delete")
def delete_crm(
    request: Request,
    submission_id: str,
    tag_id: str,
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
    try:
        submission_service.remove_crm_id(crm_tag_id=tag_id, actor_id=request.state.user.id)
    except SubmissionClosed:
        return _crm_partial(request, submission_id, status_code=409)
    if _is_htmx(request):
        return _crm_partial(request, submission_id)
    return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
