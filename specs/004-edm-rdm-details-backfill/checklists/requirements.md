# Specification Quality Checklist: EDM/RDM Details & Backfill (Iteration 3)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-23
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
- **Content Quality note.** This spec names domain/schema entities (`irp_edm`, `irp_portfolio`, `irp_treaty`, `irp_analysis`, `irp_job`/`rwb_job`, `rdm_id`) and Risk Modeler status vocabulary (`FINISHED`, *ready*). These are the project's canonical domain vocabulary from DATA_MODEL.md and the PRD, not technology/framework choices, and are used consistently across specs 001–003; they are retained deliberately for traceability. Concrete library method names and table designs are deferred to planning and appear only in the Dependencies section as confirm-before-implement items.
- **Three scope decisions** (backfill = forward-only; per-portfolio figures = read-only, included; broker results = analyses + settings only) were taken in the 2026-07-23 clarification session and are recorded under **Clarifications**; no open [NEEDS CLARIFICATION] markers remain.
- **Portfolio-primacy correction (2026-07-23 follow-up).** After a review of the design record (design note `04` §4–§5 + TL;DR; FUNCTIONAL_REQUIREMENTS §2.2), the spec's emphasis was corrected: the **per-portfolio breakdown is the P1 headline** (US1, shown inline on the EDM page) and the **EDM-aggregate rollup is demoted to P3 quick orientation** (US4, shown both as an EDM-page strip and a per-EDM line on the submission page). The EDM header is minimal (name, status + `as_of`, source + identifiers, portfolio count; no cedant/LOB). Requirements were regrouped and renumbered accordingly (US1 = FR-010–FR-018, US2 = FR-020–FR-025, US3 = FR-030–FR-035, US4 = FR-040–FR-043). Re-validated against all checklist items: still passing.
