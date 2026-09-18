"""Query-parameter parsing shared by the submissions list and the EDM and RDM
libraries (spec 017 T-08; contracts/routes.md §3).

Each text parameter is capped by length and word count, each multi-value
parameter at ``MAX_FILTER_VALUES`` values, and every refusal is one line naming
the filter. Eight multi-value filters at twenty values is 160 bound parameters,
under SQL Server's 2,100-parameter limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services import auth_service, client_service, submission_service

SEARCH_MAX_CHARACTERS = 100
SEARCH_MAX_WORDS = 10
MAX_FILTER_VALUES = 20
MIN_TREATY_YEAR, MAX_TREATY_YEAR = 1900, 2999

# query parameter -> (label, key in the ``filters`` dict of contracts §5)
TEXT_FILTERS = {
    "q": ("Name", "name"),
    "cedant": ("Cedant", "cedant_name"),
}
MULTI_FILTERS = {
    "crm_id": ("CRM ID", "crm_ids"),
    "owner": ("Owner", "owner_ids"),
    "status": ("Modeling status", "status_codes"),
    "deal_status": ("Submission status", "deal_status_codes"),
    "treaty_type": ("Treaty type", "treaty_type_codes"),
    "treaty_year": ("Treaty year", "treaty_years"),
    "client": ("Client", "client_ids"),
}
# The submission-attribute filters the EDM and RDM libraries carry (FR-015,
# P-13): no Modeling status, and no owner default.
LIBRARY_MULTI_PARAMS = ("owner", "client", "treaty_type", "treaty_year", "crm_id",
                        "deal_status")
LIBRARY_TEXT_PARAMS = ("cedant",)


@dataclass
class ListFilters:
    filters: dict[str, Any]
    error: str | None
    # The trimmed request values, for echoing back into the form's inputs.
    text: dict[str, str]
    multi: dict[str, list[str]]
    in_force: bool = False
    as_of: str = ""


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _validation_error(text: dict[str, str], multi: dict[str, list[str]]) -> str | None:
    for key, value in text.items():
        label = TEXT_FILTERS[key][0]
        if len(value) > SEARCH_MAX_CHARACTERS:
            return f"{label} must be {SEARCH_MAX_CHARACTERS} characters or fewer."
        if len(value.split()) > SEARCH_MAX_WORDS:
            return f"{label} must contain {SEARCH_MAX_WORDS} words or fewer."
    for key, values in multi.items():
        if len(values) > MAX_FILTER_VALUES:
            return f"{MULTI_FILTERS[key][0]} accepts {MAX_FILTER_VALUES} values or fewer."
    for value in multi.get("treaty_year", []):
        year = _parse_int(value)
        if year is None or not MIN_TREATY_YEAR <= year <= MAX_TREATY_YEAR:
            return (f"Treaty year must be a year between {MIN_TREATY_YEAR} and "
                    f"{MAX_TREATY_YEAR}.")
    return None


def _coerce(key: str, values: list[str]) -> list[Any]:
    if key == "treaty_year":
        return [int(value) for value in values]
    return values


def parse_list_filters(query_params, *, multi_keys, text_keys) -> ListFilters:
    """Read the named parameters off ``query_params`` (a Starlette ``QueryParams``),
    validate them, and return the ``filters`` dict the services take plus the
    one-line message when a filter is unusable. Each multi-value parameter
    repeats its name once per value; blanks are dropped. ``in_force=1`` with
    ``as_of`` (today when blank) becomes ``in_force_as_of`` (FR-018). The owner
    default and the inception date stay with the submissions router."""
    text = {key: (query_params.get(key) or "").strip() for key in text_keys}
    multi = {key: [value.strip() for value in query_params.getlist(key) if value.strip()]
             for key in multi_keys}
    error = _validation_error(text, multi)
    filters: dict[str, Any] = {
        TEXT_FILTERS[key][1]: value or None for key, value in text.items()}
    for key, values in multi.items():
        filters[MULTI_FILTERS[key][1]] = _coerce(key, values) if error is None else []
    in_force = query_params.get("in_force") == "1"
    as_of = (query_params.get("as_of") or "").strip()
    filters["in_force_as_of"] = None
    if in_force:
        if not as_of:
            as_of = date.today().isoformat()
        try:
            filters["in_force_as_of"] = date.fromisoformat(as_of)
        except ValueError:
            error = error or "In force as of must be a date."
    return ListFilters(filters=filters, error=error, text=text, multi=multi,
                       in_force=in_force, as_of=as_of)


def picker_options() -> dict[str, Any]:
    """The option lists behind the shared filter pickers: owners, treaty
    types, Submission statuses and repository clients (``None`` when the
    repository is unreachable, so the picker renders disabled)."""
    clients = client_service.list_clients()
    return {
        "owner_options": [(analyst["id"], analyst["display_name"])
                          for analyst in auth_service.list_active_analysts()],
        "treaty_types": submission_service.treaty_type_kinds(),
        "deal_statuses": submission_service.deal_status_kinds(),
        "client_options": (None if clients is None
                           else [(client.id, client.label) for client in clients]),
        "min_treaty_year": MIN_TREATY_YEAR,
        "max_treaty_year": MAX_TREATY_YEAR,
    }


def library_filters(request) -> tuple[ListFilters, dict[str, Any]]:
    """The EDM and RDM libraries' submission-attribute filters (contracts §3)
    and the template context their filter bar reads: the echoed values, the
    picker options, the one-line message and the request's own query string
    for the ``#lib-live`` poll URL."""
    parsed = parse_list_filters(
        request.query_params, multi_keys=LIBRARY_MULTI_PARAMS,
        text_keys=LIBRARY_TEXT_PARAMS)
    filter_values = {
        "q": request.query_params.get("q", ""),
        "status": request.query_params.get("status", ""),
        **parsed.text, **parsed.multi,
        "in_force": parsed.in_force, "as_of": parsed.as_of or date.today().isoformat(),
    }
    return parsed, {
        "filter_values": filter_values,
        "validation_error": parsed.error,
        "is_filtered": bool(filter_values["q"] or filter_values["status"]
                            or submission_service.has_submission_filters(parsed.filters)),
        "query_string": request.url.query,
        **picker_options(),
    }
