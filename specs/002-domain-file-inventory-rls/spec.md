# Feature Specification: Customer, Program & Submission Management, File Inventory, and Access Control

**Feature Branch**: `002-domain-file-inventory-rls`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "Iteration 1 — Domain, file inventory & RLS. Scope per docs/PRD.md §7.1a, §7.2, §7.2a, §7.2b, §8, §6.2, §6.3, §6.4 (see docs/PRD.md build plan §21 'Iteration 1 — Domain, file inventory & RLS' for the exact In/Out/Exit criteria). Covers: customer CSV seeding (upsert by short_code, no delete-on-sync), the Customer → Program → Submission domain model, submission status (ACTIVE/COMPLETED/CANCELLED, event-sourced, no delete) replacing authoring_status, submission name uniqueness per program, file inventory (directory association, immutable append-only file_artifact model, reconciliation scanner, tagging as EDM/RDM, discrepancies, upload storage), customer-access row-level security (user_customer_access, apply_scope() on Submission list, admin bypass), analyst-centric 'my submissions' filter, and the customer-access admin UI. Explicitly out of scope: EDM/RDM entities as first-class tracked records (Iteration 3), Package (Iteration 2), search framework (Iteration 2), ignore ruleset (Iteration 2), and the Workflow/Stage/Task layer (being redesigned separately, not touched by this iteration)."

## Clarifications

### Session 2026-07-02

- Q: How should the submission detail page communicate empty/error states for directories and files? → A: No directory assigned, or an assigned directory that's unreachable/missing, shows an error message. Zero files found in an otherwise-reachable directory shows a warning (not an error). Both states re-evaluate whenever the analyst adds a directory or triggers a refresh — they are not sticky/cached past the next scan.
- Q: What columns does the customer seeding CSV require? → A: Minimal schema — `short_code` and `name` only. No contact/address/metadata fields in this iteration.
- Q: What file-inventory scale should this iteration be designed for? → A: Not very large directories, especially once the (later-iteration) ignore ruleset filters out noise. Start simple — no need to design for pagination, async/background-job scanning at scale, or large-directory optimization in this iteration.
- Q: How should concurrent edits to the same record (e.g. two users changing a submission's status, or two scans updating the same file's inventory state) be handled? → A: Optimistic concurrency — read the record's current `updated_at` (or equivalent version marker) at edit time; if it no longer matches at write-back, reject the write and report the conflict to the user rather than silently overwriting.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Browse and scope submissions by customer access (Priority: P1)

An analyst signs in and sees only the submissions belonging to customers they've been granted access to. From that scoped list they can further narrow to "My Submissions" (assigned to them) or view all submissions across their accessible customers. An admin sees submissions across every customer without needing explicit per-customer access grants.

**Why this priority**: Without customer-scoped access control, every other capability in this iteration (browsing submissions, tagging files, reviewing discrepancies) would leak data across customer boundaries — a hard compliance failure for a system that isolates competing reinsurance clients' data. This is the foundational gate every other story depends on.

**Independent Test**: Can be fully tested by granting a test analyst access to Customer A only, confirming they see Customer A's submissions and not Customer B's, then granting admin access to a second test user and confirming they see both.

**Acceptance Scenarios**:

1. **Given** an analyst has been granted access to Customer A and Customer B, **When** they open the Submissions list, **Then** they see submissions for Customer A and Customer B only, not any other customer.
2. **Given** an analyst has no access grant for Customer C, **When** they attempt to view a submission belonging to Customer C directly (e.g. via a known URL), **Then** the system denies access rather than silently showing the record.
3. **Given** an admin user, **When** they open the Submissions list, **Then** they see submissions across all customers without needing explicit `user_customer_access` grants.
4. **Given** an analyst with several submissions assigned to them and several assigned to teammates (within their accessible customers), **When** they toggle "My Submissions," **Then** the list narrows to only submissions where they are the assigned analyst; toggling to "All" restores the full accessible-scope list.
5. **Given** an admin revokes an analyst's access to Customer A while the analyst has an active session, **When** the analyst's next request loads, **Then** Customer A submissions no longer appear — the change takes effect immediately, not on next login.

---

### User Story 2 - Manage the Customer → Program → Submission hierarchy, including submission status (Priority: P1)

