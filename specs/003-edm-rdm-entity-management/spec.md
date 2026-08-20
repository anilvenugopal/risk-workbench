# Feature Specification: EDM & RDM Entity Management (incl. Packages) (Iteration 2)

> **Superseded in part:** `specs/006-package-retirement/` removes Package behavior,
> replaces membership with direct Submission associations, and replaces per-EDM
> RDM apply work with one standalone RDM import.

**Feature Branch**: `003-edm-rdm-entity-management`

**Created**: 2026-07-13

**Status**: Draft

**Input**: User description: "the 003 spec, implementing 'Iteration 2' from docs/PRD.md — EDM & RDM entity management (incl. Packages). Scope per docs/PRD.md §8 (file handling at package creation), §9 (EDM/RDM entities, EDM/RDM libraries, Package behavior), §7.4 (submission-detail package cards), §14.3–§14.5 (IRP job submission, poller for import_edm/import_rdm, Dramatiq worker scaffold + notify_analyst), §20.4 (query-string-driven Jobs list filtering), and the §21 build-plan entry 'Iteration 2 — EDM & RDM entity management (incl. Packages)'; schema per DATA_MODEL.md §3–§9. EDM/RDM as first-class importable entities, real package assembly/sync/delete backed by Risk Modeler jobs, package cards on the submission, background job tracking with completion notifications, and a filterable Jobs list — building on the submission + package *structure* delivered in Iteration 1."

## Overview

This iteration makes the workbench actually *do work against Risk Modeler* for the first time. Iteration 1 delivered the deal (submission) and the empty **package structure**; this iteration delivers the behavior that fills it: an analyst imports exposure databases (**EDMs**) and broker results databases (**RDMs**) from the shared drive into Risk Modeler, assembles them into **packages** (bundles worked together), and syncs or deletes those packages against Risk Modeler with real Risk Modeler operations. Because these operations run for minutes on an external platform, the iteration also delivers the surrounding machinery an analyst needs to trust them: a background poller that mirrors each job's status, completion notifications, a filterable Jobs list, and per-package status cards on the submission detail.

This is the first iteration to cross the boundary into Moody's Risk Modeler (IRP). The unit of tracked external work is the **job**: one Risk Modeler operation, submitted on the request path and then followed to completion by a background process — never by blocking a web request.

> **A21 resolved (2026-07-13).** The one design question that gated the *real* sync/delete paths — **how a completed Risk Modeler job triggers the next queued app-side job** across the two job spaces (adversarial-review item A21) — is now resolved: package member operations are queued as app-side work, background workers perform every Risk Modeler call, and the sequence is driven by completion-chaining with idempotent fan-in. The full decision is recorded in **Clarifications → A21 resolution** and specified in the requirements below. The package UI may still be built and demonstrated against short heartbeat stubs first (the stub and the real worker share the same work-item types; only the worker body swaps).

## Clarifications

### Session 2026-07-13

No blocking `[NEEDS CLARIFICATION]` markers were required — the PRD (§8, §9, §14, §7.4, §20.4) and DATA_MODEL.md resolve most behavior in detail, and the one genuinely open design question (A21) was resolved in this session (below). Remaining bounded items are recorded in **Assumptions** and **Dependencies**:

- **Auto-naming token set** — finalized in Iteration 4; EDM/RDM names are analyst-provided (optionally pre-filled from submission context) this iteration.
- **Linking an already-in-Risk-Modeler EDM without re-import** — depends on IRP metadata sync (Iteration 4); out of scope here.
- **Notification channel** — configurable (Teams / email / desktop toast); at least one channel per the notifications configuration.
- **Q: At what granularity are completion/failure notifications delivered?** → **A: Per analyst-initiated action** reaching a terminal state (a standalone import, a package Save-and-Sync, or a package Delete) **plus a notification for any member operation that fails** — never one per successfully-completed member job. A 50-member sync produces a single completion notification (and one notification per failed member), not 50 toasts. **Standalone-import granularity (this iteration):** each EDM/RDM import is its own action, anchored on its own entity id; collapsing a multi-file multi-select into a single "import batch" notification is deferred (no batch identifier is persisted this iteration). SC-003 is read as "100% of terminal *actions* and 100% of *failures*," not one per successful member job.
- **Q: What is the maximum acceptable lag between a Risk Modeler status change and the workbench reflecting it?** → **A: ~15 seconds** — the poller runs a single-status-check pass at a configured interval defaulting to ~15s. This bounds SC-001's "one poll interval," keeps the UI near-live for minutes-long jobs, and stays comfortably within a single-worker poller's capacity for dozens of in-flight jobs.
- **Q: How many automatic retry attempts before a submission failure is parked for manual attention?** → **A: No fixed spec-level default — the cap is a deployment configuration value.** Behaviorally, the submission-retry batch retries a submit-side failure up to that configured limit and then **parks it as terminal `SUBMISSION FAILED`** for analyst-driven recovery (per-member retry / replace-source-file, FR-045–FR-046). The Article-12 retry state-machine test asserts that retries stop once the configured limit is reached, whatever its value.

### A21 resolution — package job chaining (2026-07-13)

The mechanism by which a completed Risk Modeler job triggers the next queued app-side job (and back) is resolved as follows. These decisions drive the requirements in **Package sync/delete orchestration & recovery** below; the concrete tables, kind-table codes, and library method names are derived in planning from DATA_MODEL.md §8.

