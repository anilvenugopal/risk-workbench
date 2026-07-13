# Feature Specification: Submission & Package Domain Model (Iteration 1)

**Feature Branch**: `002-submission-package-domain`

**Created**: 2026-07-10

**Status**: Draft

**Input**: User description: "Iteration 1 — Submission & Package domain model (WORKBENCH DB only). Scope per docs/PRD.md §7, §7.2, §7.2a, §7.2b, §6.1, §6.2, and the §21 build-plan entry 'Iteration 1 — Submission & Package domain model'; schema per DATA_MODEL.md §4–§5. Submission-as-deal (id, non-unique name, cedant, treaty type, inception, treaty year, renewal link, directory path, assigned analyst as soft owner), CRM-ID tag set, event-sourced status (ACTIVE/COMPLETED/CANCELLED, reopenable, no delete), non-unique-name identity + soft duplicate warning, function-level roles + 'My Submissions' filter with no row-level security, package *structure* only (bundle of ≥1 EDM/RDM members, sharable across submissions), treaty-type seed, optimistic concurrency, plus cleanup of the dropped CR-003 customer/RLS scaffolding from Iteration 0."

## Overview

This iteration establishes the **deal** as the top-level unit of work in the workbench. A *submission* is a specific cedant's specific treaty at a specific inception; it anchors everything that follows (packages, EDMs, RDMs, jobs, analyses). This iteration delivers the submission behavior an analyst uses day-to-day — create, find, filter, tag, and track status — and puts the **package structure** in place (the data foundation on which Iteration 2 builds real package behavior). It also retires the customer-hierarchy and row-level-security scaffolding that Iteration 0 left behind (dropped by CR-003).

There is intentionally **no customer or program tier** above a submission, and **no row-level security**: every authenticated analyst can see and act on every deal. Roles gate *functions*, never *rows*.

> **Provisional model (known risk).** The top-level shape defined here — submission-as-deal, CRM identifiers as flat tags — is a *build-to-learn* decision that the CIC team has reopened but not ratified (open questions OQ-1/OQ-2 in design note 03, pending wireframe review). It is deliberately built as the low-regret shape: the surrogate-key identity and the tag-set / package-bundle structure absorb a later "add a tier" or "rename the root" decision without a rebuild. Treat the top-level naming and the flat-CRM choice as subject to change.

## Clarifications

### Session 2026-07-10

- Q: What triggers the non-blocking "a similar deal already exists" warning on create/rename? → A: A **name** match **or** a match on **cedant + treaty type + inception date** (either condition warns; neither blocks).
- Q: When a submission is COMPLETED (or CANCELLED), what does the status gate block? → A: **Fully read-only** — all edits (submission fields, CRM tags, and later package actions) are blocked; only viewing and reopening (to ACTIVE) are permitted. *(PRD §7.2a reconciled to match — closed = fully read-only, not just package actions.)*
- Q: Can a submission's assigned analyst (soft "My Submissions" owner) be reassigned after creation? → A: **Yes** — any analyst may reassign the owner; it changes only the "My Submissions" filter, never access.
- Q: Is a CANCELLED submission a one-way door, or recoverable? → A: **Recoverable** — `CANCELLED → ACTIVE` reopening is allowed, the recovery path for a mistaken cancel since there is no delete. *(PRD §7.2a reconciled: both closed states are read-only and reopenable.)*

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Register a deal as a submission (Priority: P1)

An analyst receives a new broker submission and records it in the workbench as a deal: the cedant, the treaty type, the inception date, and (optionally) the treaty year, the shared-drive directory where files are staged, and a link to the expiring submission it renews. The analyst is recorded as the submission's owner.

**Why this priority**: The submission is the root that every later feature hangs off. Nothing else in the product can exist until a deal can be created and viewed. This is the irreducible MVP slice.

**Independent Test**: Create a submission with the core deal attributes; confirm it persists, is retrievable by its own identity, appears in the analyst's list, and shows all captured attributes on its detail view — with no other feature present.

**Acceptance Scenarios**:

