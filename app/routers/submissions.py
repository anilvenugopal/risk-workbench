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

from dataclasses import replace
from datetime import date
from functools import partial
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.csrf import validate_csrf_token
from app.nav import get_nav_context
from app.services import (
    edm_service,
    entity_note_service,
    rdm_service,
    shared_drive,
    submission_service,
)
from app.services.errors import (
    ConcurrencyConflict,
    InvalidMemberName,
    InvalidSourceFile,
    NameCollisionError,
    NoteConflict,
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
    treaty_year: str, directory_path: str,
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

    # The form picks the directory from the drive browser, so a path that no longer
    # resolves means it moved or was deleted between the pick and the save.
    if directory_path.strip():
        try:
            shared_drive.validate_directory(directory_path.strip())
        except InvalidSourceFile:
            errors["directory_path"] = (
                "That folder is no longer on the shared drive — browse and pick it "
                "again.")

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


def _entity_backfill_running(kind: str, entities: list) -> bool:
    latest_status = (
        edm_service.latest_backfill_status
        if kind == "edm"
        else rdm_service.latest_backfill_status
    )
    return any(
        latest_status(entity.id) in ("pending", "running")
        for entity in entities
    )


def _entity_sort_state(request: Request) -> dict[str, tuple[str, bool]]:
    state = {}
    for kind in ("edm", "rdm"):
        sort = request.query_params.get(
            f"{kind}_sort", submission_service.ENTITY_TABLE_DEFAULT_SORT)
        if sort not in submission_service.ENTITY_TABLE_SORTS:
            sort = submission_service.ENTITY_TABLE_DEFAULT_SORT
        direction = request.query_params.get(f"{kind}_dir", "asc")
        state[kind] = (sort, direction == "desc")
    return state


def _entity_sort_query(state: dict[str, tuple[str, bool]]) -> str:
    return urlencode([
        ("edm_sort", state["edm"][0]),
        ("edm_dir", "desc" if state["edm"][1] else "asc"),
        ("rdm_sort", state["rdm"][0]),
        ("rdm_dir", "desc" if state["rdm"][1] else "asc"),
    ])


def _entity_sort_links(
    submission_id: str, kind: str, state: dict[str, tuple[str, bool]],
) -> dict[str, dict]:
    current_sort, current_descending = state[kind]
    links = {}
    for sort in submission_service.ENTITY_TABLE_SORTS:
        active = sort == current_sort
        next_descending = (
            not current_descending if active
            else submission_service.ENTITY_TABLE_SORT_STARTS_DESCENDING[sort]
        )
        next_state = dict(state)
        next_state[kind] = (sort, next_descending)
        query = _entity_sort_query(next_state)
        links[sort] = {
            "href": f"/submissions/{submission_id}?{query}",
            "partial_href": f"/submissions/{submission_id}/{kind}s/table?{query}",
            "active": active,
            "aria": (
                "descending" if current_descending else "ascending"
            ) if active else "none",
            "caret": ("▼" if current_descending else "▲") if active else "",
        }
    return links


def _detail_context(request: Request, submission_id: str) -> dict | None:
    """Assemble the full detail-view context, or None if the id is unknown."""
    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return None
    analysts = _active_analysts()
    sort_state = _entity_sort_state(request)
    edm_sort, edm_descending = sort_state["edm"]
    rdm_sort, rdm_descending = sort_state["rdm"]
    submission_edms = submission_service.list_submission_edms(
        submission_id, sort=edm_sort, descending=edm_descending)
    submission_rdms = submission_service.list_submission_rdms(
        submission_id, sort=rdm_sort, descending=rdm_descending)
    sort_query = _entity_sort_query(sort_state)
    return {
        "submission": submission,
        "status_history": submission_service.get_status_history(submission_id),
        "crm_tags": submission_service.list_crm_ids(submission_id),
        "submission_edms": submission_edms,
        "submission_rdms": submission_rdms,
        "edm_backfill_running": _entity_backfill_running("edm", submission_edms),
        "rdm_backfill_running": _entity_backfill_running("rdm", submission_rdms),
        "edm_sort_links": _entity_sort_links(submission_id, "edm", sort_state),
        "rdm_sort_links": _entity_sort_links(submission_id, "rdm", sort_state),
        "edm_table_url": f"/submissions/{submission_id}/edms/table?{sort_query}",
        "rdm_table_url": f"/submissions/{submission_id}/rdms/table?{sort_query}",
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


def _entity_table_response(
    request: Request, submission_id: str, kind: str, *,
    message: str | None = None, status_code: int = 200,
):
    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return _not_found(request)
    sort_state = _entity_sort_state(request)
    entity_sort, entity_descending = sort_state[kind]
    sort_query = _entity_sort_query(sort_state)
    list_entities = (
        submission_service.list_submission_edms if kind == "edm"
        else submission_service.list_submission_rdms)
    count_field = "portfolio_count" if kind == "edm" else "analysis_count"
    count_label = "Portfolio count" if kind == "edm" else "Analysis count"
    entities = list_entities(
        submission_id, sort=entity_sort, descending=entity_descending)
    return _partial(request, "partials/submission_entity_table.html", {
        "submission": submission,
        "is_active": submission.status_code == submission_service.ACTIVE,
        "entity_message": message,
        "kind": kind,
        "entities": entities,
        "backfill_running": _entity_backfill_running(kind, entities),
        "sort_links": _entity_sort_links(submission_id, kind, sort_state),
        "table_url": f"/submissions/{submission_id}/{kind}s/table?{sort_query}",
        "count_field": count_field,
        "count_label": count_label,
    }, status_code=status_code)


@router.get("/submissions/{submission_id}/edms/table", response_class=HTMLResponse)
def submission_edm_table(request: Request, submission_id: str):
    return _entity_table_response(request, submission_id, "edm")


@router.get("/submissions/{submission_id}/rdms/table", response_class=HTMLResponse)
def submission_rdm_table(request: Request, submission_id: str):
    return _entity_table_response(request, submission_id, "rdm")


def _submission_entity_note_response(
    request: Request, submission_id: str, kind: str, entity_id: str, *,
    notes: str, original_notes: str, csrf_token: str,
):
    if not validate_csrf_token(csrf_token):
        return HTMLResponse("Invalid CSRF token", status_code=403)
    entities = (
        submission_service.list_submission_edms(submission_id)
        if kind == "edm"
        else submission_service.list_submission_rdms(submission_id)
    )
    entity = next((row for row in entities if str(row.id) == entity_id), None)
    if entity is None:
        return HTMLResponse(
            f"That {kind.upper()} is not related to this submission.",
            status_code=404,
        )
    error = None
    conflict = None
    conflict_active = False
    status_code = 200
    try:
        saved_notes = entity_note_service.update_notes(
            kind=kind, entity_id=entity_id, notes=notes,
            original_notes=original_notes, actor_id=request.state.user.id,
        )
    except ValueError as exc:
        error = str(exc)
        status_code = 422
    except NoteConflict as exc:
        conflict = exc.current_note
        conflict_active = True
        original_notes = exc.current_note or ""
        status_code = 409
    if request.headers.get("HX-Request") != "true":
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
    if status_code == 200:
        entity = replace(entity, notes=saved_notes)
    return _partial(request, "partials/submission_entity_note_cell.html", {
        "submission": submission_service.get_submission(submission_id),
        "entity": entity,
        "entity_kind": kind,
        "note_value": notes if status_code != 200 else (entity.notes or ""),
        "note_original": (
            original_notes if status_code != 200 else (entity.notes or "")
        ),
        "note_error": error,
        "note_conflict": conflict,
        "note_conflict_active": conflict_active,
        "note_editing": status_code != 200,
    }, status_code=status_code)


@router.post("/submissions/{submission_id}/edms/{edm_id}/table-notes")
def update_submission_edm_note(
    request: Request, submission_id: str, edm_id: str,
    notes: str = Form(default=""), original_notes: str = Form(default=""),
    csrf_token: str = Form(...),
):
    return _submission_entity_note_response(
        request, submission_id, "edm", edm_id, notes=notes,
        original_notes=original_notes, csrf_token=csrf_token,
    )


@router.post("/submissions/{submission_id}/rdms/{rdm_id}/table-notes")
def update_submission_rdm_note(
    request: Request, submission_id: str, rdm_id: str,
    notes: str = Form(default=""), original_notes: str = Form(default=""),
    csrf_token: str = Form(...),
):
    return _submission_entity_note_response(
        request, submission_id, "rdm", rdm_id, notes=notes,
        original_notes=original_notes, csrf_token=csrf_token,
    )


def _entity_modal_response(
    request: Request, submission_id: str, kind: str, *,
    errors: list[str] | None = None, form: dict | None = None,
    active_tab: str = "import", status_code: int = 200,
):
    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return _not_found(request)
    if submission.status_code != submission_service.ACTIVE:
        return _entity_table_response(
            request, submission_id, kind,
            message="Reopen this submission before changing its data associations.",
            status_code=409)
    candidates = (
        submission_service.list_edm_candidates(submission_id)
        if kind == "edm"
        else submission_service.list_rdm_candidates(submission_id)
    )
    response = _partial(request, "partials/submission_entity_add_modal.html", {
        "submission": submission,
        "entity_kind": kind.upper(),
        "entity_plural": f"{kind}s",
        "kind": kind,
        "candidate_page": candidates,
        "query": "",
        "errors": errors or [],
        "form": form or {"name": ""},
        "active_tab": active_tab,
    }, status_code=status_code)
    if status_code >= 400 and _is_htmx(request):
        response.headers["HX-Retarget"] = "#submission-entity-modal"
        response.headers["HX-Reswap"] = "innerHTML"
    return response


def _not_found(request: Request):
    return _templates(request).TemplateResponse(
        request, "base/error.html",
        {"status_code": 404, "title": "Not found",
         "detail": "That submission does not exist.",
         "is_htmx": _is_htmx(request), "current_user": request.state.user},
        status_code=404,
    )


# ── List + filters ───────────────────────────────────────────────────────────

# The id of the div the filter form and the pager links both target. A request
# naming it gets the table on its own: rebuilding the status list, the analyst
# list and the nav shell for htmx to discard is the cost of a keystroke otherwise.
_LIST_TARGET = "sub-list"
_SEARCH_MAX_CHARACTERS = 100
_SEARCH_MAX_WORDS = 10
# Four filters at this limit plus the text-search parameters stay below SQL
# Server's 2,100-parameter limit.
_MAX_FILTER_VALUES = 400
_MULTI_FILTER_LABELS = {"status": "Status", "treaty_type": "Treaty type",
                        "treaty_year": "Treaty year", "owner": "Owner"}
_TEXT_FILTER_LABELS = {"name": "Name", "cedant_name": "Cedant", "crm_id": "CRM ID"}


def _filter_validation_error(
    text_filters: dict[str, str], multi_values: dict[str, list[str]],
) -> str | None:
    """The one message the list banner shows, or None when every filter is usable."""
    for key, value in text_filters.items():
        if len(value) > _SEARCH_MAX_CHARACTERS:
            return (f"{_TEXT_FILTER_LABELS[key]} must be {_SEARCH_MAX_CHARACTERS} "
                    "characters or fewer.")
        if len(value.split()) > _SEARCH_MAX_WORDS:
            return (f"{_TEXT_FILTER_LABELS[key]} must contain {_SEARCH_MAX_WORDS} "
                    "words or fewer.")
    for key, values in multi_values.items():
        if len(values) > _MAX_FILTER_VALUES:
            return (f"{_MULTI_FILTER_LABELS[key]} accepts {_MAX_FILTER_VALUES} "
                    "values or fewer.")
    for value in multi_values["treaty_year"]:
        year = _parse_int(value)
        if year is None or not MIN_TREATY_YEAR <= year <= MAX_TREATY_YEAR:
            return (f"Treaty year must be a year between {MIN_TREATY_YEAR} and "
                    f"{MAX_TREATY_YEAR}.")
    return None


def _sort_links(sort_query: str, sort: str, descending: bool) -> dict[str, dict]:
    """One link per sortable header cell (D15). Clicking the sorted column flips its
    direction; clicking another starts it in ``SORT_STARTS_DESCENDING``."""
    stem = "/submissions?" + (f"{sort_query}&" if sort_query else "")
    links = {}
    for key in submission_service.SORT_COLUMNS:
        active = key == sort
        next_descending = (not descending if active
                           else submission_service.SORT_STARTS_DESCENDING[key])
        links[key] = {
            "href": f"{stem}sort={key}&dir={'desc' if next_descending else 'asc'}",
            "active": active,
            "aria": ("descending" if descending else "ascending") if active else "none",
            "caret": ("▼" if descending else "▲") if active else "",
        }
    return links


@router.get("/submissions", response_class=HTMLResponse)
def list_submissions_page(request: Request):
    text_filters = {
        "name": (request.query_params.get("q") or "").strip(),
        "cedant_name": (request.query_params.get("cedant") or "").strip(),
        "crm_id": (request.query_params.get("crm_id") or "").strip(),
    }
    # Each multi-select menu writes one input per picked value (D16).
    multi_values = {
        key: [value.strip() for value in request.query_params.getlist(key)
              if value.strip()]
        for key in _MULTI_FILTER_LABELS
    }
    validation_error = _filter_validation_error(text_filters, multi_values)
    # No `owner` at all — a nav click, a bare bookmark — lands the analyst on their
    # own deals (FR-020); `owner=any` asks for every deal.
    owner_ids = ([str(request.state.user.id)]
                 if not request.query_params.getlist("owner")
                 else [] if "any" in multi_values["owner"]
                 else multi_values["owner"])
    filters = {
        **{key: value or None for key, value in text_filters.items()},
        "treaty_type_codes": multi_values["treaty_type"],
        "inception_date": _parse_date(request.query_params.get("inception")),
        "treaty_years": [_parse_int(value)
                         for value in multi_values["treaty_year"]],
        "status_codes": multi_values["status"],
    }
    page = _parse_int(request.query_params.get("page")) or 1
    # A hand-edited ?sort=/&dir= falls back to the default order rather than 422.
    sort = request.query_params.get("sort", "")
    direction = request.query_params.get("dir", "")
    if sort not in submission_service.SORT_COLUMNS:
        sort, direction = submission_service.DEFAULT_SORT, ""
    descending = {"asc": False, "desc": True}.get(
        direction, submission_service.SORT_STARTS_DESCENDING[sort])
    listing = (submission_service.list_submissions(
        owner_ids=owner_ids, page=page, sort=sort, descending=descending, **filters)
        if validation_error is None else None)
    # Echoed back into the inputs so a filtered request re-renders what was typed,
    # and read by the template to tell "nothing matches" from "nothing here yet".
    filter_values = {
        key: request.query_params.get(key, "")
        for key in ("q", "cedant", "crm_id", "inception")
    }
    filter_values |= multi_values
    # The resolved ids, not the raw parameter: on the default landing the hidden
    # input has to hold the analyst's own id so the next request keeps it.
    filter_values["owner"] = owner_ids or ["any"]
    # Pairs, not a dict: urlencode emits one repeated key per picked value.
    query_values = [
        (query_key, filter_values[query_key].strip())
        for query_key, filter_key in (
            ("q", "name"), ("cedant", "cedant_name"), ("crm_id", "crm_id"),
            ("inception", "inception_date"),
        )
        if filters[filter_key] is not None
    ]
    for key in ("treaty_type", "treaty_year", "status", "owner"):
        query_values += [(key, value) for value in filter_values[key]]
    # Lowercased: the id arrives from a query string, `app_user.id` from the driver.
    if ([value.lower() for value in filter_values["owner"]]
            == [str(request.state.user.id).lower()]):
        query_values = [(key, value) for key, value in query_values if key != "owner"]
    # The default order is what a bare /submissions already reads, so it stays out.
    default_sort = submission_service.DEFAULT_SORT
    order_values = (
        [] if (sort, descending) == (
            default_sort, submission_service.SORT_STARTS_DESCENDING[default_sort])
        else [("sort", sort), ("dir", "desc" if descending else "asc")])
    sort_query = urlencode(query_values)
    list_ctx = {
        "rows": listing.rows if listing else [],
        "page": listing.page if listing else page,
        "has_next": listing.has_next if listing else False,
        # The applied filters and order, for the pager links to carry; each link
        # appends its own page number. `owner=any` goes too — dropping it would make
        # page 2 of an every-owner list default back to the analyst's own deals.
        "filter_query": urlencode(query_values + order_values),
        "sort_links": _sort_links(sort_query, sort, descending),
        "is_filtered": bool(owner_ids) or any(filters.values()),
        "validation_error": validation_error,
    }
    if request.headers.get("HX-Target") == _LIST_TARGET:
        response = _partial(request, "partials/submission_list.html", list_ctx)
        canonical_query = query_values + order_values
        if page > 1:
            canonical_query = [*canonical_query, ("page", str(page))]
        response.headers["HX-Push-Url"] = (
            "/submissions" + (f"?{urlencode(canonical_query)}"
                              if canonical_query else "")
        )
        return response
    return _render(request, "pages/submissions.html", "submissions.all", {
        **list_ctx,
        "treaty_types": TREATY_TYPES,
        "statuses": submission_service.status_kinds(),
        "owner_options": [(analyst["id"], analyst["display_name"])
                          for analyst in _active_analysts()],
        "filter_values": filter_values,
        "min_treaty_year": MIN_TREATY_YEAR,
        "max_treaty_year": MAX_TREATY_YEAR,
    }, status_code=422 if validation_error else 200)


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
            treaty_year=treaty_year, directory_path=directory_path,
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


# â”€â”€ Submission EDM/RDM associations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/submissions/{submission_id}/edms/add", response_class=HTMLResponse)
def add_edm_modal(request: Request, submission_id: str):
    return _entity_modal_response(request, submission_id, "edm")


@router.get("/submissions/{submission_id}/rdms/add", response_class=HTMLResponse)
def add_rdm_modal(request: Request, submission_id: str):
    return _entity_modal_response(request, submission_id, "rdm")


def _candidate_response(
    request: Request, submission_id: str, kind: str, query: str, page: int,
):
    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return _not_found(request)
    candidates = (
        submission_service.list_edm_candidates(
            submission_id, query=query, page=page)
        if kind == "edm"
        else submission_service.list_rdm_candidates(
            submission_id, query=query, page=page)
    )
    return _partial(request, "partials/submission_entity_candidates.html", {
        "submission": submission,
        "entity_kind": kind.upper(),
        "entity_plural": f"{kind}s",
        "candidate_page": candidates,
        "query": query,
    })


@router.get("/submissions/{submission_id}/edms/candidates", response_class=HTMLResponse)
def edm_candidates(
    request: Request, submission_id: str, q: str = "", page: int = 1,
):
    return _candidate_response(request, submission_id, "edm", q, page)


@router.get("/submissions/{submission_id}/rdms/candidates", response_class=HTMLResponse)
def rdm_candidates(
    request: Request, submission_id: str, q: str = "", page: int = 1,
):
    return _candidate_response(request, submission_id, "rdm", q, page)


def _import_submission_entity(
    request: Request, submission_id: str, kind: str, name: str,
    source_paths: list[str], csrf_token: str,
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
    submission = submission_service.get_submission(submission_id)
    if submission is None:
        return _not_found(request)
    if submission.status_code != submission_service.ACTIVE:
        return _entity_table_response(
            request, submission_id, kind,
            message="Reopen this submission before importing data.", status_code=409)
    source = source_paths[0] if source_paths else ""
    if not name.strip() or not source:
        return _entity_modal_response(
            request, submission_id, kind,
            errors=["A name and a source file selection are required."],
            form={"name": name}, status_code=422)
    try:
        importer = edm_service.import_edm if kind == "edm" else rdm_service.import_rdm
        result = importer(
            name=name.strip(), source_file_path=source,
            actor_id=request.state.user.id, submission_id=submission_id)
    except (InvalidSourceFile, InvalidMemberName, NameCollisionError) as exc:
        return _entity_modal_response(
            request, submission_id, kind, errors=[str(exc)],
            form={"name": name}, status_code=422)
    except SubmissionClosed:
        return _entity_table_response(
            request, submission_id, kind,
            message="Reopen this submission before importing data.", status_code=409)
    message = f"{kind.upper()} import started."
    if result.collision_unchecked:
        message += " Risk Modeler name availability could not be checked."
    if _is_htmx(request):
        return _entity_table_response(request, submission_id, kind, message=message)
    return RedirectResponse(f"/submissions/{submission_id}", status_code=303)


@router.post("/submissions/{submission_id}/edms/import")
def import_submission_edm(
    request: Request, submission_id: str, name: str = Form(""),
    source_paths: Annotated[list[str] | None, Form()] = None,
    csrf_token: str = Form(...),
):
    return _import_submission_entity(
        request, submission_id, "edm", name, source_paths or [], csrf_token)


@router.post("/submissions/{submission_id}/rdms/import")
def import_submission_rdm(
    request: Request, submission_id: str, name: str = Form(""),
    source_paths: Annotated[list[str] | None, Form()] = None,
    csrf_token: str = Form(...),
):
    return _import_submission_entity(
        request, submission_id, "rdm", name, source_paths or [], csrf_token)


def _attach_submission_entities(
    request: Request, submission_id: str, kind: str,
    entity_ids: list[str], csrf_token: str,
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
    if submission_service.get_submission(submission_id) is None:
        return _not_found(request)
    if not entity_ids:
        return _entity_modal_response(
            request, submission_id, kind,
            errors=[f"Select at least one {kind.upper()} to add."],
            active_tab="existing", status_code=422)
    try:
        result = (
            submission_service.attach_edms(
                submission_id=submission_id, edm_ids=entity_ids,
                actor_id=request.state.user.id)
            if kind == "edm"
            else submission_service.attach_rdms(
                submission_id=submission_id, rdm_ids=entity_ids,
                actor_id=request.state.user.id)
        )
    except SubmissionClosed:
        return _entity_table_response(
            request, submission_id, kind,
            message="Reopen this submission before adding existing data.",
            status_code=409)
    message = None
    if result.stale_ids:
        count = len(result.stale_ids)
        message = (
            f"{count} selected {kind.upper()}{'' if count == 1 else 's'} could not "
            "be added because the selection was no longer available."
        )
    if _is_htmx(request):
        return _entity_table_response(request, submission_id, kind, message=message)
    return RedirectResponse(f"/submissions/{submission_id}", status_code=303)


@router.post("/submissions/{submission_id}/edms/attach")
def attach_submission_edms(
    request: Request, submission_id: str,
    entity_ids: Annotated[list[str] | None, Form()] = None,
    csrf_token: str = Form(...),
):
    return _attach_submission_entities(
        request, submission_id, "edm", entity_ids or [], csrf_token)


@router.post("/submissions/{submission_id}/rdms/attach")
def attach_submission_rdms(
    request: Request, submission_id: str,
    entity_ids: Annotated[list[str] | None, Form()] = None,
    csrf_token: str = Form(...),
):
    return _attach_submission_entities(
        request, submission_id, "rdm", entity_ids or [], csrf_token)


def _detach_submission_entity(
    request: Request, submission_id: str, kind: str,
    entity_id: str, csrf_token: str,
):
    if not validate_csrf_token(csrf_token):
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
    if submission_service.get_submission(submission_id) is None:
        return _not_found(request)
    try:
        if kind == "edm":
            submission_service.detach_edm(
                submission_id=submission_id, edm_id=entity_id,
                actor_id=request.state.user.id)
        else:
            submission_service.detach_rdm(
                submission_id=submission_id, rdm_id=entity_id,
                actor_id=request.state.user.id)
    except SubmissionClosed:
        return _entity_table_response(
            request, submission_id, kind,
            message="Reopen this submission before removing data.", status_code=409)
    if _is_htmx(request):
        return _entity_table_response(request, submission_id, kind)
    return RedirectResponse(f"/submissions/{submission_id}", status_code=303)


@router.post("/submissions/{submission_id}/edms/{edm_id}/detach")
def detach_submission_edm(
    request: Request, submission_id: str, edm_id: str,
    csrf_token: str = Form(...),
):
    return _detach_submission_entity(
        request, submission_id, "edm", edm_id, csrf_token)


@router.post("/submissions/{submission_id}/rdms/{rdm_id}/detach")
def detach_submission_rdm(
    request: Request, submission_id: str, rdm_id: str,
    csrf_token: str = Form(...),
):
    return _detach_submission_entity(
        request, submission_id, "rdm", rdm_id, csrf_token)


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
            treaty_year=treaty_year, directory_path=directory_path,
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