- **Q: How is the cross-boundary chain driven?** → **Lineage chaining** (the documented DATA_MODEL §8 model). On each Risk Modeler job completion the poller writes a typed follow-on work item keyed to that job (`requestor_type='irp_job'`, `requestor_id=` the finished job); a worker performs the next Risk Modeler submit; the next completion drives the step after it. Fan-**out** (one EDM → several applies) is natural; fan-**in** (an EDM delete waiting on all its RDM deletes; the package soft-delete waiting on all members) is detected by an **idempotent "are all siblings terminal?" query guarded by an atomic status transition** — no dependency counter column.
- **Q: Where does the Risk Modeler submit happen for member ops?** → **All member operations run as app-side work items.** The Save-and-Sync / Delete request only records the initial pending work items and returns immediately; background workers perform *every* Risk Modeler submit (upload, apply, delete). This keeps a large package (50+ members) off the request path, and makes the "build against 60-second heartbeat stubs, then wire real Risk Modeler" swap a change to the worker body alone. (Article 11 permits — does not require — request-path submit; deferring these batch/dependent submits to workers is within it.)
- **Q: How does the analyst recover from a partway failure?** → **All of: idempotent whole-package re-sync, per-member retry, and replace-the-source-file-and-retry.** Re-running Save-and-Sync skips members already imported/applied and re-submits only unstarted/errored ones; a failed member also exposes a per-member retry on its card; and — the expected most-common case — the analyst can **replace the failed member's source file** (a bad/incomplete broker `.bak`) by re-browsing the shared drive and retrying against the new file. Submit-side failures (never reached Risk Modeler) continue to be retried automatically by the single-threaded submission-retry batch.
- **Delete crosses the same boundary, asymmetrically** (confirmed against `irp-integration`): **EDM delete is an asynchronous Risk Modeler job** (pollable, single-status-checked like imports); **RDM delete is synchronous** — an RDM import creates analysis entities rather than a first-class Risk Modeler object, so removing an RDM deletes those entities inline, completing within the worker with no tracked job and no polling. Delete still runs RDM-before-EDM (an EDM removal waits until all the package's RDM removals have succeeded), and the package row is soft-deleted once no live members remain.

### irp-integration reconciliation + scope call (2026-07-14)

`irp-integration` 0.2.0 (the committed PyPI default) was read end-to-end and its Risk-Modeler method surface confirmed; the authoritative method/request-body matrix lives in `contracts/worker-poller.md` → "IRP gateway — confirmed method surface" (research R1 points to it). Decisions:

- **D1 — RDM delete** is `client.analysis.delete_analysis(id)` per analysis — **synchronous, no tracked job** (confirms the A21 asymmetry; the earlier "resolve analyses by `rdmName`" named the wrong field).
- **D2 — Enumeration** is via local `irp_analysis` rows, captured at `import_rdm` completion by a `backfill_rdm_analyses` worker calling `search_analyses('sourceRdmName="…" AND exposureName="…"')`; delete drives off that table. This **reverses** the earlier "no local analysis tracking" scope note — `irp_analysis` (+ its status kind) is now created this iteration (analysis **counts still render empty**, D5).
- **D3 — Review-only / RDM-only packages are DEFERRED to follow-up.** 0.2.0's `submit_rdm_import_job` requires a target EDM, so standalone-RDM import needs a library change. **This supersedes the review-only language elsewhere in this spec (FR-002, FR-016, US2, US3, SC-004, Edge Cases):** every package this iteration has ≥1 EDM, every RDM apply targets an EDM, and Save-and-Sync rejects an RDM-only package.
- **D4 — Config:** `IRPClient()` reads `RISK_MODELER_BASE_URL` / `RISK_MODELER_API_KEY` / `RISK_MODELER_RESOURCE_GROUP_ID`; EDM import uses `server_name="databridge-1"`. S3 upload uses temporary creds from the RM response — no ambient AWS credentials; the worker host needs S3 egress only.
- **D5 — Analysis counts stay empty on the card.** Although `irp_analysis` rows ARE captured this iteration (D2), the package card's portfolio-summary and analysis counts still render **empty** — the captured rows exist only for delete-enumeration and are not surfaced until a later iteration (FR-023).
- **No `irp-integration` code change is on the Iteration-2 critical path;** deferred/nice-to-have library items are tracked in `docs/IRP_INTEGRATION_FOLLOWUPS.md`.

### US6 (Jobs list + notifications) descoped (2026-07-15)

**User Story 6 — the URL-filtered Jobs list and completion/failure notifications — is deferred out of Iteration 2** and will be picked up in a later iteration. The async machinery it would have surfaced (the poller, the `irp_job`/`rwb_job` tables, completion-chaining/fan-in, and the package-card job counts) is all still built this iteration; what defers is only the *observability surface* layered on top:

- **Deferred requirements:** FR-030, FR-031 (completion/failure notifications) and FR-032–FR-036 (the URL-filtered Jobs list, clearable filter chips, cross-page pre-filtered navigation, live SSE status). The measurable outcomes SC-003 (notification delivery) and SC-008 (Jobs-list URL filter) are deferred with them.
- **Retained (foundational, not US6):** FR-029 and FR-047 — automatic retry of submit-side failures up to the configured limit and parking as terminal `SUBMISSION FAILED` — stay in scope and are completed by the new foundational task **T017a** (tasks.md). Only the *notification* emitted when a row is parked defers with US6.
- **Consequence for US5:** the package-card job counts (FR-023) still compute, but their deep-links (FR-024) point at the pre-existing Iteration-0 Jobs-list placeholder pages rather than a live pre-filtered list — a graceful degradation (a placeholder page, not an error), reconciled when US6 lands.
- **Notification channel** config (Teams/email/desktop) remains defined but unused this iteration.

### Name collision becomes blocking (2026-07-27 — issue #17)

The FR-012 non-blocking collision warning shipped, but irp-integration ≥ 0.2.1 validates name uniqueness inside `submit_edm_import_job` — an overridden warning no longer produces the duplicate the analyst asked for; it produces a worker-side failure minutes later with no useful message (issue #17). Decisions (confirmed with the approver, 2026-07-27):

- **Collision → blocking error at save time** on every surface: standalone `/edms/import` and `/rdms/import`, package-modal Save **and** Save-and-Sync, and package re-sync (which re-checks only members it will actually (re)submit — a `ready` member never self-collides; it *is* that Risk Modeler entity). FR-012 and SC-005 amended; research R8 superseded.
- **Fail open when Risk Modeler is unreachable**: the save proceeds with a visible warning; the worker-side submit validation is the backstop, and its specific failure message is surfaced on the EDM/RDM detail page and the package-card member row.
- **As-you-type validation** (debounced ~500ms, results cached ~30s in-process — issue #11) on the standalone forms and each package-modal member row; a rendered blocking error disables the submit buttons.
- **Delete-the-existing-Risk-Modeler-entity-and-reimport** as a collision remedy is deferred to a follow-up issue.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Import an EDM from a broker file (Priority: P1)

An analyst has a broker exposure file (`.bak`, `.mdf`, or `.csv`) staged on the shared drive. They browse to it, give the resulting EDM a name, and import it into Risk Modeler. The import runs asynchronously; the workbench tracks it and shows the EDM moving from *importing* to *ready* (or *error*), and records the Risk Modeler identifier once the import succeeds.

**Why this priority**: The EDM is the exposure basis for every downstream operation (portfolios, analyses, results). Nothing in Phase B or C can happen until an EDM can be imported and reach a ready state. This is the irreducible MVP slice of the iteration.

**Independent Test**: Point the browse at a shared-drive directory, select one exposure file, name and import it; confirm a tracked import job is created, its status is mirrored from Risk Modeler without any web request blocking, and on success the EDM shows *ready* with a Risk Modeler identifier recorded — with no package or RDM involved.

**Acceptance Scenarios**:

1. **Given** an authenticated analyst browsing the shared drive, **When** they select a `.bak`/`.mdf`/`.csv` exposure file, name it, and choose Import, **Then** an EDM record is created in *pending import*/*importing* and an import job is submitted to Risk Modeler and tracked.
2. **Given** a submitted EDM import, **When** the background poller checks Risk Modeler, **Then** the tracked job's status is mirrored (e.g. *running* → *finished*) without blocking any web request, and on success the EDM becomes *ready* with its Risk Modeler identifier recorded.
3. **Given** an EDM import that Risk Modeler reports as failed, **When** the poller observes the terminal failure, **Then** the EDM shows *error* and the failure is surfaced to the analyst.
4. **Given** a directory with several exposure files, **When** the analyst multi-selects more than one file, **Then** all selected files are imported together, each producing its own EDM record and tracked job.
5. **Given** an import that never reaches Risk Modeler (submission fails), **When** the failure occurs, **Then** the job is marked as a submission failure (distinct from a Risk-Modeler-side failure) and is eligible for automatic retry.
6. **Given** an EDM import that failed because its source file was bad or incomplete, **When** the analyst selects a replacement file from the shared drive and retries, **Then** the import is re-submitted against the new file and the recorded source file path is updated.

---

### User Story 2 - Import an RDM (broker results) from a file (Priority: P1)

An analyst imports a broker-supplied results database (RDM) so the broker's own analyses can be reviewed and later compared. The RDM is applied to the EDM(s) it belongs with. *(Importing an RDM review-only, with no exposure to run against, is deferred — D3, 2026-07-14.)* As with EDMs, the import is tracked to completion.

**Why this priority**: Broker results are half of the comparison the workbench exists to support, and RDM import shares all the import/tracking machinery with EDM import, so it belongs in the same critical slice.

**Independent Test**: Import an RDM applied to a ready EDM; confirm it produces a tracked import job that reaches a terminal state, one apply per applied EDM, and (on FINISHED) captured `irp_analysis` rows for later delete-enumeration (D2). *(The review-only / no-EDM case is deferred — D3, 2026-07-14.)*

**Acceptance Scenarios**:

1. **Given** a ready EDM, **When** the analyst imports an RDM applied to it, **Then** an RDM record is created, an import job is submitted and tracked, and on success the RDM becomes *ready*.
2. *(Deferred — D3, 2026-07-14; review-only import needs a library change, 0.2.0 requires a target EDM.)* **Given** no EDM to apply to, **When** the analyst imports an RDM review-only, **Then** the RDM import still succeeds and the broker's analyses exist for review (with no owning EDM required).
3. **Given** an RDM applied across more than one EDM, **When** it is imported, **Then** the broker's results are treated as a single source (not duplicated per EDM) while still being reachable from each EDM it was applied to.
4. **Given** an RDM import reported as failed, **When** the poller observes it, **Then** the RDM shows *error* and the analyst is informed.

---

### User Story 3 - Assemble a package and sync it to Risk Modeler (Priority: P1)

An analyst groups the files that arrived and are worked together into a **package** — any combination of one or more EDMs and/or RDMs. In a package modal they browse the shared drive, multi-select the member files, name each member, and (once satisfied) choose **Save and Sync**, which runs the real Risk Modeler work: each EDM is uploaded and each RDM is applied across the EDMs in the bundle.

**Why this priority**: The package is how real broker submissions actually arrive ("take all five of these and move them"), and syncing a package is the point at which the workbench does the analyst's most repetitive, error-prone manual work for them. It is the headline capability of the iteration.

**Independent Test**: Create an EDM-only, an RDM-only, and an EDM+RDM package by browsing and multi-selecting files; confirm the ≥1-member rule holds, that an RDM-only package can be assembled/saved but its **Save-and-Sync is rejected (`EmptyPackageError`, D3)**, a member name that already exists in Risk Modeler blocks Save/Save-and-Sync with an error naming the member *(amended 2026-07-27 — issue #17)*, and Save-and-Sync on a both-package queues the correct set of member jobs with each RDM apply waiting for its target EDM's upload.

**Acceptance Scenarios**:

1. **Given** the package modal, **When** the analyst multi-selects member files from the shared drive, **Then** they form a bundle of one or more EDM and/or RDM members in any combination (EDM-only, RDM-only, or both) — though an RDM-only bundle's Save-and-Sync is deferred this iteration (D3).
2. *(Amended 2026-07-27 — issue #17.)* **Given** a member name the analyst is editing, **When** that name already exists in Risk Modeler, **Then** the name field is flagged as-you-type with a blocking collision error, Save/Save-and-Sync are disabled until the member is renamed or removed, and a submitted save is rejected with an error naming the member. When Risk Modeler is unreachable the check fails open — an amber warning shows and the save proceeds.
3. **Given** a package with at least one member, **When** the analyst chooses Save, **Then** the package and its member names are persisted and the blocking collision check runs first (a hit rejects the save), but nothing is submitted to Risk Modeler.
4. **Given** a saved package with EDM and RDM members, **When** the analyst chooses Save and Sync, **Then** one upload job per EDM and one apply job per (EDM × RDM) pair are queued as real Risk Modeler jobs, and each RDM apply waits only for its target EDM's upload to finish (applies fan out per pair, not behind a single global step).
5. *(Deferred — D3, 2026-07-14.)* An RDM-only package (RDM, no EDM) cannot be synced this iteration; Save-and-Sync rejects it (0.2.0 requires a target EDM).
6. **Given** an attempt to sync a package with no members, **When** the invariant is checked, **Then** the sync is rejected (at least one member is required).
7. **Given** the browse dialog opened for a submission that has a directory path, **When** the analyst starts browsing, **Then** the browse location is seeded from that directory path.
8. **Given** a package the analyst chooses to Save and Sync, **When** they confirm, **Then** the request records the member work and returns immediately (the work is queued), rather than holding the request open while Risk Modeler runs.
9. **Given** a sync in which one member's import failed, **When** the analyst re-runs Save and Sync, **Then** members already imported/applied are skipped and only the unstarted or failed members are re-submitted (Save and Sync is safe to repeat).
10. **Given** a member whose import failed because its source file was bad or incomplete, **When** the analyst re-browses the shared drive, selects a replacement file for that member, and retries, **Then** the member's stored source file is updated and the import is re-submitted against the new file.
11. **Given** a single failed member, **When** the analyst uses that member's retry control on the package card, **Then** only that member's operation is re-submitted, without re-running the rest of the package.

---

### User Story 4 - Delete a package and its Risk Modeler members (Priority: P2)

An analyst removes a package that was created in error or is no longer needed. Delete runs the real Risk Modeler removal in the reverse order of sync — broker results (RDM) first, exposure (EDM) last — and, once the last member is removed, soft-deletes the members and the package (retained for audit; never hard-deleted).

**Why this priority**: Deletion is essential for correcting mistakes against a live external platform, but it is a lower-frequency corrective action than import/sync, so it sits just below the create/sync slices.

**Independent Test**: Sync a both-package, then delete it; confirm the removals run in RDM-before-EDM order, that deleting an EDM cascades to its analyses while deleting an RDM removes only the broker analyses it created, and that on completion the members and the package row are soft-deleted with nothing hard-deleted.

**Acceptance Scenarios**:

1. **Given** a synced both-package, **When** the analyst chooses Delete, **Then** real Risk Modeler removals are queued in reverse order — RDM removals (synchronous) before EDM removals (asynchronous jobs).
2. **Given** an EDM being deleted, **When** its delete completes, **Then** its exposure database and the analyses on it (own and broker) are removed; **Given** an RDM being deleted, **When** its delete completes, **Then** only the broker analyses that RDM created are removed.
3. **Given** the last member removal succeeding, **When** it completes, **Then** the affected EDMs/RDMs and the package row are soft-deleted (retained for audit), consistent with the no-hard-delete posture.
4. **Given** any package in the system, **When** the analyst looks for a way to permanently erase it, **Then** none exists — removal is always a soft-delete.

---

### User Story 5 - See packages on the submission via package cards (Priority: P2)

On a submission's detail view, the analyst sees one full-width card per package showing the state that matters at a glance: upload progress, the EDM and RDM status chips side by side, the source file paths the members came from, and job counts (all / active / failed) for that package, with the counts linking through to a pre-filtered Jobs list.

**Why this priority**: The card is the analyst's day-to-day window into where each package stands. It depends on import and package behavior existing (US1–US4), so it follows them, but it is what makes that behavior legible.

**Independent Test**: On a submission with a synced package, confirm the card shows upload progress, both status chips, the source file paths, and the correct all/active/failed job counts, and that clicking a count opens the Jobs list already filtered to that package.

**Acceptance Scenarios**:

1. **Given** a submission with packages, **When** the analyst opens its detail view, **Then** each package is shown as its own full-width card (not a compact tile).
2. **Given** a package card, **When** it renders, **Then** it shows upload progress, the member EDM status chip and RDM status chip, and the source file path(s) the members were created from.
3. **Given** a package card, **When** it renders its job counts, **Then** it shows all / active / failed counts scoped to that package's members, and each count links to the Jobs list pre-filtered to that package.
4. **Given** a package card, **When** portfolio summary and analysis counts have no data yet (later iterations), **Then** those areas render as empty rather than as errors.
5. **Given** a submission that is COMPLETED or CANCELLED, **When** the analyst views its package cards, **Then** package create/sync/delete actions are blocked (read-only), inheriting the submission status gate; only viewing is permitted until it is reopened.

---

### User Story 6 - Monitor and filter jobs, and be notified on completion (Priority: P2) — DESCOPED (2026-07-15)

> **Deferred out of Iteration 2 (2026-07-15).** The underlying async machinery (poller, `irp_job`/`rwb_job` tables, completion-chaining, package-card job counts) is still built this iteration; only the observability surface — the filterable Jobs list and completion/failure notifications — is deferred to a later iteration. FR-030–FR-036, SC-003, and SC-008 are deferred with it. See Clarifications → "US6 (Jobs list + notifications) descoped". The story text below is retained for when it is picked up.

An analyst tracks all the asynchronous work in one place: a Jobs list whose filters live entirely in the URL, so a filtered view can be linked to, bookmarked, and navigated back to. Job status updates appear live as the poller advances them, and the analyst receives a notification when a job completes or fails.

**Why this priority**: Once real jobs are running, an analyst needs a trustworthy, shareable view of their state and a push when something finishes or breaks — otherwise the async model is opaque. It builds on jobs existing (US1–US4).

**Independent Test**: Open the Jobs list, apply filters via the URL (submission, package, status, job type), confirm the list reflects exactly the matching jobs and shows active-filter chips; follow a package card's job-count link and confirm it lands pre-filtered; complete a job and confirm a notification is delivered.

**Acceptance Scenarios**:

1. **Given** the Jobs list, **When** the analyst applies filters (by submission, package, status, or job type), **Then** the active filters are read from the URL query string on every load (full page or partial swap) and the list shows exactly the matching jobs plus clearable active-filter chips.
2. **Given** a package card's job-count link, **When** the analyst clicks it, **Then** the Jobs list opens pre-filtered to that package, and the address bar reflects the filter so refresh, bookmark, and back/forward all preserve it.
3. **Given** a job whose status changes, **When** the poller advances it, **Then** the Jobs list reflects the new status live (without a manual refresh).
4. **Given** an analyst action (a standalone import, package sync, or delete) that reaches a terminal state, or any member operation that fails, **When** the terminal status is observed, **Then** the analyst receives a notification on the configured channel (Teams / email / desktop) — one per action and one per failure, not one per successfully-completed member job.
5. **Given** filters that do not apply to a particular list, **When** they are present in the URL, **Then** that list accepts the subset it understands and ignores the rest (shared filter-param vocabulary).

---

### User Story 7 - Browse the global EDM and RDM libraries (Priority: P3)

An analyst opens the EDM library (or RDM library) to see every EDM (or RDM) tracked in the workbench, across all submissions, regardless of who owns the deal — and can start a new import from there and check import job status.

**Why this priority**: The libraries give a cross-submission, whole-workbench view and a second entry point for import, but the primary import and package flows (US1–US4) already deliver the core value, so the libraries are P3.

**Independent Test**: Seed EDMs/RDMs under submissions owned by different analysts; confirm the EDM and RDM libraries each list all of them for any analyst (no scoping), expose an import entry point, and show each entity's import job status.

**Acceptance Scenarios**:

1. **Given** EDMs/RDMs created under different submissions and owners, **When** any analyst opens the EDM or RDM library, **Then** all of them are listed (no row-level scoping; every analyst sees all entities).
2. **Given** a library view, **When** the analyst chooses to import, **Then** the same import flow (browse, name, submit, track) is available from the library.
3. **Given** a library view, **When** an entity has an in-flight or finished import, **Then** its import job status is visible in the list.

---

### Edge Cases

- **Empty package**: a package can never be synced or persisted with zero members (app-enforced invariant; US3).
- **Name collision override**: a member name that already exists in Risk Modeler warns but never blocks Save/Sync — the analyst may proceed or rename (US3).
- **Review-only RDM (no EDM)** — **deferred to follow-up (D3, 2026-07-14)**: 0.2.0 requires a target EDM, so RDM-only packages are rejected at Save-and-Sync this iteration.
- **RDM applied across multiple EDMs**: broker results are one logical source, retrieved/stored once rather than duplicated per EDM, while remaining reachable from each EDM (US2).
- **Per-pair sync ordering**: each RDM apply waits only for *its* target EDM's upload; independent EDMs and applies proceed in parallel — there is no single global "EDM head job" gating everything (US3).
- **Delete ordering**: delete reverses sync — RDM removals before EDM removals; deleting an EDM cascades to its analyses, deleting an RDM removes only the broker analyses it created (US4).
- **Submission failure vs Risk-Modeler failure**: a job that never reached Risk Modeler (submission failure, no Risk Modeler id) is distinct from one Risk Modeler ran and failed — different cause, different retry path (US1).
- **Partway sync failure**: when a member's import fails, its dependents do not fire and the package is left partially synced; the analyst recovers by re-running Save-and-Sync (idempotent — skips ready members), retrying the single member, or replacing that member's source file and retrying (FR-044–FR-046).
- **Duplicate/repeated completion trigger**: a completion observed more than once (poller re-poll, worker redelivery, reconciler re-enqueue) MUST NOT double-submit the next operation or advance a fan-in prematurely — chaining and fan-in are idempotent (FR-043).
- **Closed submission**: package create/sync/delete are blocked on a COMPLETED or CANCELLED submission until it is reopened (inherits the Iteration 1 status gate; US5).
- **Delete-after-transfer**: an optional per-import choice may delete an app-created temporary file after its data is transferred; the read-only shared drive and broker files are never touched.
- **Concurrent name edit**: two analysts editing the same EDM/RDM name or the same package at once must not silently overwrite each other; the conflict is detected and surfaced (US3/US6).
- **Long-running import**: an import that runs for many minutes must not block any web request and must continue to be tracked; the poller checks status once per pass and never waits for completion inline.

## Requirements *(mandatory)*

### Functional Requirements — EDM & RDM entities and import

- **FR-001**: The system MUST let an authenticated analyst import an EDM by selecting an exposure file (`.bak`, `.mdf`, or `.csv`) from the shared drive, naming it, and submitting it to Risk Modeler, creating a tracked EDM record.
- **FR-002**: The system MUST let an analyst import an RDM (broker results) applied to one or more EDMs, creating the broker analyses in Risk Modeler. *(Review-only import with no EDM is **deferred** — D3, 2026-07-14; `irp-integration` 0.2.0 requires a target EDM.)*
- **FR-003**: The system MUST allow the analyst to name an EDM/RDM (optionally pre-filled from submission context) and edit that name before submission; a system-generated naming scheme is not required this iteration (auto-naming tokens are finalized in a later iteration).
- **FR-004**: The system MUST track each asynchronous import as a job with an observable, externally-mirrored status, and MUST expose the EDM/RDM lifecycle states: *pending import*, *importing*, *ready*, *error*, *delete pending*, *deleted*.
- **FR-005**: The system MUST record the source file path each EDM/RDM was created from (a single path string, no versioning).
- **FR-006**: The system MUST back-fill each EDM/RDM's Risk Modeler identifier when its import completes successfully.
- **FR-007**: The system MUST support multi-selecting several files from one directory and importing them together, each producing its own EDM/RDM record and tracked job.
- **FR-008**: The system MUST support an optional "delete after transfer" choice that removes an app-created temporary file once its data has been transferred, and MUST NEVER write, move, or delete broker files on the read-only shared drive.
- **FR-009**: The system MUST treat the shared drive as read-only and present browsing as a live directory listing (no cached/scanned inventory to reconcile).

### Functional Requirements — Package assembly & sync

- **FR-010**: The system MUST let an analyst assemble a package as a bundle of one or more EDM and/or RDM members in any combination (several of each, EDM-only, or RDM-only). *(An RDM-only package MAY be assembled/saved, but its Save-and-Sync is rejected this iteration — D3/FR-016.)*
- **FR-011**: The system MUST let the analyst build a package by browsing the shared drive and multi-selecting the member files, with no separate prior tagging or file-registration step; the browse location MUST be seedable from the submission's directory path.
- **FR-012**: *(Amended 2026-07-27 — issue #17.)* The system MUST check each EDM/RDM member name against Risk Modeler and, on a collision, **block Save and Save-and-Sync** with an error naming the affected member(s) — nothing is persisted or submitted until the analyst renames. The same check MUST run as-you-type (debounced) so the collision surfaces before the action. When Risk Modeler cannot be reached the check **fails open**: the save proceeds with a visible warning and the worker-side submit validation (irp-integration ≥ 0.2.1) is the backstop, whose specific failure message MUST be surfaced on the affected member. *(The original non-blocking-warning behavior is superseded — see Clarifications 2026-07-27 and research R8.)*
- **FR-013**: The system MUST provide the package actions Cancel, Save, Save-and-Sync, and Delete.
- **FR-014**: On Save, the system MUST persist the package and its member names and run the collision check, and MUST NOT submit anything to Risk Modeler.
- **FR-015**: On Save-and-Sync, the system MUST perform **real** Risk Modeler work — one upload per EDM plus one apply per (EDM × RDM) pair in the bundle — with per-pair ordering such that each RDM apply waits only for its target EDM's upload to succeed (applies fan out per pair; there is no single global EDM head job). The submission mechanism is specified in FR-042–FR-043.
- **FR-016**: *(Deferred — D3, 2026-07-14.)* Review-only sync (a single apply with no EDM for an RDM-only package) is **out of scope this iteration** — the library requires a target EDM. Save-and-Sync MUST reject an RDM-only package until this lands.
- **FR-017**: The system MUST enforce, as an application-level invariant, that a package always has at least one member, and MUST reject any attempt to sync or persist an empty package.
- **FR-018**: The system MUST NOT maintain an independent package status; the package card MUST display the member EDM status chip and RDM status chip rather than a rolled-up package status.

### Functional Requirements — Package delete

- **FR-019**: On Delete, the system MUST perform **real** Risk Modeler removals in the reverse order of sync — RDM removals before EDM removals — such that an EDM removal is not submitted until every RDM removal it depends on has succeeded. **EDM removals are asynchronous jobs** (polled with a single-status check like imports); **RDM removals are synchronous** (they delete the analysis entities the RDM created and complete inline, with no tracked job).
- **FR-020**: The system MUST make deleting an EDM remove its exposure database and cascade to the analyses on it (own and broker), and make deleting an RDM remove only the broker analyses that RDM created across EDMs.
- **FR-021**: When no live members remain, the system MUST soft-delete the affected EDMs/RDMs and the package row (retained for audit), detecting completion idempotently (FR-043), and MUST provide no hard-delete of a package.

### Functional Requirements — Submission-detail package cards

- **FR-022**: The system MUST show, on a submission's detail view, one full-width card per package (not a compact grid).
- **FR-023**: Each package card MUST display: upload progress; the member EDM status chip and RDM status chip; the source file path(s) the members were created from; a portfolio summary and analysis counts that render empty for now (analyses are captured internally for delete-enumeration this iteration — D2 — but the counts still render empty per D5; populated in later iterations); and IRP/RWB job counts (all / active / failed) scoped to that package's members.
- **FR-024**: Each package card's job count MUST link to the Jobs list pre-filtered to that package.
- **FR-025**: The system MUST block package create/sync/delete actions when the owning submission is COMPLETED or CANCELLED (inheriting the Iteration 1 read-only status gate), permitting only viewing until it is reopened to ACTIVE.

### Functional Requirements — Job tracking, poller & notifications

- **FR-026**: The system MAY submit Risk Modeler operations synchronously on the request path (Article 11 permits it), but this iteration defers **all** submits — EDM/RDM imports and package member operations alike — to background workers (FR-042); it MUST perform all status polling and post-completion result work in background processes, never in a web request handler.
- **FR-027**: The system MUST poll each in-flight job with a single-status-check per pass, at a configured interval defaulting to ~15 seconds, and MUST NEVER use a blocking poll-to-completion call anywhere.
- **FR-028**: The system MUST mirror each job's Risk Modeler status onto the tracked job (updated in place) and detect terminal states, treating only a "finished" terminal state as success and other terminal states as failure.
- **FR-029**: The system MUST distinguish a submission failure (the operation never reached Risk Modeler, no Risk Modeler id) from a Risk-Modeler-side failure, and MUST make submission failures eligible for automatic retry up to a configured limit. The limit is a deployment configuration value with **no fixed default**; when it is reached the operation MUST be parked as terminal `SUBMISSION FAILED` for analyst-driven recovery (FR-045–FR-046), and the mandated retry state-machine test asserts that retries stop at the configured limit whatever its value.
- **FR-030**: *(Deferred — US6 descope, 2026-07-15.)* The system MUST notify the analyst on a configurable channel (Teams / email / desktop toast), delivered from a background worker, when an **analyst-initiated action** reaches a terminal state — a standalone import, a package Save-and-Sync, or a package Delete completing — and whenever **any member operation fails**. It MUST NOT emit a separate notification for each successfully-completed member job, so a multi-member package sync yields one completion notification (plus one per failed member), not one per member. A **standalone import** is anchored per imported entity this iteration; grouping a multi-file import into a single notification is deferred (no batch id is persisted).
- **FR-031**: *(Partially deferred — US6 descope, 2026-07-15: the completion-notification worker defers; the real-Risk-Modeler-backed member ops below stay in scope and are built.)* The system MUST provide a background worker scaffold with a working completion-notification worker, and MUST back the package member upload/apply/delete operations with real Risk Modeler jobs (the UI MAY first be built against short heartbeat stubs and wired to real Risk Modeler within this iteration).

### Functional Requirements — Jobs list & filtering

> *(Entire section — FR-032 through FR-036 — deferred out of Iteration 2 with US6; see Clarifications → "US6 (Jobs list + notifications) descoped". Retained here for the later iteration that builds it.)*

- **FR-032**: The system MUST provide a Jobs list whose active filters are read from the URL query string on every request (full page load or partial swap), using the same code path for both.
- **FR-033**: The system MUST support a shared, fixed filter-param vocabulary across filterable lists — at minimum submission, package, status, and job type — where each list accepts the subset that applies to it and ignores the rest.
- **FR-034**: The Jobs list MUST render clearable active-filter chips so the analyst can see and remove what is applied.
- **FR-035**: The system MUST support cross-page pre-applied filter navigation (e.g. a package card's job-count link opening a pre-filtered Jobs list), with the address bar reflecting the filter so refresh, deep-link, bookmark, and back/forward all preserve it.
- **FR-036**: The Jobs list MUST reflect job status changes live as the poller advances them.

### Functional Requirements — EDM & RDM libraries

- **FR-037**: The system MUST provide global EDM library and RDM library destinations that list every EDM/RDM across all submissions to every authenticated analyst (no row-level scoping).
- **FR-038**: The libraries MUST provide an import entry point (the same browse/name/submit/track flow) and MUST show each entity's import job status.

### Functional Requirements — Concurrency

- **FR-039**: The system MUST apply optimistic concurrency to analyst-editable EDM/RDM name edits and to package edits, detecting a conflicting concurrent change on save and surfacing it rather than silently overwriting.

### Functional Requirements — Foundation & data (technical carryover)

- **FR-040**: The system MUST build EDM/RDM entity management on the package *structure* delivered in Iteration 1 (the package / submission↔package relationship and the EDM/RDM package-membership link), adding the entity behavior and any additional entity fields needed this iteration, folded into the single existing initial migration (dev database is rebuilt drop-create-seed; no incremental migration introduced).
- **FR-041**: The system MUST NOT introduce customer/program tiers or any row-level access restriction on EDMs, RDMs, packages, or jobs; ownership reaches a submission transitively through the package, and every authenticated analyst can view and act on every entity.

### Functional Requirements — Package sync/delete orchestration & recovery (A21)

- **FR-042**: The system MUST run package member operations (EDM upload, RDM apply, EDM/RDM removal) as queued app-side work: the Save-and-Sync / Delete request records the initial pending work items and returns immediately, and **every** Risk Modeler call for these operations — whether an asynchronous job submit or the synchronous RDM removal — MUST be performed by a background worker, never by the web request handler.
- **FR-043**: The system MUST drive the member-operation sequence by completion-chaining — a Risk Modeler job's completion (or, for the synchronous RDM removal, the worker's success) triggers the next dependent operation — and MUST NOT submit any dependent operation until every operation it depends on has reached a successful terminal state (an RDM apply waits for its target EDM's upload; an EDM removal waits for all its RDM removals; the package soft-delete waits for all members). This dependency fan-in MUST be detected idempotently, so that duplicate or repeated triggers cannot double-submit or advance prematurely.
- **FR-044**: The system MUST make Save-and-Sync idempotent: re-running it on a package MUST skip members already imported/applied and re-submit only members that are unstarted or in an error state, and MUST be safe to invoke repeatedly.
- **FR-045**: The system MUST provide a per-member retry, invocable from the package card, that re-submits only the failed member's operation without re-running the rest of the package.
- **FR-046**: The system MUST let the analyst replace the source file of a failed EDM/RDM import (in a package or standalone) — re-browsing the shared drive and selecting a different file — and retry the import against the new file, updating the recorded source file path accordingly. This is the expected primary remedy for a bad or incomplete broker file.
- **FR-047**: The system MUST continue to retry submit-side failures (operations that never reached Risk Modeler) automatically via the single-threaded submission-retry batch, up to the FR-029 configured limit — after which the operation is parked as terminal `SUBMISSION FAILED` — independent of the analyst-driven recovery in FR-044–FR-046.
- **FR-048**: The system MUST support building the package sync/delete UI against short heartbeat stubs first and then wiring the same work-item types to real Risk Modeler within this iteration, without changing the orchestration shape (stub and real differ only in the worker body).

### Key Entities

- **EDM (record)** — an exposure database as it exists in Risk Modeler, distinct from the source file that produced it. Carries a name, a Risk Modeler identifier (back-filled on import success), a lifecycle status, the source file path, package membership, and last-confirmed-against-Risk-Modeler trust signals. Ownership reaches a submission through its package.
- **RDM (record)** — a broker-supplied results database tracked in Risk Modeler. Carries a name, a Risk Modeler identifier, a lifecycle status, the source file path, and package membership. Has no single owning EDM — a broker RDM is applied across every EDM in its bundle. *(Review-only / no-EDM RDMs are deferred — D3, 2026-07-14.)*
- **Package** — a lightweight bundle of one or more EDM/RDM members worked together, created/named/synced/deleted as a unit, sharable across submissions, always non-empty, soft-removed rather than deleted, with no independent status of its own.
- **Job (tracked external operation)** — one *asynchronous* Risk Modeler operation followed to completion by the background poller: an EDM import, an RDM apply, or an EDM removal. Has a type (which determines how it is polled), a mirrored status, and a Risk Modeler identifier once submitted. (An **RDM removal** is synchronous — it deletes the analysis entities the RDM created — so it is performed by a worker but is **not** a tracked, polled job.)
- **App-side work item** — background work this app performs on analyst request or after a Risk Modeler job completes (e.g. submit the next member operation, deliver a notification), executed by a background worker and decoupled from the external job. Its trigger is recorded as either an analyst request or a specific completed job, which is also the idempotency/dedup key that makes completion-chaining and fan-in safe against repeats.
- **Notification** — a completion/failure message delivered to the analyst on a configured channel.
- **Shared-drive file selection** — a live, read-only browse of the mounted shared drive from which the analyst multi-selects member files; the selected path is stored on the resulting EDM/RDM (there is no file inventory).
- **Jobs-list filter** — the URL-query-string state (submission / package / status / job type) that defines a filtered Jobs view as a pure function of the URL.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An analyst can import an EDM from a shared-drive file and see it reach *ready* without ever manually polling; the tracked status reflects Risk Modeler within one poll interval (target ≤ ~15 seconds).
- **SC-002**: No web request blocks on a Risk Modeler import or on polling — imports that run for minutes are tracked entirely by background processing, verified by importing while the UI stays responsive.
- **SC-003**: *(Deferred — US6 descope, 2026-07-15.)* A completion or failure notification is delivered on the configured channel for 100% of terminal analyst actions (standalone import / package sync / package delete) and for 100% of member failures; a successful multi-member sync produces a single completion notification rather than one per member job.
- **SC-004**: Both **syncable** package shapes — EDM-only and EDM+RDM — can be created and synced by browsing and multi-selecting files from the shared drive. *(An RDM-only package may be assembled/saved, but its Save-and-Sync is rejected this iteration — D3, 2026-07-14.)*
- **SC-005**: *(Amended 2026-07-27 — issue #17.)* With Risk Modeler reachable, a colliding member name blocks Save/Sync in 100% of cases (an error names the member; nothing persists). With Risk Modeler unreachable, 100% of saves proceed fail-open with a visible warning, and a worker-side collision failure surfaces its specific Risk Modeler message on the member.
- **SC-006**: Save-and-Sync on an EDM+RDM package produces exactly one upload job per EDM and one apply job per (EDM × RDM) pair, with each apply starting only after its target EDM's upload succeeds — verified against the queued job set.
- **SC-007**: Delete on a synced package runs member removals in RDM-before-EDM order and, on completion, soft-deletes the members and the package with zero hard-deletes.
- **SC-008**: *(Deferred — US6 descope, 2026-07-15.)* Clicking a package card's job count lands on the Jobs list pre-filtered to that package, and refresh, bookmark, and browser back/forward all preserve that filter (filter state is fully carried in the URL).
- **SC-009**: The EDM and RDM libraries each show 100% of entities across all submissions to every analyst, independent of deal ownership.
- **SC-010**: Concurrent edits to the same EDM/RDM name or the same package never produce a silent lost update — the later save is reconciled or reported as a conflict.
- **SC-011**: Package create/sync/delete actions are blocked on a COMPLETED or CANCELLED submission 100% of the time, and become available again after it is reopened to ACTIVE.
- **SC-012**: A package with zero members is rejected for sync/persist 100% of the time.
- **SC-013**: A failed member can be recovered without rebuilding the package — by replacing its source file and retrying, by retrying the single member, or by re-running Save-and-Sync — and re-running Save-and-Sync never re-submits a member already imported/applied (idempotent in 100% of repeats).
- **SC-014**: No package member operation is submitted to Risk Modeler from a web request handler (all member submits occur in background workers), and a repeated completion trigger never causes a double-submit or a premature fan-in advance.

## Assumptions

- **Builds on Iteration 1 structure.** The submission, the package/`submission_package` relationship, the EDM/RDM package-membership link, the submission status gate (§7.2a), and optimistic concurrency were delivered in Iteration 1 (spec 002); this iteration adds EDM/RDM entity management and real package behavior on top and does not redefine that structure.
- **A21 is resolved (2026-07-13).** The cross-boundary chaining mechanism is decided (see Clarifications → A21 resolution) and specified in FR-042–FR-048: member ops run as queued app-side work, workers perform every Risk Modeler submit, completion-chaining with idempotent fan-in drives the sequence, and recovery is idempotent re-sync + per-member retry + source-file replacement. The package UI may still be built against short heartbeat stubs first and wired to real Risk Modeler within this iteration (same work-item types; only the worker body swaps).
- **Auto-naming is deferred.** The auto-naming token set (built from the deal's attributes) is finalized in Iteration 4; this iteration uses analyst-provided names, optionally pre-filled from submission context.
- **Linking an existing Risk Modeler EDM without re-import is out.** That entry point depends on IRP metadata sync (Iteration 4) and is not built here; this iteration's libraries cover import and status only.
- **Phase A is out.** DataBridge validation, exposure profiling, exposure modification, and the Exposure Repository write (§10, §16.5) are not in scope this iteration.
- **Search framework is out.** The global command-palette search (Ctrl/Cmd-J and its providers, §19) is Iteration 3; only the query-string-driven Jobs list filtering (§20.4) is built here.
- **Analysis, grouping, results, repositories, and treaties are out.** Portfolio summary and analysis counts on the package card render empty this iteration and are populated later. *(Exception: minimal `irp_analysis` rows ARE captured for delete-enumeration — D2 — but are not surfaced; counts stay empty.)*
- **Notification channel is configurable.** At least one of Teams / email / desktop toast is delivered per the notifications configuration; the exact channel(s) enabled are a configuration choice, not a scope question.
- **Poller scope is minimal.** This iteration polls only the import job types needed for EDM/RDM import (plus the package member operations); the remaining job types arrive with later iterations.
- **No app-created temporary files this iteration.** Imports submit against the shared-drive path directly (no app-side file staging), so FR-008's optional "delete after transfer" behavior is inert until app-side staging exists; the read-only-drive guarantee — the app never writes, moves, or deletes broker files — still holds.
- **Canonical schema lives in DATA_MODEL.md.** This spec defines observable behavior and constraints; the concrete tables, columns, job-type kinds, status vocabularies, and the exact member-job sequencing are derived in planning from DATA_MODEL.md (§3–§9), which is the schema source of truth.
- **External library signatures are verified at implementation.** `irp-integration` is a pre-release library sourced switchably across PyPI (`0.2.0`, production default), TestPyPI (`0.2.1`/`…dev`), and a local editable checkout via uv dependency groups (`make irp-pypi | irp-testpypi | irp-local`; plan §Technical Context / research R1). Its method signatures are verified against the **active** wheel before implementing any Risk-Modeler-backed operation, and single-item Risk Modeler calls are preferred over fail-fast batch helpers. **Confirmed against 0.2.0 on 2026-07-14** — the library is manager-based (`client.edm/.rdm/.import_job/.risk_data_job/.analysis`); see `contracts/worker-poller.md`.
- **Dev database strategy is rebuild.** Any schema needed this iteration is folded into the single existing initial migration; the dev database is dropped, recreated, and seeded (no incremental migration until production cutover). The DB-lifecycle prompt (Rebuild / Refresh / Skip) is run for the WORKBENCH database before schema-affecting work.

## Dependencies

- **Iteration 1 (spec 002)** — submission as the deal, the package/`submission_package` structure and EDM/RDM membership link, the submission status read-only gate, and optimistic concurrency are in place.
- **Iteration 0 (spec 001)** — application shell and navigation manifest, authentication and sessions, function-level roles, and the background-processing infrastructure scaffold (poller process, worker/broker, live-status transport) exist to build on.
- **Risk Modeler (IRP) + `irp-integration`** — a live external dependency for submitting and polling every EDM/RDM import and package member operation; without it, operations that need it are simply not offered, while already-imported entities remain viewable.
- **Shared drive** — mounted read-only into the host for live browsing and file upload; the app never mutates it.
- **DATA_MODEL.md §3–§9** — canonical definitions for the EDM/RDM entities, package membership, the job model (tracked external jobs and app-side work items), member-job sequencing, and results storage referenced by the RDM dedup rule. Records the resolved A21 chaining model (§8) and the added `delete_edm` job-type kind (RDM delete is synchronous, so it has no `irp_job` type).
- **`irp-integration` methods (confirmed vs 0.2.0, 2026-07-14)** — EDM delete = `edm.submit_delete_edm_job(exposure_id)` polled via `risk_data_job.get_risk_data_job`; **RDM delete = `analysis.delete_analysis(id)` per analysis (synchronous)**, enumerated from local `irp_analysis` rows (D2). Full method/request-body matrix in `contracts/worker-poller.md`. The source stays switchable (PyPI `0.2.0` default / TestPyPI / local, research R1); re-confirm signatures if the active source is switched off 0.2.0.
- **CIC team** — confirmation of the notification channel(s) and any ratification of the provisional top-level model carried over from Iteration 1 can arrive after this iteration and be absorbed without a rebuild.
