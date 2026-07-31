# Contract: HTTP routes & UI — breakout modal, confirm, lineage display (R8)

**Module**: `app/routers/portfolios.py` (NEW; nav key `irp.edm_library` — no new nav node, Article 1)

Conventions inherited wholesale: `_render`/`_partial` helpers, `validate_csrf_token` on POST (HTMX CSRF failure → `204 + HX-Refresh`), gate refusal → **409 + re-rendered fragment** (the `packages.py` precedent), toasts via `HX-Trigger: {"rwb:toast": …}`, errors via `.form-banner--error` in fragments.

---

## Routes

### `GET /edms/{edm_id}/portfolios/{portfolio_id}/breakout` — modal fragment

- Auth'd fragment; calls `breakout_service.evaluate_gate`.
- Renders `partials/breakout_modal.html` (Alpine open/close — the `package_modal.html` precedent; injected `hx-swap`-style into the page, self-removing on completion):
  - **Dimension chooser**: LOB / Geography (state) — each option enabled per `DimensionEligibility`; ineligible options rendered disabled with their `reason` (missing summary variant links the existing per-EDM **Sync** control).
  - **Slice-list preview** (per selected dimension): rows of *value → generated name* from `build_slice_plan`, slice count, `exists` rows marked "already created" (idempotent re-run view).
  - **Disclosures (FR-007, static copy)**: (a) account-bucketing overlap — geography variant explicitly: *"An account with locations in more than one state is included in full in every matching state slice; slices can overlap and may sum to more than the source."* (b) blank/unassigned values: *"Exposure with no {LOB/state} value is not included in any slice."*
  - **Confirm button** → the POST below. Nothing is created before confirm (Article 5).
  - `in_flight` state: chooser replaced by "a {dimension} breakout is already running" + live indicator.
- Portfolio/EDM missing or deleted → 404 fragment (graceful, non-erroring page).

### `POST /edms/{edm_id}/portfolios/{portfolio_id}/breakout` — confirm

- Form fields: `dimension` (`lob`|`state`), `csrf_token`. HTMX + no-JS PRG both supported (the `sync()` route precedent).
- Flow: `validate_csrf_token` → `breakout_service.request_breakout` (server-side gate re-check **+ the FR-002a summary-freshness check** — current RM `stampDate` via `search_portfolios` must equal the stamp captured at backfill).
  - Success → returns the EDM **body partial** (`_body_partial` reuse) with the breakout-in-flight indicator; `HX-Trigger` toast *"Breakout started — N slices"*. The page's existing 3-second self-poll takes over.
  - Gate refusal → **409** + modal fragment re-rendered with reasons.
  - Stale summary (stamp mismatch / no stored stamp / freshness unverifiable) → 409 variant: *"Portfolio data has changed in Risk Modeler since the last sync — Sync the EDM, then retry."* **No `rwb_job` row is created.**
  - Already running → 409 variant with "already running" banner (idempotent enqueue returned `None`).
- The freshness read is the only RM call on this path (Article 2 submit-time name resolution) — otherwise enqueue only (Article 11).

## Page integration (`edm_detail_body.html`, `portfolio_row.html` — EDITs)

- **Action entry point**: a "Break out" control on each portfolio row (near the row's expand affordance), shown when `edm.status == 'ready'`; `hx-get` the modal route. Section-header note "split / breakout arrive Iteration 4" is retired.
- **In-flight indicator**: rides the existing `live` self-poll machinery — `detail_state`/body poll continues while any `run_breakout_*` job for the EDM's portfolios is `pending|running`; slice rows appear on each poll as the worker upserts them (figures show the pending/empty state until `backfill_edm_detail` completes — existing graceful-empty rendering).
- **Completion surfacing**: on the first poll after terminal, a banner/toast summarizes `output_data` (*"LOB breakout: 10 created, 1 adopted, 1 failed"*); failures keep a per-row `.form-banner--error`-style line in the portfolio section with the re-run affordance (same POST — idempotent). **Lifecycle (clarified 2026-07-30)**: the error line is server-rendered from the **latest terminal** `run_breakout_*` job's `output_data` for that portfolio + dimension — it survives refresh and navigation, carries no dismissal state, and disappears only by being superseded by the next terminal run (Sync → re-run for drift; plain re-run for transient failures). Zero-match reasons additionally point at Sync.
- **Lineage badge** (`portfolio_row.html`): slice rows show `↳ from {source_name} · {dimension label}: {value}`; chained lineage shows the immediate source only. Broker-arrived rows unchanged.

## UI preview obligation (docs/UI_WORKFLOW.md rule 1)

The **breakout modal is a real new interactive surface** → build a rendered HTML preview from `docs/ui_previews/_scaffold.html` and get the informal 👍 **before wiring**. States to cover: LOB preview (happy path), state preview with the sharpened overlap disclosure, missing-summary disabled state (Sync pointer), single-value disabled state, in-flight state, and the completion banner incl. partial failure. The portfolio-row badge + button are derivative (existing `.dtable` row styling) — no preview needed.

## Styling (Article 9)

`details.css` (EDIT): modal slice-list, lineage badge, in-flight indicator — via existing tokens/ITCSS layers; no hardcoded hex.

## Route-test surface

`test_breakout_routes.py` — modal states (eligible / disabled-with-reason / in-flight), CSRF enforcement, 409 gate refusal, 409 stale-summary refusal (stamp mismatch → banner + no job row), enqueue idempotency (double POST → one job), body-partial response shape, 404 fragments.
