# Tasks: Analysis Comparison (Iteration 10)

**Input**: Design documents from `specs/013-analysis-comparison/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R5), data-model.md, contracts/routes.md, quickstart.md

**Tests**: Included — the constitution (Article 12) requires the pair validator and the
percent-change math to have unit coverage before the templates render them
(plan.md Testing). Only the unit tier is affected; there is no schema change
and no IRP call, so the SQL Server and IRP tiers gain nothing.

**UI preview**: already approved — `docs/ui_previews/analysis_comparison.html`
(P-01, 2026-08-27). No preview task; the preview is guidance, not markup.

**Organization**: tasks are grouped by user story. Implement one story per
pass and stop at its checkpoint for the approver to click the running feature
(docs/UI_WORKFLOW.md).

## Format: `[ID] [P?] [Story] [Ref] Description`

- **[P]**: can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3, mapped to spec.md
- **[Ref]**: the `FR-nnn` / `T-nn` / `P-nn` the task closes
- `- Proof:` names the test or observation that closes the task where it is
  not obvious

---

## Phase 1: Setup

No setup tasks. Existing single-app layout, no new dependency, no migration,
no seeds (plan.md Material changes). The unit tier runs from any host shell:
`uv run pytest tests/unit`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the two `ResultsColumn` fields every story reads — the column
headers (US1), the screen-wide render (US2), and the currency guard (US3) all
depend on them.

- [X] T001 [T-03] [T-04] Failing unit tests for `ResultsColumn.engine` and `ResultsColumn.run_currency` in `tests/unit/test_analysis_service.py`: own row → `submitted_settings.currency.code`, broker row → `settings_metadata.currencyCode`, missing value → `None`; engine joined from the extract's `engine_type`/`engine_version` as `AnalysisSettings.engine` joins them, never from `settings_metadata`
- [X] T002 [T-03] [T-04] [FR-005] [FR-011] Extend `ResultsColumn` in `app/services/analysis_service.py` with `engine` and `run_currency`, populated in `list_results_columns`; existing callers unaffected (data-model.md)
  - Proof: T001 tests pass; `uv run pytest tests/unit/test_analysis_service.py` green

**Checkpoint**: foundation ready — user story phases can begin.

---

## Phase 3: User Story 1 — Compare two finished analyses (Priority: P1) 🎯 MVP

**Goal**: from the merged analyses table, the analyst picks Compare, ticks two
analyses (first = base), adds the pair, and a new browser tab renders base,
second, and % Chg with per-side currency and engine — no export, no
spreadsheet.

**Independent Test**: quickstart.md Story 1 — one pair from the submission
Results section end-to-end, breadcrumbs and tab title per entry point, page
renders with the modeling platform unreachable.

### Tests for User Story 1 (write first, watch them fail)

- [X] T003 [P] [US1] [T-01] [T-06] [FR-009] Failing unit tests for pair resolution and percent change in `tests/unit/test_comparison_service.py` (new): `pairs` string parses to ordered UUID pairs; a pair is dropped whole when a side fails to parse or resolve, the ids are equal, either run currency is unrecorded, or currencies differ; only the first 5 pairs survive; % Chg = (second − base) / base per return period and for AAL and Std dev; zero or missing base → `None`, never `inf`
- [X] T004 [P] [US1] [T-05] [FR-001] [FR-002] Failing unit tests for the modal read model in `tests/unit/test_analysis_service.py`: `list_comparable_analyses` returns own rows then broker rows in table order with id, name, `rdm_name`, `run_currency`, `results_state` (`pending`/`failed`/`ready`, failed via the `rwb_job` retrieval join); results-ready count counts `loss_results IS NOT NULL` with broker handles deduped once per `irp_id`
- [X] T005 [P] [US1] [T-01] [FR-007] [FR-012] Failing route tests for `GET /results/comparison` in `tests/unit/test_results_comparison.py` (new): one pair renders base/second/% Chg columns headed by analysis names with currency and engine sub-lines; submission entry → submission crumb + submission tab title, EDM entry → EDM and submission crumbs + EDM tab title; unknown `perspective`/`ep_type` fall back to defaults (Pre-Cat Net, OEP); no Risk Modeler call in the render path

### Implementation for User Story 1

- [X] T006 [US1] [T-01] [T-06] [FR-003] [FR-009] [FR-014] Implement `ComparisonPair` and `list_comparison_pairs` in `app/services/analysis_service.py`: resolve pairs over `list_results_columns`, apply the four build-time validations (data-model.md), compute per-row percent change server-side; absent perspective on either side → no percent (em-dash cell)
  - Proof: T003 tests pass
- [X] T007 [US1] [T-05] [FR-001] [FR-002] Implement `list_comparable_analyses` and the results-ready count in `app/services/analysis_service.py`, composing the table's existing reads (`list_submission_executed_analyses` / `list_executed_analyses` + RDM broker reads) so dedup and soft-delete rules are inherited
  - Proof: T004 tests pass
- [X] T008 [P] [US1] [FR-007] Add hidden nav node `results.comparison` (label "Comparison") to `app/nav/manifest.py`, sibling of `results.analyses`
  - Proof: `tests/unit/test_nav_manifest.py` still green with the node present
- [X] T009 [US1] [T-01] [FR-007] [FR-012] [FR-016] Implement `GET /results/comparison` in `app/routers/shell.py`: parse `pairs`/`submission`/`edm`/`perspective`/`ep_type`, call `list_comparison_pairs`, build `extra_crumbs` and tab title exactly as `/results/analyses` does; render `pages/results_comparison.html`; never a 500 for bad input (P-06)
  - Proof: T005 tests pass
- [X] T010 [US1] [FR-008] [FR-010] [FR-011] Create `app/templates/pages/results_comparison.html`: one shared return-period column; per pair three columns (base, second, % Chg) headed by analysis names with each side's run currency and engine sub-line; the 11 return-period rows for the selected EP type, then AAL and Std dev below the curve rows; minimal empty state when no pair survives (US3 finishes its copy); extend `.ep` / `.res-toolbar` classes, no preview classes pasted
- [X] T011 [US1] [FR-002] Add the compare-modal fragment routes (contracts/routes.md §1) — `GET /submissions/{submission_id}/analyses/compare` and `GET /submissions/{submission_id}/edms/{edm_id}/analyses/compare` in `app/routers/submissions.py`, `GET /edms/{edm_id}/analyses/compare` in `app/routers/edms.py` — one shared handler rendering `partials/compare_modal.html` from `list_comparable_analyses`; gone scope → the analyses-section gone-notice precedent
  - Proof: route tests for all three scopes in `tests/unit/test_results_comparison.py`
- [X] T012 [US1] [T-02] [FR-003] [FR-006] Create `app/templates/partials/compare_modal.html` with the Alpine cart: rows carry `data-currency`, id, name; tick order marks the first pick *base*; two ticks arm **Add pair**; one analysis may sit in any number of pairs (two ticks are two distinct rows, so self-pairing cannot be expressed); **Compare N pairs** builds the §2 URL from the fragment's `submission`/`edm` ids and opens it via `window.open` (the View pattern), closing the modal
- [X] T013 [US1] [T-05] [FR-001] Add **Compare** beside View in `app/templates/partials/analyses_merged_section.html` on all three scopes, enabled when the results-ready count ≥ 2, `hx-get` to the matching modal route targeting `#compare-modal-mount`; add the mount outside the self-polling section in `app/templates/pages/submission_detail.html` and `app/templates/pages/edm_detail.html` (breakout-modal precedent)
- [X] T014 [US1] Extend `app/static/css/details.css` and `app/static/css/components.css` through existing tokens: percent cell, base tag, cart rows (Article 9 — no hardcoded hex)
- [X] T015 [US1] Run the unit tier: `uv run pytest tests/unit` — all green, including T003–T005

