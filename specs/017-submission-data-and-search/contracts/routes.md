# Contracts: routes and query parameters (spec 017)

Server-rendered HTMX fragments; no JSON API. Every POST carries `csrf_token`
and is rejected without a session (Article 13). Amended 2026-09-21 for the
contract grain. Decision references: [plan.md](plan.md) T-02, T-05, T-09,
T-12, T-14.

## 1. Submission page — POSTs

| Route | Form fields | Gate | Success | Errors |
|---|---|---|---|---|
| `POST /submissions/{sid}/statuses` | `modeling_status` ∈ `submission_status_kind.code`, `reason` (optional), `updated_at` | Modeling status transition rules unchanged | event written, cached column stamped; re-render `#deal-head` | 409 on `updated_at` mismatch; 422 on an unknown code |
| `POST /submissions/{sid}/contracts` | `crm_id` (required), `treaty_type_code` (required, in the list), `inception_date` (ISO, required), `expiration_date` (ISO or blank → inception + 1 year − 1 day), `contract_status_code` (default `IN_PROCESS`) | Modeling status Active | row inserted; re-render `#contracts` | 409 `SubmissionClosed`; 422 with the field named: blank CRM ID, duplicate CRM ID on this submission, CRM ID already a contract on another submission (that submission named and linked), unknown code, unparseable date |
| `POST /submissions/{sid}/contracts/{cid}` | the same fields except status, plus `updated_at` | Modeling status Active | attributes updated in place; re-render `#contracts` | 409 `SubmissionClosed` or stale `updated_at`; 422 as above |
| `POST /submissions/{sid}/contracts/{cid}/status` | `contract_status_code`, `updated_at` | none (P-12) | status updated in place, no event; re-render `#contracts` | 409 stale `updated_at`; 422 unknown code |
| `POST /submissions/{sid}/contracts/{cid}/delete` | none | Modeling status Active | row deleted; re-render `#contracts` | 409 `SubmissionClosed` |
| `POST /submissions/{sid}/dates` | **removed** | | | |
| `POST /submissions/{sid}/crm-ids/…` | **removed** (all three) | | | |

`#contracts` is the contract table on the deal card: a header row and one
row per contract in insertion order — CRM ID · Treaty type · Inception ·
Expiration · Contract status · actions. A row's pencil swaps it for its editor
in place; the Add control opens an empty editor row whose dates are pre-filled
from the last row (FR-005). Contract status is a select live in every
Modeling status; the pencil and Add are hidden when Modeling status is not
Active.

## 2. Create / edit form

| Field | Name | Rules |
|---|---|---|
| Name | `name` | required (unchanged) |
| Cedant | `cedant_name` | required (unchanged) |
| Client ID | `client_id` | optional; options from `client_service.list_clients()` as `"ID - name"`; disabled with "Client list unavailable — the submission saves without one" when the list is `None` |
| Data vintage | `data_vintage` | optional ISO date |
| Treaty year | `treaty_year` | optional; the form fills it from the first contract inception typed; the server fills it from the earliest contract inception when blank |
| Links to, Directory path | unchanged | |
| Contract rows | `contract_crm_id[]`, `contract_treaty_type[]`, `contract_inception[]`, `contract_expiration[]`, `contract_status[]` | repeated names, positionally aligned, zero or more rows; every row needs CRM ID, treaty type and inception; blank expiration filled server-side; blank status is `IN_PROCESS`; a row with every field blank is ignored |

Removed from the form: `treaty_type_code`, `inception_date`,
`expiration_date` at submission level. On the edit form the contract rows are
not shown; contracts are edited on the page (§1).

Validation: the whole save is refused with the row index named when a row's
CRM ID is blank, repeats another row's or an existing contract's on this
submission, or is already a contract on another submission (case-insensitive,
trimmed; the message names that submission and links to it, opening in a new
tab), its treaty type is not in the list, or a date does not parse. A posted `client_id` not in a reachable list → 422
"Choose a client from the list."

## 3. Query parameters shared by the three lists

Parsed by `app/routers/_list_filters.py`. Values OR within a parameter and AND
across parameters. Multi-value parameters repeat the name once per value.
Every multi-value parameter caps at 20 values; over the cap the page returns
`"{Label} accepts 20 values or fewer."` and no rows.