1. **Given** an authenticated analyst, **When** they create a submission with a name, cedant, treaty type, and inception date, **Then** the submission is saved, assigned to that analyst as owner, given a status of ACTIVE, and shown on its detail view with the entered attributes.
2. **Given** a new submission form, **When** the analyst leaves treaty year, directory path, and renewal link blank, **Then** the submission is still created (those fields are optional).
3. **Given** an existing submission, **When** the analyst sets a renewal link to the expiring submission, **Then** the link is stored and the expiring submission is identified on the detail view.
4. **Given** the analyst is typing a cedant name, **When** cedant values already exist on other submissions, **Then** the field offers autocomplete suggestions so the same cedant is spelled consistently (no separate cedant registry is created).

---

### User Story 2 - Find and filter submissions (Priority: P1)

An analyst opens the submission list and, by default, sees only the deals they own ("My Submissions"). They can switch to "All Submissions" to see every deal in the workbench, and narrow either view by cedant, treaty type, or inception.

**Why this priority**: During peak season each analyst owns a deal end-to-end; getting to *their* deals instantly, while still being able to see everyone's, is the core daily interaction. Without it the submission list is unusable at scale.

**Independent Test**: Seed submissions owned by two different analysts; confirm the default view for a given analyst shows only their own, the "All" toggle shows every submission regardless of owner, and each filter (cedant / treaty type / inception) narrows the current view to matching rows only.

**Acceptance Scenarios**:

1. **Given** submissions owned by several analysts, **When** analyst A opens the list, **Then** the default view shows only submissions where A is the owner.
2. **Given** the default "My Submissions" view, **When** analyst A toggles to "All Submissions", **Then** every submission is shown, including those owned by other analysts.
3. **Given** any list view, **When** the analyst filters by cedant, by treaty type, or by inception, **Then** only submissions matching the chosen filter(s) are shown, and filters combine.
4. **Given** the "All Submissions" view, **When** analyst A opens a submission owned by analyst B, **Then** A can view it fully (there is no access restriction by owner).
5. **Given** a submission owned by analyst A, **When** any analyst reassigns its owner to analyst B, **Then** it leaves A's "My Submissions" view and appears in B's, while remaining fully visible to everyone.

---

### User Story 3 - Track a submission's status lifecycle (Priority: P2)

An analyst marks a deal's progress by setting its status to COMPLETED when finished, or CANCELLED when it will not proceed, and can reopen a closed submission (COMPLETED or CANCELLED) back to ACTIVE. Every status change is preserved as history.

**Why this priority**: Status is how analysts and their lead see which deals are live, closed, or dead. It's high-value but depends on a submission already existing (US1), so it sits just below the create/find slices.

**Independent Test**: Create a submission (ACTIVE), move it to COMPLETED, reopen it to ACTIVE, and separately move another to CANCELLED and reopen it as well; confirm each transition is allowed with no precondition, the current status is reflected on the submission, the full change history is retained, and no submission can be deleted.

**Acceptance Scenarios**:

1. **Given** an ACTIVE submission, **When** the analyst sets it to COMPLETED, **Then** the current status becomes COMPLETED and the change is recorded as a history entry — with no system precondition checked (e.g. it is not blocked because work is unfinished).
2. **Given** a COMPLETED submission, **When** the analyst reopens it, **Then** its status returns to ACTIVE and the reopening is recorded as a further history entry.
3. **Given** any submission, **When** the analyst sets it to CANCELLED, **Then** the current status becomes CANCELLED and the change is recorded.
4. **Given** any submission in any status, **When** the analyst looks for a delete action, **Then** none exists — CANCELLED is the "this isn't happening" outcome in place of deletion.
5. **Given** a submission with several past status changes, **When** its history is inspected, **Then** every prior change is still present (no history entry is overwritten or lost).
6. **Given** a COMPLETED or CANCELLED submission, **When** the analyst attempts to edit a field or a CRM tag, **Then** the edit is blocked; the analyst must first reopen it to ACTIVE (reopening is allowed from both COMPLETED and CANCELLED).

---

### User Story 4 - Attach and manage CRM-ID tags (Priority: P2)