**Checkpoint**: Story 1 works end-to-end. **STOP** — the approver clicks the
running feature (quickstart.md Story 1) before Story 2 begins.

---

## Phase 4: User Story 2 — Several pairs on one screen, one set of controls (Priority: P2)

**Goal**: up to 5 pairs render on one page; perspective, EP type, and units
are chosen once for the whole screen; the table copies into a spreadsheet
with headers.

**Independent Test**: quickstart.md Story 2 — two pairs on one page, sixth
pair refused, one perspective switch moves every pair, units never rescale a
percent, copy-with-headers pastes intact.

### Tests for User Story 2 (write first, watch them fail)

- [X] T016 [P] [US2] [FR-012] [FR-014] [T-06] Failing route tests in `tests/unit/test_results_comparison.py`: multiple pairs render against the one shared return-period column with no per-pair label; `perspective`/`ep_type` params re-render every pair (defaults Pre-Cat Net, OEP); a perspective one side did not produce shows the base numbers, an absent message on the other side, and an em dash for % Chg — never an error; loss cells carry `data-unit-value`, percent cells carry none

### Implementation for User Story 2

- [X] T017 [US2] [FR-012] [FR-013] [T-06] Add the screen-wide toolbar to `app/templates/pages/results_comparison.html`, the `/results/analyses` toolbar verbatim in pattern: perspective and EP-type selects re-render `#comparison-view` over HTMX carrying `pairs`/`submission`/`edm` and each other's value; units is the existing `data-units-select` sliver (default millions), Copy table the existing `data-copy-table` sliver; loss cells get `data-unit-value`, percent cells none
  - Proof: T016 tests pass; quickstart Story 2 step 3 (units rescale losses, never percents)
- [X] T018 [US2] [FR-014] Render the absent-perspective case in `app/templates/pages/results_comparison.html`: absent side shows the absent message, partner's numbers render, % Chg is an em dash (`list_comparison_pairs` already yields `None` from T006)
  - Proof: T016 absent-perspective assertions pass
- [X] T019 [US2] [FR-004] [P-02] Cart cap in `app/templates/partials/compare_modal.html`: a sixth **Add pair** is refused with the reason; removing a cart row re-arms adding; **Compare N pairs** works for 1–5
  - Proof: quickstart Story 2 step 2
