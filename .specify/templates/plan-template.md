# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [spec.md](spec.md)

<!-- Technical only. User stories and scope → spec.md. Schema → data-model.md.
     Payloads → contracts/. Endpoint investigation → research.md. Everything
     above the `---` is what a reviewer reads to decide: ten minutes to read. -->

## Plan status

**Ready for tasks:** [Yes | No]
**Blocked by:** [O-nn / T-nn, one line each, or Nothing]

## Design summary

<!-- Max 15 bullets, in execution order. Name what runs, where it runs, and what
     it writes: the route, the worker, the table, the job. -->

- [what happens on the request path]
- [what is persisted, and when]
- [what the worker does]
- [what fires afterward]

## Material changes

| Area | Change |
|---|---|
| Database | |
| Worker | |
| UI | |
| Library | |

## High-risk technical decisions

<!-- Every technical decision a reviewer must see, resolved or not — and
     nowhere else. Status: Approved | Proposed | Assumed | Open | Deferred |
     Blocked. Decided rows stay in the table and keep their ID — task [Ref]
     tags and research.md anchors point at these IDs. Delete the losing
     alternative, not the row; history lives in research.md. -->

| ID | Decision | Status | Detail |
|---|---|---|---|
| T-01 | | Approved | |

---

## Technical Context

<!-- Only what changed or constrains the design. The stack is documented in
     docs/PRD.md §3 (Technology stack & environment); architecture rules in
     .specify/memory/constitution.md. Do not restate either. -->

**New dependencies**: [or None]
**Databases touched**: [which of the three, and why]

## Constitution Check

*GATE: before Phase 0 research, re-checked after Phase 1 design.*

Reviewed against all 13 articles in `.specify/memory/constitution.md`: [no violations | violations below].

Material interactions — where an article actively shapes this design:

- **Article [n] ([title])**: [how]

## Project Structure

<!-- Changed areas only, real paths. -->

```text
[path/that/changes]/     # what changes
```

## Complexity Tracking

> Only if the Constitution Check has a violation to justify.

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|

## Testing

<!-- Strategy by tier. Not a test-file inventory. -->

- **Unit**: [what is covered]
- **SQL Server integration**: [what is covered]
- **IRP sandbox**: [what is covered, or N/A]