An analyst attaches zero or more CRM identifiers to a deal as free-text tags, and can add, edit, or remove them over time. A deal may carry several CRM IDs, or none.

**Why this priority**: CRM IDs are how the business tracks the deal externally, and the July 9 session established that a single CRM field is wrong (one deal can map to several, or none, and they are hand-entered and error-prone). It's important but not on the critical path to a usable deal record, so P2.

**Independent Test**: On an existing submission, add two CRM tags, edit one, remove one, and add a submission with none; confirm the tag set reflects each change and that zero tags is a valid state.

**Acceptance Scenarios**:

1. **Given** a submission with no CRM tags, **When** the analyst adds a CRM identifier, **Then** it appears in the deal's CRM tag set.
2. **Given** a submission with CRM tags, **When** the analyst edits or removes a tag, **Then** the change is reflected and other tags are unaffected.
3. **Given** a new submission, **When** no CRM identifier is entered, **Then** the submission is valid with an empty CRM tag set.
4. **Given** the CRM field, **When** the analyst enters any text, **Then** it is accepted without format validation (identifiers may be mistyped or non-standard by design).

---

### User Story 5 - Coexisting look-alike deals (non-unique identity) (Priority: P3)

Two genuinely distinct deals can share every naming attribute (same cedant, same inception, same treaty type) and differ only by a manual CRM ID. The workbench lets both exist, warning the analyst at create/rename time without ever blocking.

**Why this priority**: This prevents a real peak-season failure mode (a legitimate second deal rejected, forcing label-mangling) but only surfaces in the edge case of true duplicates, so it's P3.

**Independent Test**: Create a submission, then create a second with an identical name and attributes; confirm a non-blocking "a similar deal already exists" warning is shown and the second submission is created anyway with its own identity.

**Acceptance Scenarios**:

1. **Given** an existing submission, **When** the analyst creates or renames another with the same name/attributes, **Then** a non-blocking warning ("a similar deal already exists") is shown and the analyst may proceed.
2. **Given** the duplicate warning, **When** the analyst proceeds, **Then** the second submission is created with its own distinct identity, independent of its name.
3. **Given** two submissions with the same name, **When** either is opened or referenced, **Then** the correct one is resolved by its own identity, not by name.

---

### User Story 6 - Package bundle structure foundation (Priority: P3)

The system can represent a **package**: a bundle of one or more EDM and/or RDM members (any combination), which may be shared across more than one submission. A package must always contain at least one member. This iteration delivers the *structure* only — no package creation, sync, or delete behavior (that is Iteration 2).

**Why this priority**: This is the data foundation Iteration 2's package behavior builds on; delivering it now de-risks the next iteration. It is largely developer-facing this iteration (no analyst-facing package UI yet), so P3.

**Independent Test**: Through the data-access layer and its tests, confirm a package can hold multiple EDM/RDM members, that a package with zero members is rejected by the app-enforced invariant, and that one package can be attached to two submissions.

**Acceptance Scenarios**:

1. **Given** the package structure, **When** a package is created with one or more EDM/RDM members, **Then** it is valid and its members are associated with it.
2. **Given** an attempt to persist a package with no members, **When** the invariant is checked, **Then** the operation is rejected (at least one member is required).
3. **Given** one package, **When** it is associated with two different submissions, **Then** both associations are valid (a package is sharable across deals).
4. **Given** a package that is removed, **When** it is inspected afterward, **Then** it is soft-removed (retained for audit), consistent with the no-hard-delete posture used for submissions.

---

### Edge Cases

- **Look-alike duplicate**: a matching name, *or* a matching cedant + treaty type + inception, is *not* an error — the system warns but creates the second deal (US5, FR-004).
- **Renewal link to a non-active submission**: linking a renewal to a COMPLETED or CANCELLED expiring submission is allowed (the link is a manual historical relationship, not a live-state constraint).
- **Self-referential renewal**: a submission MUST NOT link its renewal to itself; this is prevented.
- **Status set to the current value**: setting a submission to the status it already has is a harmless no-op or a recorded no-change event; it never errors.
- **Concurrent edit**: if two analysts edit the same submission at the same time, the second save MUST NOT silently overwrite the first — the conflict is detected and surfaced so the analyst can reconcile.
- **Empty / whitespace CRM tag**: a blank tag is not stored; non-blank tags are accepted as-is with no format checking; duplicate identical tags on the same deal are permitted (unvalidated by design).
- **Empty package**: a package can never be persisted with zero members (US6).
- **Owner leaves / reassignment**: the assigned analyst is a soft owner for the "My Submissions" filter only; a submission owned by anyone remains fully visible and actionable by every analyst.