An admin seeds the customer list from a CSV file (bulk onboarding), and analysts work within Programs and Submissions under those customers. Each submission has a name (unique within its program) and a status — `ACTIVE` while being worked, `COMPLETED` when tracking is done (reopenable), or `CANCELLED` if withdrawn. There is no way to delete a submission outright.

**Why this priority**: This is the load-bearing business hierarchy every other entity in the system attaches to (`customer_id` is denormalized everywhere). Nothing else in this iteration — file inventory, RLS scoping, "my submissions" — has anything to scope against until customers, programs, and submissions with correct status semantics exist.

**Independent Test**: Can be fully tested by running the seeding CLI against a sample CSV, confirming customers appear (or update) by short code without any existing customer being removed, then creating a submission, changing its status through the full state set, and confirming the no-delete and uniqueness rules hold.

**Acceptance Scenarios**:

1. **Given** a CSV of customers with short codes, **When** the admin runs the seeding process, **Then** each row inserts a new customer (if the short code is new) or updates the existing customer's fields (if the short code already exists) — no existing customer row is ever removed by seeding, even if its short code is absent from the CSV.
2. **Given** the seeding process is run twice with the same CSV, **When** the second run completes, **Then** the resulting customer data is identical to after the first run (idempotent).
3. **Given** a program already has a submission named "Q1 Property Program", **When** an analyst attempts to create another submission with the same name under the same program, **Then** the system rejects it as a duplicate.
4. **Given** two different programs, **When** an analyst creates a submission named "Q1 Property Program" under each, **Then** both succeed — uniqueness is scoped per program, not global.
5. **Given** a new submission, **When** it is created, **Then** its status is `ACTIVE`.
6. **Given** a submission with status `ACTIVE`, **When** an analyst marks it `COMPLETED`, **Then** no further edits to the submission's files, directories, or tagging are possible while it remains `COMPLETED` (packages are a later-iteration concept, out of scope here — this scenario governs only the file-inventory-level edits this iteration delivers).
7. **Given** a submission with status `COMPLETED`, **When** an analyst changes it back to `ACTIVE`, **Then** editing capability is restored — reopening is allowed with no restriction.
8. **Given** a submission in any status, **When** an analyst marks it `CANCELLED`, **Then** it becomes a withdrawn record; there is no user-facing action to permanently delete a submission from the system, regardless of status.
9. **Given** a submission's status changes, **When** the change is viewed later, **Then** the history of status changes (who, when, and to what status) is available — status changes are recorded, not just overwritten.

---

### User Story 3 - Build and maintain the file inventory from shared-drive directories (Priority: P2)

An analyst associates one or more shared-drive directories with a submission. The system scans those directories and tracks every file it finds as an inventory entry, detecting when files are added, changed, or go missing — without ever modifying, moving, or deleting the original files. The analyst tags relevant files as EDM or RDM sources and can upload files directly when they aren't on the shared drive.

**Why this priority**: File inventory is the second major pillar of this iteration's scope alongside the domain model, but it depends on a submission existing first (Story 2), which is why it's P2 rather than P1. It's still core to the iteration — without it, there's no way to track what broker-supplied files exist for a submission at all.

**Independent Test**: Can be fully tested by pointing a test submission at a directory containing sample files, triggering a scan, confirming all files appear as inventory entries, then modifying a file on disk and re-scanning to confirm the change is detected without data loss.

**Acceptance Scenarios**:

1. **Given** a submission with no associated directories, **When** an analyst associates a shared-drive directory, **Then** the directory becomes linked to the submission and is available for scanning.
2. **Given** a directory linked to a submission with files present, **When** a scan runs (triggered automatically or via "Refresh inventory"), **Then** every previously-untracked file becomes a new inventory entry.
3. **Given** a tracked file that has not changed since the last scan, **When** a scan runs again, **Then** no new entry is created and the existing entry is left as-is.
4. **Given** a tracked file whose content or modification time has changed, **When** a scan runs, **Then** the prior entry is preserved (marked as superseded) and a new entry is added reflecting the current file — the file's history is never overwritten or lost.
5. **Given** a tracked file that has been removed from the shared drive, **When** a scan runs, **Then** the entry is marked as missing rather than deleted from the inventory.
6. **Given** a file was recently modified and may still be mid-copy, **When** a scan runs immediately after, **Then** the system avoids fingerprinting it prematurely (waits for the file to "settle" before recording it as changed).
7. **Given** an inventory entry for a file, **When** an analyst tags it as an EDM source or RDM source, **Then** the tag is recorded and the file becomes eligible for downstream use as an EDM/RDM source.
8. **Given** a file an analyst wants to include that isn't on the shared drive, **When** they upload it directly, **Then** it becomes an inventory entry through the same tracking model as shared-drive files, stored separately from the read-only shared-drive mount.
9. **Given** a tracked, tagged file changes or disappears, **When** the scan detects it, **Then** a discrepancy is raised, with higher severity than an untagged file experiencing the same change.
10. **Given** the shared drive is mounted read-only, **When** any scan or file operation runs, **Then** the system never writes to, moves, or deletes the original broker-supplied files.

