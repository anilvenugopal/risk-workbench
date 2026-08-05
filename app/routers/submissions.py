"""Submission routes — master-detail list, create/edit, status, CRM tags.

Server-rendered FastAPI + Jinja2 + HTMX (Article 8). Every route requires an
authenticated session (SessionMiddleware); every state-changing route validates a
CSRF token (Article 13). No route applies row-level access — any authenticated
analyst may load and act on any submission (Article 6 / FR-019).

Service errors are mapped to HTTP here:
  SubmissionClosed / ConcurrencyConflict → 409 banner (input preserved)
  SelfLinkError                          → 422
  duplicate look-alikes (unconfirmed)    → non-blocking dup-warning partial

Field-level validation returns a ``field_errors`` dict the form renders under the
offending input, plus a one-line summary banner (CR4) — a single combined message
never told the analyst which field failed.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.services import package_sync_service, submission_service
from app.services.errors import (
    ConcurrencyConflict,
    SelfLinkError,
    SubmissionClosed,
)
from db import execute

router = APIRouter()

TREATY_TYPES = [
    ("cat_xol", "Cat XoL"), ("quota_share", "Quota Share"), ("surplus", "Surplus"),
    ("per_risk_xol", "Per-Risk XoL"), ("aggregate_xol", "Aggregate XoL"),
    ("stop_loss", "Stop Loss"),
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, nav_key: str, extra: dict | None = None):
    current_user = request.state.user
    nav = get_nav_context(current_user, nav_key)
    ctx = {"current_user": current_user, "nav": nav, **(extra or {})}
    return _templates(request).TemplateResponse(request, template, ctx)


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
    year_range = f"Enter a year between {MIN_TREATY_YEAR} and {MAX_TREATY_YEAR}."
    if treaty_year.strip() and parsed_treaty_year is None:
        errors["treaty_year"] = year_range
    elif (parsed_treaty_year is not None
            and not MIN_TREATY_YEAR <= parsed_treaty_year <= MAX_TREATY_YEAR):
        errors["treaty_year"] = year_range

    return errors, parsed_inception_date, parsed_treaty_year


def _error_banner(field_errors: dict[str, str], action: str) -> list[str]:
    """Summary line above the form. The per-field messages carry the detail."""
    count = len(field_errors)
    if not count:
        return []
    subject = "One field needs" if count == 1 else f"{count} fields need"
    return [f"{subject} attention before this deal can be {action}."]


def _link_target(submission_id: str | None):
    """The submission currently chosen in "links to", so the picker can show its
    name instead of a UUID. None when nothing is linked or the id is stale."""
    if not submission_id:
        return None
    return submission_service.get_submission(submission_id)


def _detail_context(request: Request, submission_id: str) -> dict | None:
    """Assemble the full detail-view context, or None if the id is unknown."""
    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return None
    analysts = execute(
        "SELECT id, display_name FROM app_user WHERE is_active = 1 "
        "ORDER BY display_name",
        {}, connection="WORKBENCH",
    )
    return {
        "submission": submission,
        "status_history": submission_service.get_status_history(submission_id),
        "crm_tags": submission_service.list_crm_ids(submission_id),
        "package_cards": package_sync_service.get_package_cards(submission_id),
        "link_target": _link_target(submission.links_to_submission_id),
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

def _list_page(request: Request, *, owner_id, nav_key: str):
    filters = {
        "cedant_name": (request.query_params.get("cedant") or "").strip() or None,
        "treaty_type_code": (request.query_params.get("treaty_type") or "").strip() or None,
        "inception_date": _parse_date(request.query_params.get("inception")),
        "treaty_year": _parse_int(request.query_params.get("treaty_year")),
    }
    rows = submission_service.list_submissions(owner_id=owner_id, **filters)
    extra = {
        "rows": rows,
        "treaty_types": TREATY_TYPES,
        "scope": "mine" if owner_id else "all",
        "filter_values": {
            "cedant": request.query_params.get("cedant", ""),
            "treaty_type": request.query_params.get("treaty_type", ""),
            "inception": request.query_params.get("inception", ""),
            "treaty_year": request.query_params.get("treaty_year", ""),
        },
    }
    return _render(request, "pages/submissions.html", nav_key, extra)


@router.get("/submissions", response_class=HTMLResponse)
def list_all(request: Request):
    return _list_page(request, owner_id=None, nav_key="submissions.all")


@router.get("/submissions/mine", response_class=HTMLResponse)
def list_mine(request: Request):
    return _list_page(request, owner_id=request.state.user.id,
                      nav_key="submissions.mine")


def _suggest_menu(request: Request, options: list[dict], term: str,
                  empty_message: str):
    """Render one of the two typeahead menus.

    ``searched`` is what tells the template apart "we looked and found nothing"
    from "the term is too short to look yet" (``submission_service
    .MIN_SUGGEST_TERM``) — the second renders a blank menu, since claiming no
    cedant matches "a" would be wrong."""
    return _partial(request, "partials/typeahead_menu.html", {
        "options": options,
        "searched": len(term.strip()) >= submission_service.MIN_SUGGEST_TERM,
        "empty_message": empty_message,
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
        term, "No matching cedant.",
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
        term, "No matching submission.",
    )


# ── Create ────────────────────────────────────────────────────────────────────

@router.get("/submissions/new", response_class=HTMLResponse)
def new_form(request: Request):
    return _render(request, "pages/submission_form.html", "submissions.all", {
        "mode": "create", "treaty_types": TREATY_TYPES, "form": {}, "errors": [],
        "field_errors": {}, "warnings": [], "submission": None,
        "link_target": None,
    })


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
        "links_to_submission_id": links_to_submission_id,
    }
    links_to = links_to_submission_id.strip() or None

    def _reshow(errors, field_errors, warnings):
        return _render(request, "pages/submission_form.html", "submissions.all", {
            "mode": "create", "treaty_types": TREATY_TYPES, "form": form,
            "errors": errors, "field_errors": field_errors, "warnings": warnings,
            "submission": None, "link_target": _link_target(links_to),
        })

    field_errors, parsed_inception_date, parsed_treaty_year = (
        _validate_submission_form(
            name=name, cedant_name=cedant_name,
            treaty_type_code=treaty_type_code, inception_date=inception_date,
            treaty_year=treaty_year,
        )
    )
    if field_errors:
        return _reshow(_error_banner(field_errors, "created"), field_errors, [])

    result = submission_service.create_submission(
        name=name.strip(), cedant_name=cedant_name.strip(),
        treaty_type_code=treaty_type_code, inception_date=parsed_inception_date,
        treaty_year=parsed_treaty_year,
        directory_path=directory_path.strip() or None,
        links_to_submission_id=links_to,
        actor_id=request.state.user.id, confirmed=(confirmed == "1"),
    )
    if not result.created:
        # Non-blocking look-alike warning (FR-004): re-render form + dup list.
        return _reshow([], {}, result.warnings)
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
    return _render(request, "pages/submission_form.html", "submissions.detail", {
        "mode": "edit", "treaty_types": TREATY_TYPES, "form": form,
        "errors": [], "field_errors": {}, "warnings": [], "submission": submission,
        "link_target": _link_target(submission.links_to_submission_id),
    })


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

    def _reshow(errors, warnings, field_errors=None, status_code=200):
        nav = get_nav_context(request.state.user, "submissions.detail")
        return _templates(request).TemplateResponse(
            request, "pages/submission_form.html",
            {"current_user": request.state.user, "nav": nav, "mode": "edit",
             "treaty_types": TREATY_TYPES, "form": form, "errors": errors,
             "field_errors": field_errors or {}, "warnings": warnings,
             "submission": submission, "link_target": _link_target(links_to)},
            status_code=status_code,
        )

    field_errors, parsed_inception_date, parsed_treaty_year = (
        _validate_submission_form(
            name=name, cedant_name=cedant_name,
            treaty_type_code=treaty_type_code, inception_date=inception_date,
            treaty_year=treaty_year,
        )
    )
    if field_errors:
        return _reshow(_error_banner(field_errors, "saved"), [], field_errors)

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
            [], [],
            {"links_to_submission_id": "A submission cannot link to itself."},
            status_code=422)
    except SubmissionClosed:
        return _detail_response(request, submission_id, status_code=409)
    except ConcurrencyConflict:
        return _reshow(
            ["This deal changed since you opened it — reload and re-apply."], [],
            status_code=409)

    if not result.updated:
        return _reshow([], result.warnings)  # non-blocking dup warning
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
