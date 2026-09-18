# Specification Quality Checklist: Treaty-Level (TY) Loss Results Export

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-16
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

- O-01, O-02, and O-03 closed 2026-09-16 by the user: write every treaty (P-03), data name plus treaty number and name (P-06), per-treaty AAL from the loaded rows and none in the cart (P-10). FR-002, FR-009, and FR-016 carry them.
- The spec depends on spec 014 as amended by the 2026-09-15 session (flat exports table, decimal model version, export-wide engine-version override). Those amendments are 014's to record; this spec references them and does not restate them.
- P-01 (group first) is approved from the 2026-09-11 session. The rejected aggregate-on-export path and its trade-offs belong in research.md when the plan is written, per Anil's request that both stay documented.
- P-02, P-07, P-08, and P-09 approved 2026-09-16 by the user, with two rules added the same day: TY is offered when every selected analysis was run with treaties (P-05, FR-001), and the manifest holds one row per treaty per analysis (P-09, FR-011). The Workbench today records result availability for the five viewing perspectives only; how FR-001 reads "run with treaties" from Workbench or irp-integration data is a plan question, not a spec one.
- P-11 / FR-017 state the per-event combination of one treaty's rows (loss summed, independent SD summed, correlated SD root-sum-square) from the 2026-09-11 session. O-04 (exposure value) is Open: the spec keeps the largest value until Cheng's query is read. It blocks `/speckit-tasks`, not `/speckit-plan`.
