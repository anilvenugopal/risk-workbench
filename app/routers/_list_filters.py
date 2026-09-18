"""Query-parameter parsing shared by the submissions list and the EDM and RDM
libraries (spec 017 T-08; contracts/routes.md §3).

Each text parameter is capped by length and word count, each multi-value
parameter at ``MAX_FILTER_VALUES`` values, and every refusal is one line naming
the filter. Eight multi-value filters at twenty values is 160 bound parameters,
under SQL Server's 2,100-parameter limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SEARCH_MAX_CHARACTERS = 100
SEARCH_MAX_WORDS = 10
MAX_FILTER_VALUES = 20
MIN_TREATY_YEAR, MAX_TREATY_YEAR = 1900, 2999

# query parameter -> (label, key in the ``filters`` dict of contracts §5)
TEXT_FILTERS = {
    "q": ("Name", "name"),
    "cedant": ("Cedant", "cedant_name"),
    "crm_id": ("CRM ID", "crm_id"),
}
MULTI_FILTERS = {
    "owner": ("Owner", "owner_ids"),
    "status": ("Modeling status", "status_codes"),
    "deal_status": ("Submission status", "deal_status_codes"),
    "treaty_type": ("Treaty type", "treaty_type_codes"),
    "treaty_year": ("Treaty year", "treaty_years"),
}


@dataclass
class ListFilters:
    filters: dict[str, Any]
    error: str | None
    # The trimmed request values, for echoing back into the form's inputs.
    text: dict[str, str]
    multi: dict[str, list[str]]


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
    repeats its name once per value; blanks are dropped. Owner defaults and the
    date parameters stay with the router that owns them."""
    text = {key: (query_params.get(key) or "").strip() for key in text_keys}
    multi = {key: [value.strip() for value in query_params.getlist(key) if value.strip()]
             for key in multi_keys}
    error = _validation_error(text, multi)
    filters: dict[str, Any] = {
        TEXT_FILTERS[key][1]: value or None for key, value in text.items()}
    for key, values in multi.items():
        filters[MULTI_FILTERS[key][1]] = _coerce(key, values) if error is None else []
    return ListFilters(filters=filters, error=error, text=text, multi=multi)