---

### Edge Cases

- What happens when the CSV seeding file contains a malformed or duplicate short code within the same file? The system should reject or report the bad row without aborting the entire seeding run for valid rows.
- What happens when an analyst tries to rename a submission to match an existing name within the same program? The rename is rejected the same way creation is (per FR-010).
- What happens when a directory is associated with a submission whose customer the current analyst doesn't have access to? This should not be possible via the UI, but the underlying scope check must still hold if attempted directly.
- What happens when two scans of the same directory run concurrently (e.g. triggered by two different events at once)? The scan process must not create duplicate inventory entries for the same file version.
- What happens when a submission is `COMPLETED` and a background scan trigger fires anyway (e.g. someone else's workflow references a file in it)? Read-only inventory operations (viewing, scanning for discrepancy detection) continue; only analyst-initiated edits are blocked by `COMPLETED` status.
- What happens when an admin revokes their own admin access? Out of scope for this iteration — assume admin self-modification safeguards exist at the platform level or are handled by a separate admin-management concern.
- What happens when a shared-drive directory becomes unreachable (network share down) during a scan, or a submission has no directory associated at all? The submission detail page shows an error state (not a silent empty list) in both cases; the scan does not mark previously-tracked files as missing just because the directory was momentarily unreachable.
- What happens when a submission has at least one reachable directory but it contains zero files? The submission detail page shows a warning state, distinct from the error state used for a missing/unreachable directory.
- What happens when an analyst adds a directory, or clicks refresh, after seeing an error or warning state? The state is re-evaluated immediately as part of that action — it is not cached or left stale until some later scan.
- What happens when two users (or two processes) attempt to write to the same submission or file-inventory record at the same time? The second write to reach the database detects that the record's version marker no longer matches what it read, rejects the write, and reports the conflict to the user rather than silently overwriting the first write.

## Requirements *(mandatory)*

### Functional Requirements

**Customer seeding**

- **FR-001**: System MUST provide a way for an admin to bulk-load customer records from a CSV file containing, at minimum, a short code column and a name column — no other columns are required in this iteration.
- **FR-002**: System MUST treat the customer's short code as the unique matching key when seeding — a row with a short code that already exists updates that customer; a row with a new short code creates a new customer.
- **FR-003**: System MUST NOT delete or deactivate any existing customer record as a result of running the seeding process, even if that customer's short code is absent from the CSV being loaded.
- **FR-004**: System MUST be safe to re-run the seeding process with the same input file without producing duplicate customers or changing outcomes beyond the first run.

**Customer → Program → Submission domain model**

- **FR-005**: System MUST support a hierarchy where every Program belongs to exactly one Customer, and every Submission belongs to exactly one Program.
- **FR-006**: System MUST record which Customer a Submission belongs to directly on the Submission (not only derivable by walking through Program), so that access-scoping checks do not require a join through Program on every request.
- **FR-007**: System MUST allow a Submission to be assigned to one analyst (the "assigned analyst").
- **FR-008**: System MUST record who created and who last updated each Customer, Program, and Submission record, and when.

**Submission status**

- **FR-009**: System MUST support exactly three submission statuses: `ACTIVE`, `COMPLETED`, and `CANCELLED` — no other status values are valid.
- **FR-010**: System MUST default a newly-created submission's status to `ACTIVE`.
- **FR-011**: System MUST allow a submission to transition from `COMPLETED` back to `ACTIVE` (reopening) at any time.
- **FR-012**: System MUST NOT enforce any precondition (such as "all files must be present" or "no open discrepancies") before allowing a status transition — the assigned analyst or an authorized user decides when to change status.
- **FR-013**: System MUST prevent analyst-initiated edits to a submission's files, directories, and tagging while its status is `COMPLETED`; changing status back to `ACTIVE` restores edit capability.
- **FR-014**: System MUST NOT provide any way to permanently delete a submission record. `CANCELLED` is the only withdrawal mechanism.
- **FR-015**: System MUST record every submission status change as an entry in a permanent history (who changed it, to what value, and when), in addition to tracking the submission's current status.

**Submission uniqueness**

- **FR-016**: System MUST prevent two submissions from having the same name within the same Program.
- **FR-017**: System MUST allow the same submission name to be reused across different Programs.

**Customer-access row-level security**

- **FR-018**: System MUST restrict which customers' data (including submissions and everything under them) a non-admin user can view to only those customers they have been explicitly granted access to.
- **FR-019**: System MUST allow an admin role to view data across all customers without requiring explicit per-customer access grants.
- **FR-020**: System MUST apply the customer-access restriction to every submission list, detail view, and related-data view in this iteration — there must be no code path that bypasses the scope check for a non-admin user.
- **FR-021**: System MUST apply access-scope changes (granting or revoking a user's access to a customer) immediately on the affected user's next request — not only after their next login or session refresh.
- **FR-022**: System MUST provide an admin-facing interface to grant and revoke a user's access to specific customers.

**Analyst-centric views**

- **FR-023**: System MUST provide a "My Submissions" view showing only submissions assigned to the current user, as the default view for analysts.
- **FR-024**: System MUST provide a way to toggle from "My Submissions" to a view of all submissions the current user has access to (across their accessible customers, or all customers for an admin).

**File inventory & artifacts**

- **FR-025**: System MUST allow an analyst to associate one or more shared-drive directory paths with a submission.
- **FR-026**: System MUST treat each associated directory path as unique across the system — the same path cannot be associated with the system twice.
- **FR-027**: System MUST scan associated directories to discover files and record each discovered file as an inventory entry, without requiring a user request to trigger every scan (background/triggered scanning).
- **FR-028**: System MUST trigger a scan at minimum when: a directory is added to or removed from a submission, an analyst opens the submission's detail page, and an analyst explicitly requests a refresh.
- **FR-029**: System MUST identify a file's version using inexpensive, readily-available file metadata (such as path, size, and modification time) rather than reading and hashing full file contents.
- **FR-030**: System MUST preserve prior versions of a tracked file when a change is detected — never overwrite or discard history of a file's prior inventory state.
- **FR-031**: System MUST mark a previously-tracked file as missing (not delete its inventory record) when it is no longer found during a scan.
- **FR-032**: System MUST avoid treating a file that is still being written or copied as a stable, scannable version — it must wait for the file to stop changing for a defined period before recording it.
- **FR-033**: System MUST allow an analyst to tag an inventory entry as representing an EDM source file or an RDM source file.
- **FR-034**: System MUST support uploading a file directly (not sourced from the shared drive) and tracking it in the same inventory model, stored in a location separate from the read-only shared-drive mount.
- **FR-035**: System MUST treat uploaded files as immutable once uploaded.
- **FR-036**: System MUST NOT write to, move, rename, or delete any file on the read-only shared-drive mount under any circumstance.
- **FR-037**: System MUST raise a discrepancy record when a tracked file changes or goes missing.
- **FR-038**: System MUST assign a higher discrepancy severity when the affected file was tagged (EDM/RDM) than when it was untagged.
- **FR-039**: System MUST surface discrepancies to the user — at minimum, a count visible from the submission and a dedicated list of discrepancies.
- **FR-040**: System MUST allow discrepancies to be marked resolved.
- **FR-041**: System MUST show an error state on the submission detail page when the submission has no associated directory, or when an associated directory cannot be reached.
- **FR-042**: System MUST show a warning state (distinct from the error state in FR-041) when an associated directory is reachable but contains no files.
- **FR-043**: System MUST re-evaluate the error/warning state described in FR-041/FR-042 immediately whenever an analyst adds a directory or triggers a refresh — the state must not remain stale past that action.
- **FR-044**: System MUST NOT mark previously-tracked files as missing solely because a directory scan failed due to the directory being temporarily unreachable — a failed scan attempt is distinct from a confirmed absent file.

**Concurrency**

- **FR-045**: System MUST detect, at write time, whether a record (submission or file inventory entry) being updated has changed since it was last read by the user making the change.
- **FR-046**: System MUST reject a write when the underlying record has changed since it was read (optimistic concurrency), and MUST report the conflict to the user rather than silently overwriting the intervening change.

### Key Entities

- **Customer**: A reinsurance client the workbench does work for. Has a name and a unique short code used for identification and, in later iterations, auto-naming. The root of the access-scoping hierarchy — every other entity in this iteration traces back to a Customer.
- **Program**: A grouping of submissions under a Customer. Purely organizational in this iteration — no independent status or lifecycle of its own.
- **Submission**: The analyst's core unit of work. Belongs to one Program (and, denormalized, one Customer). Has a name (unique within its Program), an assigned analyst, and a status (`ACTIVE` / `COMPLETED` / `CANCELLED`) with a permanent history of status changes. Anchors directories and file inventory in this iteration (anchors EDM/RDM records, packages, and workflows in later iterations, out of scope here).
- **Submission Directory**: A shared-drive path associated with a submission for file scanning. Each path is unique across the system.
- **File Artifact (inventory entry)**: One version of a discovered or uploaded file. Immutable and append-only — a detected change creates a new entry rather than modifying the old one. Carries a source (shared drive vs. upload), a status (present / changed / missing), and an optional tag (EDM / RDM).
- **Discrepancy**: A flagged issue raised when a tracked file changes or disappears unexpectedly. Has a severity that escalates based on whether the affected file was tagged, and a resolved/unresolved state.
- **User-Customer Access Grant**: A record of which customers a specific (non-admin) user is permitted to see data for. Absence of a grant means no access to that customer's data; admins bypass this check entirely.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An admin can seed or update the full customer list from a CSV file in a single operation, with zero existing customers lost or altered incorrectly, regardless of how many times the file is loaded.
- **SC-002**: A non-admin user viewing any submission list or detail page never sees a submission belonging to a customer they have not been granted access to — verified with zero exceptions across all list, detail, and related-data views delivered in this iteration.
- **SC-003**: An access grant change (add or revoke) made by an admin is reflected in the affected user's view on their very next request, with no perceptible delay tied to login or caching.
- **SC-004**: Analysts can find their own active work at a glance — the "My Submissions" default view requires no additional filtering step to see only assigned work.
- **SC-005**: Two submissions can never silently collide under the same program — 100% of duplicate-name attempts within the same program are rejected at creation or rename time.
- **SC-006**: No submission is ever permanently removed from the system through normal use — the only two states of finality available to a user are `COMPLETED` (reversible) and `CANCELLED` (a tracked outcome, not an erasure).
- **SC-007**: For a directory of files that is scanned repeatedly with no changes, the file inventory reflects zero spurious new entries or lost history across any number of re-scans.
- **SC-008**: When a tracked file on the shared drive changes, an analyst can see both the fact that it changed and a full, unbroken history of that file's prior tracked versions — never just the latest state with no trail.
- **SC-009**: The original broker-supplied files on the shared drive are never altered by the system — verifiable by comparing file content/metadata on the shared drive before and after any scan or inventory operation.
- **SC-010**: A user is never able to silently overwrite another user's or process's concurrent change to a submission or file-inventory record — every such conflict is surfaced to the user attempting the second write, with zero silent data loss from lost updates.

## Assumptions

- "Admin" and "analyst" are treated as the two relevant roles for this iteration's access-control behavior; any additional roles introduced elsewhere in the system are out of scope for the acceptance criteria here.
- The read-only shared-drive mount (CIFS/SMB or equivalent) is provisioned and reachable as an existing platform dependency — provisioning that mount itself is out of scope for this feature.
- "Settle window" duration (how long a file must be unchanged before being treated as stable) is an operational tuning value, not a user-facing setting, and its exact value is a reasonable default chosen during implementation rather than specified here.
- EDM/RDM tagging in this iteration only marks a file inventory entry with a tag — it does not create or manage the first-class EDM/RDM tracked-entity records described for a later iteration; that distinction is intentional and preserved here.
- The customer-access grant model is additive/allow-list only (a grant permits access; there is no separate explicit "deny" grant) — consistent with existing platform conventions for this kind of scoping.
- File identity based on path, size, and modification time (rather than content hashing) is an accepted trade-off for large files where hashing would be prohibitively expensive; this may occasionally miss a same-size, same-timestamp content change, which is an accepted limitation rather than a defect.
- Directory scale is modest, not enterprise-scale — especially once the (later-iteration) ignore ruleset filters out noise. This iteration is not designed for pagination, background/async scanning at scale, or large-directory optimization; a straightforward synchronous or lightly-deferred scan is sufficient.
