# Feature Specification: [FEATURE NAME]

**Branch**: `[###-feature-name]` | **Created**: [DATE]

<!-- Product only. Design → plan.md. Schema → data-model.md. Evidence and
     rejected options → research.md. Everything above the `---` is what a
     reviewer reads to decide: keep it under 40 lines. -->

## Status

<!-- "Ready for tasks" is declared once, in plan.md (Plan status) — set Phase
     to match it; do not restate the verdict here. -->

**Phase:** [Draft | Planning | Ready for tasks | Implementing]
**Blocking:** [what, or Nothing]

## Outcome

[Two sentences. What can a user do now that they could not before?]

## In scope

- [capability]

## Out of scope

- [excluded thing, so no one has to ask]

## Non-negotiable behavior

1. [rule a reviewer must agree to]

## Open product decisions

<!-- Every product decision a reviewer must see, resolved or not — and nowhere
     else. Assumptions go here with status Assumed. Status: Approved | Proposed
     | Assumed | Open | Deferred | Blocked. Decided rows stay in the table and
     keep their ID (an O-nn stays O-nn when approved) — task [Ref] tags and
     research.md anchors point at these IDs. Delete the losing alternative,
     not the row; history lives in research.md. -->

| ID | Decision | Status | Where |
|---|---|---|---|
| O-01 | [question] | Blocked | `research.md#R1` |

---

## User Stories

<!-- 2–4 stories, P1 first, each independently shippable. Max 7 scenarios each. -->

### 1. [Title] (P1)

[The journey in plain language. One paragraph.]

**Acceptance**

1. **Given** [state], **When** [action], **Then** [outcome]

## Requirements

<!-- Max 25. Outcomes the user or business needs, not how it is built.
     Gaps: [NEEDS CLARIFICATION: question] plus an O-nn row above. -->

- **FR-001**: [capability]

## Key Entities *(if the feature involves data)*

- **[Entity]**: [what it means to the business]

## Success Criteria

<!-- Measurable, technology-agnostic. 3–5. -->

- **SC-001**: [measurable outcome]
