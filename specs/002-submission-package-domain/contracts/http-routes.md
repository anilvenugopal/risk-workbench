# Contract — HTTP Routes (Submissions)

Server-rendered FastAPI + Jinja2 + HTMX (Article 8). All routes require an authenticated session (existing `SessionMiddleware`). Every **state-changing** route requires a valid CSRF token (Article 13). No route applies row-level access — every authenticated analyst may load and act on every submission (Article 6 / FR-019). Function-level role gating (if any admin-only maintenance is added) is checked server-side; the submission CRUD below is available to any authenticated analyst.

Response convention: full-page GETs return the shell-embedded page (`hx-boost` handles nav); HTMX POST/PATCH return the affected **partial** (row, tag set, detail pane, or warning fragment). JS-off falls back to full-page redirects.

---

## List & detail

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/submissions` | Master-detail list — **default "All"** or last view | Nav `submissions.all`. Filters via query string. |
| GET | `/submissions/mine` | List filtered to `assigned_analyst_id = current_user` | Nav `submissions.mine`; the **default** landing per FR-020. |
| GET | `/submissions/{id}` | Detail view — attributes, status + history, CRM tags, packages | Real URL (Article 8). 404 if unknown id. Visible to any analyst (FR-019). |

**List query params** (all optional, combine with AND — FR-021/R7/R10): `cedant`, `treaty_type`, `inception` (ISO date), `treaty_year`. The "My/All" distinction is the route (`/submissions/mine` vs `/submissions`), i.e. a plain owner predicate, not a scope wrapper.

| Method | Path | Purpose |
|---|---|---|
| GET | `/submissions/cedant-suggest?cedant_name=` | HTMX cedant typeahead for the create/edit form (`DISTINCT cedant_name`, contains match); returns `partials/typeahead_menu.html` (FR-006/R6). htmx sends the field under its own name; `?q=` is still accepted. Both suggest routes render an empty body below a 2-character term and cap the menu at 10 rows in the query |
| GET | `/submissions/link-suggest?links_to_search=&links_to_exclude=` | HTMX typeahead for the "links to" picker; AND-combines terms across name and cedant, drops `links_to_exclude` from the results, returns `partials/typeahead_menu.html` (FR-007/CR8). `?q=`/`?exclude=` are still accepted |

---

## Create & edit (gated + concurrency-checked)

| Method | Path | Purpose | Success | Errors |
|---|---|---|---|---|
| GET | `/submissions/new` | Create form | — | — |
| POST | `/submissions` | Create submission | 303 → `/submissions/{id}` (or detail partial) | dup-warn partial (unconfirmed match, FR-004); 422 validation |
| GET | `/submissions/{id}/edit` | Edit form (carries `updated_at`) | — | 409 gate if not ACTIVE |
| POST | `/submissions/{id}` | Update fields | detail partial | `SubmissionClosed`→409/banner; `ConcurrencyConflict`→409 banner (input preserved); dup-warn partial; `SelfLinkError`→422 |
| POST | `/submissions/{id}/reassign` | Reassign owner (any analyst, FR-005a) | detail/row partial (leaves My view) | gate 409; concurrency 409 |

**Duplicate-warning flow (FR-004 / R4):** POST create/update carries `confirmed` (hidden field, default absent). If `find_similar` returns matches and `confirmed` is not set, the response is the **non-blocking** `dup_warning` partial listing look-alikes with a "Create/Save anyway" control that re-POSTs with `confirmed=1`. It never hard-rejects and never mangles the name.

**Optimistic concurrency (FR-031 / R1):** edit/reassign/status forms carry the `updated_at` they read; a mismatch (`rowcount 0`) returns a 409 "this deal changed — reload" banner without overwriting.

---

## Status lifecycle (event-sourced)

| Method | Path | Purpose | Notes |
|---|---|---|---|
| POST | `/submissions/{id}/status` | Set status to ACTIVE / COMPLETED / CANCELLED | Body: `to_status`, optional `reason`, `updated_at`. No precondition (FR-012). Reopen (→ACTIVE) allowed from COMPLETED **and** CANCELLED (FR-011). Returns status chip + history partial. |
| — | *(no delete route)* | — | There is **no** delete endpoint anywhere (FR-014 / SC-005). |

Once COMPLETED/CANCELLED, the detail view renders **read-only**: edit/reassign/CRM controls are hidden and their routes reject with `SubmissionClosed` (server-authoritative gate, R3). The only state-changing action offered is **Reopen** (FR-015/SC-012).

---

## CRM tags (gated)

| Method | Path | Purpose | Notes |
|---|---|---|---|
| POST | `/submissions/{id}/crm-ids` | Add a CRM tag | blank/whitespace rejected; no format validation (FR-018); re-adding a tag the deal already carries is a case-insensitive no-op returning the existing tag id |
| POST | `/submissions/{id}/crm-ids/{tag_id}/delete` | Remove a CRM tag | returns tag-set partial |

Both reject with `SubmissionClosed` (409) unless the submission is ACTIVE (FR-017/FR-015). Zero tags is a valid state (FR-016).

FR-017's *edit* is served by remove + add: tags render as read-only chips, so there is no in-place edit route (issue #16). A dedicated `POST /submissions/{id}/crm-ids/{tag_id}` was removed along with `edit_crm_id`.

---

## Packages (structure only — mostly deferred)

Per FR-028, **no analyst-facing package UI is built this iteration.** The submission-detail view may *read-only list* any attached packages (via `package_service.get_packages_for_submission`) as a placeholder, but there are **no** package create/sync/delete routes, no shared-drive browse, and no package cards — those are Iteration 2. Package structure is exercised this iteration through the `package_service` data-access functions and their tests, not HTTP.

---

## Cross-cutting

- **CSRF** on every POST (Article 13); token from the existing `app/auth/csrf.py`.
- **Nav manifest** (Article 1): the `submissions` rail + `submissions.mine`/`submissions.all` nodes exist; add a `submissions.detail` node (parameterized) so breadcrumb/active-state derive from position, not history.
- **HTMX idle-timeout**: inherits the Iteration-0 `HX-Redirect` handling on session expiry.
- **No IRP calls** on any of these routes (Article 11 N/A this iteration).