- [X] T020 [US2] Run the unit tier: `uv run pytest tests/unit` — all green

**Checkpoint**: Stories 1 and 2 work. **STOP** — the approver clicks
quickstart Story 2 before Story 3 begins.

---

## Phase 5: User Story 3 — The guards: currency, missing results, vanished analyses (Priority: P2)

**Goal**: every pairing rule is enforced where the analyst acts, with a
reason, never silently — and the server render, not the modal, is the
enforcement (SC-003).

**Independent Test**: quickstart.md Story 3 — mixed-currency add refused
naming both currencies, non-ready rows listed but not tickable, deleted
analysis drops its pair whole with a notice, hand-typed garbage URL shows the
empty state.

### Tests for User Story 3 (write first, watch them fail)

- [X] T021 [P] [US3] [FR-015] [P-06] [SC-003] Failing route tests in `tests/unit/test_results_comparison.py`: a dropped pair produces one notice above the table naming the missing analysis (unresolvable side) or the two currencies (mismatch), and the generic dropped-pair notice for a self-pair, an unrecorded currency, or a pair beyond the first 5; surviving pairs render normally; no `pairs` param, garbage ids, or no surviving pairs → the empty state directing the analyst to Compare on a submission or EDM page — never a 500, never a converted figure
- [X] T022 [P] [US3] [FR-002] [P-05] Failing route tests for the modal fragment in `tests/unit/test_results_comparison.py`: rows with `results_state` `pending`/`failed` render disabled with the state named ("retrieving…" / "retrieval failed"); a row with no recorded run currency renders tickable but carries no `data-currency`

### Implementation for User Story 3

- [X] T023 [US3] [FR-005] [P-05] Pair-add refusal in `app/templates/partials/compare_modal.html`: Add pair refused with a message naming both currencies when the two `data-currency` values differ, and naming the missing currency when either is unrecorded; the ticks stay for re-picking
  - Proof: quickstart Story 3 step 1
- [X] T024 [US3] [FR-002] Disabled modal rows in `app/templates/partials/compare_modal.html`: `pending` and `failed` rows listed, never tickable, state named
  - Proof: T022 tests pass
- [X] T025 [US3] [FR-015] [SC-003] [P-06] Drop notice and final empty state in `app/templates/pages/results_comparison.html` and the `app/routers/shell.py` handler: one notice above the table — the missing analysis or the two currencies when the cause is known, the generic dropped-pair line otherwise; empty state copy directing the analyst back to Compare
  - Proof: T021 tests pass
- [X] T026 [US3] Run the unit tier: `uv run pytest tests/unit` — all green

**Checkpoint**: all three stories work. **STOP** — the approver clicks
quickstart Story 3.

---

## Phase 6: Polish & Cross-Cutting

- [x] T027 Review the diff for subtraction (AGENTS.md): remove comments and tests restating the implementation, inline helpers that only rename a call, drop speculative branches; compare diff size with requirement size
- [ ] T028 Walk quickstart.md end-to-end with the developer (needs the stack up — the developer starts it, not an agent); report which tiers ran: unit expected green, SQL Server and IRP tiers not applicable this iteration

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 2 (Foundational)** blocks everything: T001 → T002.
- **Phase 3 (US1)** after Phase 2. Within it: T003/T004/T005 (tests, parallel)
  → T006 → T007 → T008 ∥ T009 → T010 → T011 → T012 → T013 → T014 → T015.
- **Phase 4 (US2)** after the US1 checkpoint: T016 → T017 → T018 → T019 → T020.
  T017/T018 touch the same template — sequential.
- **Phase 5 (US3)** after the US2 checkpoint: T021 ∥ T022 → T023 → T024 → T025
  → T026. T023/T024 touch the same partial — sequential.
- **Phase 6** last.

### Story Dependencies

- US1 delivers the page, the modal, and the service reads — the MVP.
- US2 and US3 both extend US1's files (page template, modal partial); they are
  independently *testable* (each has its own quickstart section and route
  tests) but not independently *implementable* before US1.

### Parallel Opportunities

- T003, T004, T005 — three different test files/concerns, no shared code yet.
- T008 (nav manifest) with T009 (route handler) — different files.
- T021 and T022 — both add tests to `tests/unit/test_results_comparison.py`;
  parallel only as authored sections, sequential if edited as one file.

---

## Implementation Strategy

**MVP = User Story 1.** Phase 2 + Phase 3 give the analyst the whole headline
case: own-vs-broker, one pair, percent change, per-side currency and engine,
platform-down-safe. Stop at the T015 checkpoint and get the click.

Then one story per pass: US2 (multi-pair + screen-wide controls), click; US3
(guard messages, drop notice, empty state), click; polish.

Note the enforcement layering across stories: `list_comparison_pairs` (T006,
US1) already drops every invalid pair — mixed currency, unrecorded currency,
self-pair, unresolvable side, sixth pair — so SC-003 holds from the MVP
onward. US3 adds the *messages* (modal refusal reasons, drop notice, empty
state copy), not the enforcement.
