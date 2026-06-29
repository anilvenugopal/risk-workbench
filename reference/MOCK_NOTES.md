# Mock notes — for Claude Code

This is a **clickable mock**, not the application. The frontend (CSS, HTMX,
Alpine, manifest-driven shell, interaction patterns) is production-faithful and
should be carried forward. The backend is a stdlib stand-in: in-memory data, no
DB, no auth, no file reads, no IRP. The `/api/*` handlers in `serve.py` are a
**first sketch of the API contract** — adapt them, don't ship them.

## Run it

```bash
cd mock
python serve.py        # -> http://localhost:8000
```

No dependencies. Start at `/` (Home). Try: open a submission (master-detail +
inventory + discrepancies), tag a file, open a workflow and click "Run workflow"
(live stage monitor), press Ctrl/Cmd-J for global search, visit `/signin`.

## What is REAL (keep this)

- The four ITCSS CSS files under `static/css/` are copied verbatim from
  `docintel/ui/src/styles`. `app.css` imports them in ITCSS order then adds mock
  layout/shell helpers. Re-copy the four to pull design updates.
- `nav_manifest.py` is the **navigation manifest** — the single source of truth
  the real app keeps in code. Rail, sidebar, breadcrumb, active-state, and the
  search "Applications" group all derive from it. This pattern is the keystone;
  keep it.
- Breadcrumb/active-state are computed from the manifest node for the current
  URL (not from history) — which is why opening a workflow from a submission
  still shows Workflows context. Keep this rule.
- HTMX swap patterns, `hx-push-url` intent, the self-terminating poll monitor,
  the Alpine search modal, and the icon set (`static/icons`, inlined with
  `currentColor`) are all as the real app should do them.

## What is FAKED (replace this)

| Faked here | Real app |
|---|---|
| In-memory dicts in `mock_data.py` | SQL Server via repository layer + `apply_scope()` |
| No auth; `SESSION` is a constant | Entra OIDC + backdoor; signed-cookie session |
| No RLS | `apply_scope()` on every query; admin bypass |
| Inventory is static fixtures | Read-only shared-drive scan; immutable `file_artifact` |
| Workflow "run" advances on a timer | Task = job row; worker submits to IRP; status mirrored |
| IRP not contacted | IRP interface (submit/poll/validate/resolve-family) |
| Search is substring over fixtures | Search-provider registry over scoped SQL |
| `serve.py` stdlib router | FastAPI + Jinja; these handlers map ~1:1 to routes |

## API contract sketch (from `serve.py`)

These are the request/response shapes the real FastAPI endpoints should mirror.
All responses are HTML fragments unless noted.

```
GET  /                                  -> page: Home (no sidebar)
GET  /submissions                       -> page: Submissions (master-detail)
GET  /submissions/{id}                  -> page: Submissions, detail preselected
GET  /workflows                         -> page: Workflows (master-detail)
GET  /workflows/{id}                    -> page: Workflow detail (stage monitor)
GET  /results | /templates | /irp | /admin -> section pages (shells this pass)
GET  /signin                            -> sign-in page (SSO + backdoor)

GET  /api/submissions?customer=&program=        -> <tr> rows (filtered, scoped)
GET  /api/submissions/{id}                       -> detail panel (inventory + workflows)
GET  /api/submissions/{id}/inventory             -> inventory table fragment
POST /api/submissions/{id}/inventory/refresh     -> inventory table fragment (re-scan)
POST /api/inventory/{artifactId}/tag?tag=&sub=   -> single updated <tr> (outerHTML swap)
GET  /api/workflows                              -> <tr> rows
GET  /api/workflows/{id}/peek                    -> small detail panel
GET  /api/workflows/{id}/stages                  -> stage list; carries the poll
                                                    trigger only while running
POST /api/workflows/{id}/run                     -> stage list (now running)
POST /api/workflows/{id}/stages/{i}/review/complete -> stage list (stage -> complete, run resumes)
POST /api/workflows/{id}/stages/{i}/review/cancel   -> stage list (workflow halted -> canceled)
POST /api/workflows/{id}/stages/{i}/autocomplete    -> stage card (toggle, value=0|1)
GET  /workflows/review                              -> Review queue page (active gates only)
POST /api/workflows                              -> create workflow (form) -> 204 + HX-Redirect
GET  /api/workflows/new/inputs?sub=              -> <option>s of EDM-tagged inputs
POST /api/workflows/{id}/stages/{i}/tasks        -> new task block + OOB count (add)
POST /api/workflows/{id}/stages/{i}/tasks/{t}/save   -> updated task block (edit)
POST /api/workflows/{id}/stages/{i}/tasks/{t}/delete -> empty + OOB count (remove)
GET  /api/search?q=                              -> grouped results (Applications/
                                                    Submissions/Workflows/Templates)
POST /api/signin                                 -> 204 + HX-Redirect: /
```

