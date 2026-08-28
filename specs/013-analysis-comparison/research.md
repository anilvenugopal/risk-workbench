# Research — Analysis Comparison (Iteration 10)

Evidence and rejected alternatives for the T-nn rows in [plan.md](plan.md).
All findings come from the repo and the spec-011 implementation; no external
spike was needed — the feature reads data spec 011 already stores.

## R1 — Page contract: `GET /results/comparison?pairs=` with server re-validation (T-01)

**Decision**: The comparison page is
`GET /results/comparison?pairs=<baseId>:<secondId>,<baseId>:<secondId>[&submission=…][&edm=…][&perspective=…][&ep_type=…]`.
Pairs are colon-joined UUID pairs, comma-separated, in cart order. The route
re-validates every pair at render and drops any pair that fails — both sides
must resolve to undeleted `irp_analysis` rows, the two ids must differ (P-04),
both run currencies must be recorded and equal (P-05, FR-005), and at most the
first 5 pairs render (P-02). Each dropped pair produces the FR-015 notice; a
page with no surviving pairs shows the empty state (P-06).

**Rationale**:

- UUIDs contain hyphens, so `:` is the unambiguous in-pair separator. Five
  pairs is ten UUIDs ≈ 400 characters of query string — far under any URL
  limit, and the same order-carrying-URL pattern the results page already
  uses for `ids` (spec 011 FR-016).
