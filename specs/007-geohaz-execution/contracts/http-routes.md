# HTTP Routes: GeoHaz Execution

All routes live in `app/routers/edms.py` (they resolve to the existing
`irp.edm_library` nav key — no nav-manifest change). All are authenticated; the
POST is CSRF-validated. No route touches Risk Modeler (Article 11).

## `GET /edms/{edm_id}/geohaz/new` — launch-form modal fragment

- Query: `portfolio_ids` (repeated) — the checked portfolios, included by the
  launch button's `hx-get` from the selection form.
- Renders `partials/geohaz_modal.html` into `#geohaz-modal-mount`
  (the `package_modal.html` pattern; Alpine component registered in `app.js`).
- Pre-populated defaults (FR-002): data version = first entry of
  `GEOHAZ_DATA_VERSIONS`; model family = DLM (HD disabled, O7-1); perils =
  earthquake + windstorm checked; skip locations with previous hazard lookup
  unchecked; overwrite user-defined hazard values checked.
- Error variants (rendered in the modal, not error pages): no portfolios
  selected; a selected portfolio is P-06-ineligible (lists which); gate not met.

## `POST /edms/{edm_id}/geohaz` — launch

- Form fields: `csrf_token`, `portfolio_ids` (repeated), `data_version`,
  `perils` (repeated; ≥1 required), `skip_prev_hazard` and
  `override_user_def` (checkbox booleans; omitted means false).
- Validates: gate (EDM exists, ≥1 portfolio — FR-004), every `portfolio_ids`
  belongs to this EDM and is P-06-eligible, ≥1 peril (FR-002), `data_version`
  is a configured value.
- On success: enqueues one `run_geohaz` rwb_job per portfolio (one shared
  `request_params` document — FR-003) + dispatch, then re-renders the
  portfolios section fragment (HTMX) / PRG (no-JS). Each launched portfolio's
  cell immediately shows **Queued**.
- On validation failure: 422/409 re-render of the modal with the error; nothing
  is enqueued (the launch is validated as a whole — a P-06-ineligible selection
  is rejected, not silently skipped).

## `GET /edms/{edm_id}/portfolios/{portfolio_id}/geohaz-cell` — status cell fragment

- Renders `partials/geohaz_cell.html`: the four-state column value
  (No / in-line status / Yes / Failed — data-model §4).
- Self-terminating poll: the fragment carries
  `hx-get … hx-trigger="every 3s" hx-target="this" hx-swap="outerHTML"` **only
  while** the portfolio has a non-terminal lookup; on terminal render the
  attributes are omitted and polling stops (FR-012, T-01).
- 404 if the portfolio is gone (soft-deleted) — renders a terminal empty cell,
  never an error page.

## Existing routes touched

- `GET /edms/{edm_id}` / `GET /edms/{edm_id}/body` — the portfolios table gains
  the selection checkboxes, the launch button (disabled until ≥1 eligible
  portfolio is checked; absent when the gate fails — FR-004), the new column
  (update `--cols`/`min-width` together), and the expanded-row latest-details column.
  The whole-body poll contract (204 mid-sync guard) is unchanged — geohaz
  status rides the per-cell fragment, not the body poll.
