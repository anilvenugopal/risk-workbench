# HTTP Routes: GeoHaz Execution

All routes live in `app/routers/edms.py` (they resolve to the existing
`irp.edm_library` nav key — no nav-manifest change). All are authenticated; the
POST is CSRF-validated. No route touches Risk Modeler (Article 11).

## `POST /edms/{edm_id}/geohaz` — launch

- Form fields: `csrf_token`, `portfolio_ids` (repeated), and optional
  `submission_id`. The submission-contextual EDM page supplies `submission_id`;
  the direct EDM library page omits it.
- The route supplies the parameter set: the configured data version
  (`settings.hazard_data_version`), DLM, earthquake + windstorm,
  `skip_prev_hazard=false`, and `override_user_def=true`.
- Validates: gate (EDM exists, ≥1 portfolio — FR-004), every `portfolio_ids`
  belongs to this EDM and is P-06-eligible.
- On success: enqueues one `run_geohaz` rwb_job per portfolio (one shared
  `request_params` document — FR-003) + dispatch, then re-renders the
  portfolios section fragment (HTMX) / PRG (no-JS). Each launched portfolio's
  cell immediately shows **SUBMITTING**.
- On validation failure: nothing is enqueued. HTMX receives the refreshed EDM
  body and an error toast; no-JavaScript requests redirect to the EDM page with
  an error banner. A P-06-ineligible selection is rejected, not silently skipped.
- When `submission_id` is supplied, the route validates the `submission_edm`
  association before enqueueing, then returns the submission-contextual body
  fragment (HTMX) or redirects to the submission-contextual EDM page (no-JS).
  The direct EDM library response remains unchanged when the field is absent.

## `GET /edms/{edm_id}/portfolios/{portfolio_id}/geohaz-cell` — status cell fragment

- Renders `partials/geohaz_cell.html`, which carries three elements: the
  status cell itself, an `hx-swap-oob` checkbox (only when the portfolio is
  not live — keeps the row's selection checkbox in sync with eligibility),
  and an `hx-swap-oob` most-recent-lookup details section.
- The status cell shows the in-line status for a non-terminal job or the
  stored raw `hazardVersion` otherwise (data-model §4).
- Self-terminating poll: the fragment carries
  `hx-get … hx-trigger="every 3s" hx-target="this" hx-swap="outerHTML"` **only
  while** the portfolio has a non-terminal lookup; on terminal render the
  attributes are omitted and polling stops (FR-012, T-01).
- 200 with a terminal cell rendering an em-dash (`<span class="na">—</span>`,
  no `hx-*` attributes) if the portfolio is gone — the swap ends the poll
  instead of leaving it running against a 404.

## Existing routes touched

- `GET /edms/{edm_id}` / `GET /edms/{edm_id}/body` — the portfolios table gains
  the selection checkboxes, the one-click launch button (disabled until ≥1 eligible
  portfolio is checked; absent when the gate fails — FR-004), the new column
  (update `--cols`/`min-width` together), and the expanded-row latest-details column.
  The whole-body poll contract (204 mid-sync guard) is unchanged — geohaz
  status rides the per-cell fragment, not the body poll.