## Requirements *(mandatory)*

### Functional Requirements — Submission

- **FR-001**: The system MUST let an authenticated analyst create a submission capturing a name, a cedant, a treaty type, and an inception date, with optional treaty year, shared-drive directory path, and renewal link.
- **FR-002**: The system MUST identify each submission by an identity independent of its name, so that two submissions may have the same name and attributes and still be distinct records.
- **FR-003**: The system MUST NOT enforce uniqueness on the submission name.
- **FR-004**: On create or rename, the system MUST perform a **non-blocking** "a similar deal already exists" check — triggered when another submission shares the same **name**, **or** the same combination of **cedant + treaty type + inception date** — and warn the analyst, while always allowing them to proceed.
- **FR-005**: The system MUST record the creating analyst as the submission's owner (a soft owner for filtering only — see FR-020).
- **FR-005a**: The system MUST allow any analyst to reassign a submission's owner after creation (a deal handoff). Reassignment changes only which analyst's "My Submissions" view the deal appears in; it never changes who may view or act on it.
- **FR-006**: The system MUST support autocomplete of cedant name from values already present on other submissions, without introducing a separate cedant registry.
- **FR-007**: The system MUST allow a submission's renewal link to reference another submission, and MUST prevent a submission from referencing itself as its own renewal.
- **FR-008**: The system MUST treat treaty type as a controlled value drawn from a maintained list of treaty-type kinds (see FR-030).
- **FR-009**: The system MUST present submissions in a master-detail layout: a filterable list plus a detail view showing the submission's attributes.

### Functional Requirements — Status

- **FR-010**: The system MUST support exactly three submission statuses: ACTIVE, COMPLETED, and CANCELLED, with ACTIVE as the status on creation.
- **FR-011**: The system MUST allow reopening a COMPLETED **or CANCELLED** submission to ACTIVE. Neither closed state is a one-way door; because there is no delete (FR-014), reopening is also the recovery path for a mistaken CANCELLED.
- **FR-012**: The system MUST NOT enforce any precondition on a status transition — the analyst decides when a deal is done or withdrawn.
- **FR-013**: The system MUST record every status change as an immutable history entry and reflect the current status on the submission, such that the current status and the full change history are always consistent and no prior entry is lost.
- **FR-014**: The system MUST NOT provide any means to delete a submission; CANCELLED is the "not proceeding" outcome that stands in place of deletion — and, unlike a delete, is reversible by reopening to ACTIVE (FR-011).
- **FR-015**: When a submission is COMPLETED or CANCELLED, the system MUST make it **read-only**: editing its fields, adding/editing/removing CRM tags, and (in later iterations) package create/sync/delete MUST all be blocked. The only permitted actions on a non-ACTIVE submission are viewing and reopening to ACTIVE (from COMPLETED or CANCELLED, FR-011). This gate is enforced this iteration for submission fields and CRM tags; package actions inherit it when they are built (Iteration 2).

### Functional Requirements — CRM tags

- **FR-016**: The system MUST let an analyst attach zero or more CRM identifiers to a submission as a tag set.
- **FR-017**: The system MUST let an analyst add, edit, and remove CRM identifiers on a submission while it is ACTIVE (CRM edits are blocked once the submission is COMPLETED or CANCELLED, per FR-015).
- **FR-018**: The system MUST accept CRM identifiers as free text without format validation, and MUST treat an absent CRM tag set as valid.

### Functional Requirements — Access & filtering