Contract conventions worth preserving:
- A detail/list endpoint returns the *fragment*, and the caller decides the
  target/swap (`hx-target`, `hx-swap`) — endpoints don't assume placement.
- The monitor endpoint returns markup that **includes its own poll trigger only
  while non-terminal**, so polling stops itself. No server push needed.
- Mutations return the updated unit (a row), swapped `outerHTML` in place; use
  `hx-swap-oob` when one action must update a second region (e.g. a sidebar dot).

## Stage review model (generic — no HITL stage type)

Every stage carries an **execution status** — `not_started → blocked → running → review →
complete | canceled` — plus a per-instance **`auto_complete`** toggle (default **false**) the
user sets at compose time. When a stage's tasks finish: `auto_complete=true` ⇒ the stage goes
`complete` and the run proceeds; `auto_complete=false` ⇒ it **parks in `review`** until a human
acts. **`ERROR` is a dynamic rollup** (any task failed) that overlays any status, so a stage can
read "complete · with errors" — error is shown but is **never a gate**. **`blocked`** is a gate
that carries a validation message (the slot exists; the checks are future). **Review/Cancel**:
Complete advances the workflow (complete-with-errors stays complete); Cancel halts the whole
workflow (`status → canceled`). No retry/rerun.

The mock simulates this with a cursor model in `mock_data.py` (`advance` / `review_decide`);
the real worker would set stage status and stop/resume the queue. Active gates (`review` +
`blocked`) surface on: the home **Review** card (count), the Workflows **Review** sidebar item +
queue page (`/workflows/review`), the workflows-table indicator (REVIEW/BLOCKED/ERROR), and the
workflow-detail per-stage **review panel**. **Composition is per-stage**: a stage in
`not_started` is editable (task add/remove/edit + the `auto_complete` toggle), even while other
stages run; it locks once it leaves `not_started`.

## Out of scope this pass (by request)

- **Workflow authoring / validation editor** — the type-port wiring UI, two-phase
  validation, and `draft -> validated -> runnable` transitions. The mock shows
  the *monitoring* detail, not the authoring flow. Add next.
- Results/Templates/IRP/Admin internals (shells only).
- Real session timeout, CSRF, audit log.

## File map

```
serve.py          router + manifest-driven page rendering + /api handlers
nav_manifest.py   the navigation manifest (keystone) + resolution helpers
mock_data.py      fixtures + run simulation + search
templates/        shell.html + page mains (home, submissions, workflows, signin)
static/css/       4 real ITCSS files + app.css
static/js/        htmx.min.js, alpine.min.js (self-hosted), app.js (search modal)
static/icons/     8 rail icons (currentColor SVG)
```


## Review model endpoints
- POST /api/workflows/{id}/stages/{i}/review/complete|cancel
- POST /api/workflows/{id}/stages/{i}/autocomplete (value=0|1)
- GET /workflows/review (active gates: review+blocked only)


## Caveats
- The mock uses ThreadingHTTPServer with in-memory module state; it is **not thread-safe** (single-user demo only). The real app is the DB + transactions.
- Status here is mutated in place; the real design is **event-sourced** (append-only `*_event` tables + a cached current-status column), with separate composition/execution streams for stages and tasks.