| Param | Type | Label | Applies to | Level | Semantics |
|---|---|---|---|---|---|
| `q` | text | Name | all three | submission / entity | unchanged |
| `cedant` | text | Cedant | all three | submission | word-AND substring on `cedant_name` |
| `owner` | multi | Owner | all three | submission | unchanged defaults |
| `client` | multi | Client ID | all three | submission | `client_id IN`; NULL never matches |
| `treaty_year` | multi | Treaty year | all three | submission | `treaty_year IN` |
| `status` | multi | Modeling status | submissions list | submission | `status_code IN` |
| `crm_id` | multi | CRM ID | all three | contract | `LOWER(TRIM(c.crm_id)) IN (…)` (P-10) |
| `treaty_type` | multi | Treaty type | all three | contract | `c.treaty_type_code IN` |
| `inception` | date | Inception | submissions list | contract | `c.inception_date =` |
| `contract_status` | multi | Contract status | all three | contract | `c.contract_status_code IN` (was `deal_status`) |
| `in_force` + `as_of` | flag + date | In force as of | all three | contract | `c.contract_status_code = :won AND c.inception_date <= :asof AND c.expiration_date >= :asof` |
| `sort`, `dir`, `page` | — | — | submissions list | | `sort=inception` orders on the contract aggregate of data-model.md §5 |

All contract-level clauses of one request share one `EXISTS (SELECT 1 FROM
contract c WHERE c.submission_id = s.id AND …)` (P-18). On the libraries,
when no submission-attribute parameter is set, no `EXISTS` is added and
unlinked EDMs and RDMs are listed (FR-016). Library-only `status` stays the
import status.

## 4. Library fragments

Unchanged: `GET /edms/table?…` and `GET /rdms/table?…` accept every parameter
in §3; the `#lib-live` poll URL carries the request's own query string.

## 5. Export form (FR-011)

`GET /submissions/{sid}/exports/new` renders, beside the fields spec 014
defines: `client_id` pre-selected from `submission.client_id`; `data_vintage`
pre-filled from `submission.data_vintage`; a **Contract** select with one
option per contract (`data-crm-id`, `data-inception`), pre-selected when the
submission has exactly one, blank otherwise. Choosing an option copies its
values into `crm_id` and `treaty_incept` client-side; both stay editable and
`POST` is unchanged from spec 014 (the export records what was posted).

## 6. Service signatures

```python
# submission_service
WON = "WON"
@dataclass(frozen=True)
class Contract: id; submission_id; crm_id; treaty_type_code; treaty_type_label
                inception_date; expiration_date; contract_status_code
                contract_status_label; updated_at
@dataclass(frozen=True)
class ContractInput: crm_id: str; treaty_type_code: str; inception_date: date
                     expiration_date: date | None; contract_status_code: str = "IN_PROCESS"
@dataclass(frozen=True)
class ContractOwner: submission_id: str; name: str
class ContractInvalid(ValueError): index: int | None; owner: ContractOwner | None
    # owner is set when another submission holds the CRM ID; the message then
    # ends "is already a contract on" and the template appends the linked name

def create_submission(*, …, data_vintage=None, contracts: list[ContractInput] = (), …) -> str
def update_submission(*, …, data_vintage=None, …) -> None        # no contract fields
def list_contracts(submission_id) -> list[Contract]
def add_contract(*, submission_id, contract: ContractInput, actor_id) -> str
def update_contract(*, contract_id, contract: ContractInput, expected_updated_at, actor_id) -> None
def set_contract_status(*, contract_id, to_status, expected_updated_at, actor_id) -> None
def remove_contract(*, contract_id, actor_id) -> None
def set_statuses(*, submission_id, modeling_status, reason=None, expected_updated_at, actor_id) -> None
def submission_filter_clauses(filters: dict, alias: str = "s") -> tuple[list[str], dict]
    # returns the submission-level clauses; the contract-level clauses arrive
    # already wrapped as one EXISTS string in the same list
def treaty_type_kinds() -> list[tuple[str, str]]
def contract_status_kinds() -> list[tuple[str, str]]
```

Deleted: `CrmTag`, `add_crm_id`, `remove_crm_id`, `list_crm_ids`,
`set_crm_dates`, `reset_crm_dates`, `deal_status_kinds`,
`export_service.list_clients`.

`filters` keys (all optional): `owner_ids`, `name`, `cedant_name`,
`client_ids`, `treaty_years`, `status_codes` (Modeling), `crm_ids`,
`treaty_type_codes`, `inception_date`, `contract_status_codes`,
`in_force_as_of`.

## 7. Extract

`SELECT * FROM v_contract` — columns in
[data-model.md §4](../data-model.md#4-v_contract--view-the-fr-013-extract-t-04).
One row per contract; a submission with no contract emits no row.
