# Contracts: routes and query parameters (spec 017)

Server-rendered HTMX fragments; no JSON API. Every POST carries `csrf_token`
and is rejected without a session (Article 13). Decision references:
[plan.md](plan.md) T-02, T-03, T-05, T-06, T-08.

## 1. Submission page — new POSTs

| Route | Form fields | Gate | Success | Errors |
|---|---|---|---|---|
| `POST /submissions/{sid}/deal-status` | `to_status` ∈ `deal_status_kind.code`, `expected_updated_at` | none on Modeling status (T-02) | re-render the deal-facts band (`#deal-facts`) with the new chip; full-page redirect without HTMX | 409 banner on `updated_at` mismatch; 422 on an unknown code |
| `POST /submissions/{sid}/crm-ids/{tag}/dates` | `inception_date` (ISO or blank), `expiration_date` (ISO or blank) | Modeling status Active | re-render `#crm-tags`; blank clears that override (inherits) | 409 `SubmissionClosed`; 422 on an unparseable date |
| `POST /submissions/{sid}/crm-ids/same-dates` | none | Modeling status Active; `hx-confirm` on the button | re-render `#crm-tags` with every row inherited | 409 `SubmissionClosed` |

Existing `POST /submissions/{sid}/crm-ids` and `…/crm-ids/{tag}/delete` are
unchanged; a removed CRM ID takes its override columns with it (FR-004).

`#crm-tags` renders one row per CRM ID: CRM ID · effective inception ·
effective expiration · actions. An inherited date carries the `crm-date--inherited`
class and the title "Inherited from the deal"; an entered one renders plain.

## 2. Create / edit form — new fields

| Field | Name | Rules |
|---|---|---|
| Expiration date | `expiration_date` | optional ISO date; blank stores NULL |
| Client | `client_id` | optional; options from `client_service.list_clients()` shown as `"ID - name"`, typeahead narrows on ID or name; when the list is `None` the field is disabled with "Client list unavailable — the submission saves without one" and posts nothing |
| Treaty type | `treaty_type_code` | options from `submission_service.treaty_type_kinds()` (eleven rows) |

A posted `client_id` not in a reachable list → 422 field error "Choose a client
from the list." Required fields are unchanged: name, cedant, treaty type,
inception.

## 3. Query parameters shared by the three lists

Parsed by `app/routers/_list_filters.py`. Values OR within a parameter and AND
across parameters. Multi-value parameters repeat the name once per value
(`?crm_id=A&crm_id=B`). Every multi-value parameter caps at 20 values; over
the cap the page returns the one-line banner `"{Label} accepts 20 values or
fewer."` and no rows.

| Param | Type | Label | Applies to | Semantics |
|---|---|---|---|---|
| `q` | text | Name | all three | submissions: word-AND on name; libraries: substring on entity name (unchanged) |
| `cedant` | text | Cedant | all three | word-AND substring on `submission.cedant_name` |
| `crm_id` | multi | CRM ID | all three | each value a substring of any CRM ID of the submission (`LIKE`, escaped); OR across values |
| `owner` | multi | Owner | all three | `assigned_analyst_id IN`; submissions list defaults to the signed-in analyst when absent and `owner=any` clears it (unchanged); libraries have no default |
| `status` | multi | Modeling status | submissions list | `status_code IN` (existing param, relabelled) |
| `modeling_status` | multi | Modeling status | libraries | same predicate; the libraries' `status` param stays the import status |
| `deal_status` | multi | Submission status | all three | `deal_status_code IN` |
| `client` | multi | Client | all three | `client_id IN` (integers); a submission with NULL client never matches |
| `treaty_type` | multi | Treaty type | all three | `treaty_type_code IN` |
| `treaty_year` | multi | Treaty year | all three | `treaty_year IN`, each 1900–2999 |
| `inception` | date | Inception | submissions list | `inception_date =` (unchanged) |
| `in_force` | flag `1` | In force as of | all three | when set, the in-force predicate with `as_of` |
| `as_of` | date | In force as of | all three | defaults to today when `in_force=1` and blank |
| `sort`, `dir`, `page` | — | — | submissions list | unchanged |

Library-only parameters kept as today: `status` (import status, exact match).

On the libraries, when none of `cedant`, `crm_id`, `owner`,
`modeling_status`, `deal_status`, `client`, `treaty_type`, `treaty_year`,
`in_force` is set, no `EXISTS` is added and EDMs/RDMs with no submission are
listed (FR-016).

## 4. Library fragments

`GET /edms/table?…` and `GET /rdms/table?…` accept every parameter in §3 and
render `partials/library_table.html`; the `#lib-live` poll URL is built from
the request's own query string so a filtered view keeps polling filtered.

## 5. Service signatures

```python
# submission_service
WON = "WON"
def submission_filter_clauses(filters: dict, alias: str = "s") -> tuple[list[str], dict]
def treaty_type_kinds() -> list[tuple[str, str]]
def deal_status_kinds() -> list[tuple[str, str]]
def set_deal_status(*, submission_id, to_status, expected_updated_at, actor_id) -> None
def set_crm_dates(*, crm_tag_id, inception_date, expiration_date, actor_id) -> None
def reset_crm_dates(*, submission_id, actor_id) -> None
def list_crm_ids(submission_id) -> list[CrmTag]          # now carries effective dates + inherited flags

# edm_service / rdm_service
def list_edms(*, name=None, status=None, submission_filters: dict | None = None) -> list[EdmRow]
def list_rdms(*, name=None, status=None, submission_filters: dict | None = None) -> list[RdmRow]

# client_service (new; every function fails open)
@dataclass(frozen=True)
class Client: id: int; name: str | None
def list_clients() -> list[Client] | None
def client_names(ids: Iterable[int]) -> dict[int, str]
def display(client_id: int | None, name: str | None) -> str | None
```

`filters` keys (all optional): `owner_ids`, `name`, `cedant_name`, `crm_ids`,
`client_ids`, `treaty_type_codes`, `treaty_years`, `inception_date`,
`status_codes` (Modeling), `deal_status_codes`, `in_force_as_of` (a `date`,
present only when the flag is set).

## 6. Extract

`SELECT * FROM v_submission_crm_id` — columns in
[data-model.md §4](../data-model.md#4-v_submission_crm_id--view-t-04). One row
per CRM ID; a submission with none emits one row with `crm_id` NULL.