- A real, shareable URL is Article 8 ("every page/detail MUST have a real
  URL") and is what makes P-06 work: a hand-typed or stale URL is just a
  render whose pairs fail validation, not a distinct error path.
- Server re-validation is what makes SC-003 airtight: the modal guard (R2) is
  client-side convenience; the render is the enforcement. A hand-typed
  mixed-currency pair is dropped exactly like a deleted-analysis pair, so no
  cross-currency percent change can ever be produced. FR-015's drop notice is
  reused for every failure kind; the notice names the analysis when one side
  is missing and the currencies when the mismatch is the cause.

**Alternatives considered**:

- *POST form with `target="_blank"`* — opens a new tab without a URL the
  analyst can refresh or share; violates Article 8's real-URL rule and makes
  P-06 (stale URL → empty state) meaningless. Rejected.
- *Persisting the cart server-side and passing one comparison id* — the spec
  rules persistence out ("Comparisons are not persisted", P-06). Rejected.
- *Trusting the modal guard and skipping render-time validation* — leaves
  SC-003 open to hand-edited URLs. Rejected.

## R2 — The cart is an Alpine sliver in a server-fetched modal fragment (T-02)

**Decision**: Compare fetches a modal fragment over HTMX into a
`#compare-modal-mount` that sits outside the self-polling analyses section
(the `breakout_modal.html` / `#breakout-modal-mount` precedent — the section's
3-second poll must never remove an open modal). The fragment lists the table's
analyses server-rendered, each row carrying `data-currency` and its results
state; tick order, the base mark, pair-add refusals (currency mismatch,
unknown currency), the 5-pair cap, pair removal, and the final
`window.open(...)` are an Alpine component inside the fragment. No server
round trip serves a pair-add.

**Rationale**:

- Everything a pair-add checks is already in the fragment (two ticked rows'
  `data-currency` values, the cart length), so a round trip would compose
  nothing the client doesn't have. The breakout cart is not the precedent
  here: its rows were *server-composed previews* (group JSON the server had
  to build); a comparison cart row is two names the list already shows.
- Article 8 permits Alpine for exactly this — a modal sliver. The server
  stays the enforcement point (R1).
- `window.open` with a built URL is how View already opens the results page
  in a new tab (`app.js` `data-view-analyses` handler); Compare follows it.

**Alternatives considered**:

- *Server-composed cart rows over HTMX POSTs (breakout style)* — one round
  trip per add/remove to render two names the page already has; the modal
  would also need server-held cart state the spec says must not exist.
  Rejected.
- *A fully client-side modal built from the table's DOM rows* — broker rows
  lazy-load per RDM group, so the DOM may not hold them; the modal must list
  the whole table scope regardless of which groups are expanded. A
  server-rendered fragment reads the full scope in one request. Rejected.

## R3 — Currency and engine sources per side (T-03, T-04)

**Decision**: The pairing guard and the column-header currency read the **run
currency**: own rows from `submitted_settings.currency.code` (the submit-time
snapshot, spec 011 T-09), broker rows from `settings_metadata.currencyCode`
(captured at analysis-detail backfill, spec 011 T-05). A row missing its value
is listed but not pairable (P-05). The engine/model version per side reads the
extract's own `engine_type` / `engine_version` snapshot
(`irp_analysis.loss_results`, spec 011 T-04/FR-021) rendered as
`AnalysisSettings.engine` renders — never re-fetched.

**Rationale**:

- FR-005 names both sources explicitly, and they are both already stored:
  `_submitted_view` (analysis_service.py) parses
  `submitted_settings.currency.code/scheme/vintage`; `_to_display` parses
  `settings_metadata.currencyCode`. No new column, no backfill.
- The extract carries `engine_type`/`engine_version` at document level
  (contracts/loss-results.md, spec 011) precisely so results views can show
  what each side ran (the note 18 O18-10 gap, SC-005). Only rows with a
  retrieved extract are pairable (FR-002), so the snapshot is always present
  for a rendered side.
- `ResultsColumn` (the dedicated page's column model) today exposes
  `currency` (settings_metadata) but not the engine or the run currency —
  the comparison read extends it with both rather than growing a parallel
  model.

**Alternatives considered**:

- *One source (`settings_metadata.currencyCode`) for both origins* — simpler,
  but FR-005 fixes the own-row source as the submit-time run currency;
  the submitted snapshot is the value the workbench itself sent to Risk
  Modeler, immutable by construction (AGENTS.md rule 8). Rejected.
- *Engine from `settings_metadata`* — the extract snapshot was captured for
  this exact view and is atomic with the numbers it labels. Rejected.

## R4 — Modal data and Compare enablement (T-05)

**Decision**: A new read model, `list_comparable_analyses(scope)`, returns the
table-at-hand's analyses flat and in table order — own rows (EDM- or
submission-scoped) then broker rows grouped by RDM — each entry carrying id,
display name, origin/RDM name, run currency (R3), and results state. The
Compare button's enabled state comes from a scope-wide count of analyses with
`loss_results IS NOT NULL` (broker handles counted once per `irp_id`, the
`_dedup_handles` rule), computed at section render.

**Rationale**:

- The section render cannot derive the count from its own context: broker
  rows lazy-load per RDM group (`contextual_rdm_analyses`), so the section
  holds group *counts* but not results states. One COUNT query at render is
  the cheap answer to P-01 ("enabled whenever the table holds two or more
  analyses with retrieved results").
- The modal list composes the same reads the table already uses
  (`list_submission_executed_analyses` / `list_executed_analyses` +
  the RDM-group broker reads), so modal order matches table order and the
  dedup and soft-delete rules are inherited, not re-implemented.

**Alternatives considered**:

- *Enable Compare from own-ready + broker-total counts already in context* —
  overcounts broker rows whose retrieval failed or is pending; P-01 says
  "with retrieved results". Rejected.
- *Always-enabled button with a "not enough analyses" modal state* —
  contradicts the approved preview and P-01. Rejected.

## R5 — Percent change is server-computed; the slivers are untouched (T-06)

**Decision**: `% Chg = (second − base) / base` is computed in the read model
per displayed row (each of the 11 return periods, AAL, standard deviation)
and rendered server-side as a signed one-decimal percent. No value is
computed when either side's perspective is absent (FR-014) or when the base
value is zero or missing (division undefined) — the cell renders an em dash.
Loss cells carry `data-unit-value` exactly as on the results page so the
existing units sliver rescales them; percent cells carry none, so no unit
change ever touches a percent. Copy-with-headers is the existing
`data-copy-table` sliver over the same table markup.

**Rationale**:

- The only computed figure the spec allows is this percent (non-negotiable
  4); computing it where the stored numbers are read keeps FR-016 ("stored
  result extracts only") trivially true and keeps the client slivers
  display-only (spec 011 FR-018 carried).
- A zero base at low return periods is a real case (the stored curves carry
  literal `0.0` rows — see contracts/loss-results.md example); an undefined
  percent must degrade to absent, never to `inf`/`NaN` markup.

**Alternatives considered**:

- *Client-side percent computation from `data-value` attributes* — moves the
  one piece of arithmetic the feature owns into a display sliver, where the
  screen-wide HTMX re-render (perspective/EP swap) would have to re-run it;
  the server already re-renders those cells. Rejected.
