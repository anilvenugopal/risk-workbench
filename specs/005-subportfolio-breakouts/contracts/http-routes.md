# Contract: HTTP routes & UI — breakout modal, confirm, lineage display (R8)

**Module**: `app/routers/portfolios.py` (NEW; nav key `irp.edm_library` — no new nav node, Article 1)

Conventions inherited wholesale: `_render`/`_partial` helpers, `validate_csrf_token` on POST (HTMX CSRF failure → `204 + HX-Refresh`), gate refusal → **409 + re-rendered fragment** (the `packages.py` precedent), toasts via `HX-Trigger: {"rwb:toast": …}`, errors via `.form-banner--error` in fragments.

*(Revised 2026-08-03 after the probe run. Two things this file used to specify are now wrong and are corrected below: the FR-007 overlap disclosure was static copy — it is now quantified per portfolio from the stored account counts (P-13); and the preview list carried only value and name — it carries the account count behind each value and the generated number is what identifies the sub-portfolio for adoption (P-11).)*

---

## Routes

### `GET /edms/{edm_id}/portfolios/{portfolio_id}/breakout` — modal fragment

- Auth'd fragment; calls `breakout_service.evaluate_gate`.
- Renders `partials/breakout_modal.html` (Alpine open/close — the `package_modal.html` precedent; fetched into a dedicated `#breakout-modal-mount` div in `pages/edm_detail.html`, **outside the self-polling `#edm-detail` wrapper** so the 3-second poll never removes an open modal; self-removing on completion):
  - **Modal header**: source portfolio name, RM portfolio id, and the source's **account total where the stored summary carries one** (P-13) — the denominator behind the per-value counts; omitted when `account_total` is absent.
  - **Dimension chooser**: LOB / Geography (state) — each option enabled per `DimensionEligibility`; ineligible options rendered disabled with their `reason` (missing summary variant links the existing per-EDM **Sync** control). A Sync link inside the modal submits the existing `POST /edms/{edm_id}/sync` — the same action as the page-header button — **and closes the modal**: the modal renders one summary snapshot and never stays open across a sync; progress shows on the page ("Syncing…" + self-poll) and the Break out control re-enables when the refresh finishes.
  - **Preview list** (per selected dimension, FR-006): one row per sub-portfolio to be created — *value*, *display label* where one exists, *generated name*, and *account count* — from `build_breakout_plan`, plus the count of sub-portfolios. **No truncation of the list regardless of count.** `exists` rows marked "already created" (idempotent re-run view). Geography values are `Admin1Code`; the `Admin1Name` label renders next to the code where the EDM has it (P-12).
  - **Disclosures (FR-007)**: (a) account-bucketing overlap, **quantified for this portfolio** from `compute_overlap` — *"12 of this portfolio's 1,701 accounts match more than one state and will be included in full in each one"*, or the clean-partition wording when `repeats == 0`, or the qualitative sentence alone when `account_total` is absent — followed by the fixed note that exposure inflation can exceed account inflation, since the accounts appearing in several sub-portfolios tend to be the largest. Geography adds the multi-state-account consequence explicitly. (b) blank/unassigned values (static copy): *"Exposure with no {LOB/state} value is not included in any sub-portfolio."*
  - **Large fan-out note (FR-006c)**: above the named threshold (25 sub-portfolios) the preview adds a plain statement that the run takes several minutes. The queue-occupancy consequence (the run holds the single rwb_job worker — [worker-poller.md](worker-poller.md)) stays out of analyst-facing copy (P-15 as amended 2026-08-04). There is no cap and no second gate — one confirm regardless of count (P-15).
  - **Confirm button** → the POST below. Nothing is created before confirm (Article 5). The gate's `summary_as_of` rides along as a hidden field (FR-002b).
  - `in_flight` state: chooser replaced by "a {dimension} breakout is already running" + live indicator.
  - `refresh_in_flight` state: chooser disabled with "this EDM is syncing — the exposure summary is being rewritten" (P-16). The self-poll already running on the page brings the analyst back to the eligible state when the refresh finishes.