- **FR-019**: The system MUST NOT apply any row-level access restriction: every authenticated analyst MUST be able to view and act on every submission and everything beneath it.
- **FR-020**: The system MUST provide a "My Submissions" list view (submissions owned by the current analyst) as the **default**, with a toggle to an "All Submissions" view.
- **FR-021**: The system MUST let the analyst filter any list view by cedant, by treaty type, and by inception, with filters combining.
- **FR-022**: The system MUST gate *functions* by role (e.g. admin-only maintenance), checked server-side on every request, and MUST NOT use roles to restrict which submissions a user may read or write.

### Functional Requirements — Package structure (schema only)

- **FR-023**: The system MUST represent a package as a bundle of EDM and/or RDM members in any combination (several of each, EDM-only, or RDM-only).
- **FR-024**: The system MUST enforce that a package always has at least one member, as an application-level invariant (membership spans two member types and cannot be expressed by a single column constraint), and this invariant MUST be covered by an automated test.
- **FR-025**: The system MUST allow a package to be associated with more than one submission (a package is sharable across deals), and a submission to have more than one package.
- **FR-026**: The system MUST allow an EDM or RDM record to exist with no package assigned (membership is optional at the record level).
- **FR-027**: The system MUST soft-remove packages (retain for audit) rather than hard-delete them, consistent with the submission no-delete posture.
- **FR-028**: The system MUST NOT implement any package *behavior* this iteration — no shared-drive browse, no name-collision check against Risk Modeler, no create/sync/delete jobs, and no submission-detail package cards (all Iteration 2).
- **FR-029**: The system MUST provide data-access functions for the package structure with accompanying automated tests.

### Functional Requirements — Reference data

- **FR-030**: The system MUST seed the treaty-type list with a candidate set (cat XoL, quota share, surplus, per-risk XoL, aggregate XoL, stop loss), flagged as **pending confirmation with the CIC team** — the authoritative list is an open item, and the seed is a placeholder that can change without a rebuild.

### Functional Requirements — Concurrency

- **FR-031**: When two users edit the same submission concurrently, the system MUST detect the conflict on save and prevent a silent overwrite, surfacing the conflict to the later writer.

### Functional Requirements — Cleanup of dropped Iteration 0 scaffolding (technical)

- **FR-032**: The system MUST remove the customer-isolation and row-level-security scaffolding left over from Iteration 0 (the `customer`, `program`, and `user_customer_access` shell tables, and the generic scope helper and its tests), so that no customer tier and no row-scoping mechanism remains anywhere in the codebase.
- **FR-033**: After cleanup, the schema and data-access layer MUST expose no customer/program/scope constructs, and the Iteration 1 domain schema MUST be folded into the single existing initial migration (dev database is rebuilt drop-create-seed; no new migration is introduced).

### Key Entities

- **Submission** — the deal; the top-level unit of work. Carries a name (a non-unique human label), a cedant, a treaty type, an inception date, an optional treaty year, an optional shared-drive directory path, an optional renewal link to another submission, an owning analyst (soft owner), and a current status. Anchors packages (and, in later iterations, EDMs, RDMs, and jobs). Identified independently of its name.
- **Submission status history entry** — an immutable record of one status change on a submission (from/to, when, by whom). The current status on the submission is kept consistent with this history.
- **CRM ID tag** — a free-text external identifier attached to a submission; zero-to-many per submission; hand-entered and unvalidated.
- **Treaty type (kind)** — a controlled value in a maintained list, used to classify a deal and as a primary filter.
- **Package** — a lightweight bundle of one or more EDM/RDM members that are worked together; sharable across submissions (many-to-many); always non-empty; soft-removed rather than deleted. This iteration defines its structure only.
- **EDM / RDM (records)** — exposure and results entities that may belong to a package. Their tables exist so the membership link can exist; their entity management (import, Risk Modeler integration) is out of scope until Iteration 2.
- **Analyst / role** — the acting user and their function-level role. Roles gate which functions a user may invoke, never which rows they may see.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An analyst can create a new submission and see it in their list within a single short flow (no more than a handful of steps, under ~30 seconds of interaction).
- **SC-002**: The default submission list shows 100% of the current analyst's own deals and 0% owned solely by others; the "All" toggle shows 100% of all deals — verified across a seeded multi-analyst dataset.
- **SC-003**: Each filter (cedant, treaty type, inception) and any combination returns exactly the matching submissions and no others.
- **SC-004**: A submission can move ACTIVE → COMPLETED → ACTIVE (reopen) and ACTIVE → CANCELLED, with every transition succeeding without precondition and every change retained in history (zero history loss across an arbitrary sequence of changes).
- **SC-005**: No workflow exists by which a submission can be deleted; attempting to find one yields no action.
- **SC-006**: Two deals with identical names and attributes can both be created; the create/rename flow shows a warning in 100% of look-alike cases (matching name, or matching cedant + treaty type + inception) and blocks in 0%.
- **SC-007**: A CRM tag set can hold zero, one, or many identifiers, and add/edit/remove each reflect immediately; a submission with zero CRM tags is valid.
- **SC-008**: A package with zero members is rejected 100% of the time; a package can be attached to two submissions; these are proven by automated tests.
- **SC-009**: Concurrent edits to one submission never result in a silent lost update — the later save is either reconciled or reported as a conflict.
- **SC-010**: After the cleanup, no customer-isolation or row-level-security construct remains in the schema, data-access layer, or tests, and every authenticated analyst can view every submission (verified by the absence of the dropped constructs and by an access check across owners).
- **SC-011**: Reassigning a submission's owner moves it out of the prior owner's "My Submissions" view and into the new owner's, with zero change to who can view or act on it.
- **SC-012**: Every edit attempt (field or CRM tag) on a COMPLETED or CANCELLED submission is blocked (100%); the only state-changing action permitted on a non-ACTIVE submission is reopening it to ACTIVE (allowed from both COMPLETED and CANCELLED).

## Assumptions

- **Scope boundary is WORKBENCH state only.** This iteration touches the app's own workbench database (submission/package domain, status, tags, reference data). No EDM/RDM import, no Risk Modeler/IRP calls, no exposure/loss repositories, no analysis, and no search framework — those are later iterations.
- **Package behavior is explicitly deferred.** Only the package *structure* and its data-access layer/tests are in scope; creation, sync, delete, and the submission-detail package cards are Iteration 2, built on this structure.
- **Roles already exist from Iteration 0.** The role model and admin user-management were delivered in Iteration 0; this iteration applies function-level role gating to the new submission functions and adds the "My Submissions" ownership filter. Exact role codes beyond `analyst` and `admin` remain TBD with the team; `analyst`/`admin` are the working default.
- **Treaty-type seed is provisional.** The seeded treaty-type list is a placeholder pending CIC confirmation (FR-030); confirming or changing it is a reference-data edit, not a structural change.
- **Top-level model is provisional (OQ-1/OQ-2).** The submission-as-deal root and flat CRM tags are a build-to-learn choice the CIC team has reopened but not ratified (design note 03), pending wireframe review. It is built as the low-regret shape; a later decision to add a tier or rename the root should not require a rebuild.
- **Inception filtering.** Filtering by inception matches on the inception date (exact date and/or treaty-year grouping); the precise granularity is a UI detail resolved during design, not a scope question.
- **No rate-limit lockout.** Authentication rate-limit/lockout remains deferred from Iteration 0 and is not introduced here.
- **Canonical schema lives in DATA_MODEL §4–§5.** This spec defines observable behavior and constraints; the concrete tables, columns, keys, and the event-sourced status mechanism are derived in the planning phase from DATA_MODEL.md, which is the schema source of truth.
- **Dev database strategy is rebuild.** The Iteration 1 schema is folded into the single existing initial migration; the dev database is dropped, recreated, and seeded (no incremental migration until production cutover).

## Dependencies

- **Iteration 0 (spec 001)** — app shell, navigation manifest, authentication, sessions, roles, and admin user management are in place. This iteration builds submissions into that shell and reuses its role model.
- **DATA_MODEL.md §4–§5** — canonical entity/relationship definitions for submission, package, and the EDM/RDM membership link.
- **CIC team** — authoritative treaty-type list (FR-030) and ratification of the provisional top-level model (OQ-1/OQ-2) are external inputs that can arrive after this iteration and be absorbed without a rebuild.