- Portfolio/EDM missing or deleted → 404 fragment (graceful, non-erroring page).

### `POST /edms/{edm_id}/portfolios/{portfolio_id}/breakout` — confirm

- Form fields: `dimension` (`lob`|`state`), `summary_as_of` (echoed from the preview, FR-002b), `csrf_token`. HTMX + no-JS PRG both supported (the `sync()` route precedent).
- Flow: `validate_csrf_token` → `breakout_service.request_breakout`, whose five ordered steps are the gate re-check, **the FR-002b summary-unchanged check** (the stored summary's `as_of` must equal the one the preview carried), **the FR-002a summary-freshness check** (current RM `stampDate` via `search_portfolios` must equal the stamp captured at backfill), **composing and persisting the approved plan** into `input_data` — the list the worker executes rather than recomputing (FR-006a / AGENTS.md rule 8) — and the idempotent enqueue.
  - Success → returns the EDM **body partial** (`_body_partial` reuse) with the breakout-in-flight indicator; `HX-Trigger` toast *"Breakout started — N sub-portfolios"*. The Portfolios section's own 3-second poll takes over (T-11).
  - Gate refusal → **409** + modal fragment re-rendered with reasons.
  - Summary rewritten since the preview (`summary_as_of` mismatch) → 409 variant: *"This EDM was synced while you were reviewing — here is the current breakout."* The re-rendered preview shows the new values, and the analyst confirms again. **No `rwb_job` row is created.** This refusal must fire even when the `stampDate` still matches — a re-backfill that leaves the Risk Modeler portfolio untouched writes back an equal stamp (P-16).
  - Stale summary (stamp mismatch / no stored stamp / freshness unverifiable) → 409 variant: *"Portfolio data has changed in Risk Modeler since the last sync — Sync the EDM, then retry."* **No `rwb_job` row is created.**
  - Already running → 409 variant with "already running" banner (idempotent enqueue returned `None`).
- The freshness read is the only RM call on this path (Article 2 submit-time name resolution) — otherwise enqueue only (Article 11).

### `GET /edms/{edm_id}/portfolios-section` — the breakout-episode poll (added 2026-08-05, T-11)

**Module**: `app/routers/edms.py` (the EDM detail page owns its own poll targets — `GET /edms/{edm_id}/body` is the precedent).

- Renders `partials/edm_portfolios_live.html`: the `#edm-portfolios` section (the poll's own swap target) plus **one out-of-band swap** — `#edm-detail-meta` (the header line: portfolio count, sync stamp, Sync button state). It is the only element outside the section that moves while sub-portfolios are created.
- Emits the `every 3s` trigger only while the breakout episode is live — a `run_breakout_*` job `pending|running`, or its FR-013 follow-up `backfill_edm_detail` still filling figures in — so polling self-terminates on the first render after the episode ends.
- EDM hard-gone mid-poll → a terminal section with no trigger, so the poll ends instead of 404-looping.
- Read-only: stored state only, no writes, no Risk Modeler call (Article 11).
- **Why the section and not the body**: `#edm-detail` is the detail page's scrolling element (`.shell` is `height:100vh; overflow:hidden`, `.page-pad` is `overflow:auto`), so the `hx-swap="outerHTML"` body poll destroyed and recreated the scroller every 3 seconds — the analyst was thrown back to the top of the page for the whole run. `app/static/js/app.js` additionally records every `<details id>` open state plus the scroller offset before any swap and reapplies both after it settles, so an expanded portfolio row stays expanded across the section swap.

## Page integration (`edm_detail_body.html`, `edm_portfolios_section.html`, `portfolio_row.html` — EDITs)

- **Action entry point**: a "Break out" control on each portfolio row (near the row's expand affordance), shown when `edm.status == 'ready'`; `hx-get` the modal route. Section-header note "split / breakout arrive Iteration 4" is retired.
- **In-flight indicator**: the `#edm-portfolios` section polls itself while any `run_breakout_*` job for the EDM's portfolios is `pending|running` (T-11 — the body poll is not involved); generated portfolio rows appear on each poll as the worker upserts them (figures show the pending/empty state until `backfill_edm_detail` completes — existing graceful-empty rendering).
- **Completion surfacing**: on the first poll after terminal, a banner/toast summarizes `output_data` (*"LOB breakout: 10 created, 1 adopted, 1 failed"*); failures keep a per-row `.form-banner--error`-style line in the portfolio section with the re-run affordance (same POST — idempotent). **Lifecycle (clarified 2026-07-30)**: the error line is server-rendered from the **latest terminal** `run_breakout_*` job's `output_data` for that portfolio + dimension — it survives refresh and navigation, carries no dismissal state, and disappears only by being superseded by the next terminal run (Sync → re-run for drift; plain re-run for transient failures). Zero-match reasons additionally point at Sync.
- **Banner lifecycle (implemented 2026-08-04)**: the banner renders from the **newest** terminal `run_breakout_*` job on the EDM. A clean run's banner shows while that job's FR-013 follow-up `backfill_edm_detail` (keyed `rwb_job`/job id) is pending|running — the "figures are filling in" window — and disappears when it lands; a failed or partial run's banner is durable until the next terminal run supersedes it. During a breakout episode (the run itself, or the follow-up backfill filling in) the section poll above is what brings generated rows and the banner in as they land; the body-poll route keeps its populated-mid-sync 204 short-circuit throughout (**revised 2026-08-05, T-11** — it previously skipped the short-circuit during an episode, which re-rendered the scrolling wrapper every 3 seconds).
- **Lineage badge** (`portfolio_row.html`): generated rows show `↳ from {source_name} · {dimension label}: {value}`; chained lineage shows the immediate source only. Broker-arrived rows unchanged. The `states` column on every row now renders state codes (P-12) — pre-change snapshots keep showing names until the next Sync, and both render.

## UI preview obligation (docs/UI_WORKFLOW.md rule 1)

The **breakout modal is a real new interactive UI** → build a rendered HTML preview from `docs/ui_previews/_scaffold.html` and get the informal 👍 **before wiring**. States to cover: LOB preview (happy path), state preview with the measured overlap statement, missing-summary disabled state (Sync pointer), single-value disabled state, in-flight state, sync-running disabled state (P-16), and the completion banner incl. partial failure. A long fan-out (40+ values, untruncated) is required, not optional — it is both the case the list has to stay readable in and the case that carries the FR-006c several-minutes statement. The portfolio-row badge + button are derivative (existing `.dtable` row styling) — no preview needed.

## Styling (Article 9)

`details.css` (EDIT): modal preview list, lineage badge, in-flight indicator — via existing tokens/ITCSS layers; no hardcoded hex.

## Route-test surface

`test_breakout_routes.py` — modal states (eligible / disabled-with-reason / breakout in-flight / sync in-flight), the preview list rendering value, label, name, and account count with no truncation, the quantified overlap statement in all three forms (repeats / clean partition / absent `account_total`), the FR-006c large-fan-out statement appearing above the threshold and absent below it, CSRF enforcement, 409 gate refusal, 409 stale-summary refusal (stamp mismatch → banner + no job row), 409 summary-rewritten refusal (`summary_as_of` mismatch with a matching stamp → re-rendered preview + no job row), the approved plan written into `input_data` at enqueue, enqueue idempotency (double POST → one job), body-partial response shape, 404 fragments.

`test_edm_sync.py` (T-11) — the breakout episode polls the section and not the body, the body poll answers 204 while the follow-up backfill fills figures in, the section poll's trigger and its two out-of-band fragments, the trigger gone once the episode is terminal, the EDM-gone terminal section, and the full-page render carrying the out-of-band target ids with no `hx-swap-oob` attribute on it.
