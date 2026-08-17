# Feature Specification: Package Retirement

**Feature Branch**: `006-package-retirement`

**Created**: 2026-08-12

**Status**: Draft

## Review

Package is removed from the product and database. A submission relates directly
to EDMs and RDMs through separate many-to-many associations. The submission page
shows EDM and RDM tables instead of package cards. An EDM opened from a
submission shows every RDM related to the selected submission and links back only to the
submission used to open the EDM.

The change also adopts the standalone RDM import design from PR #57: an RDM is
imported once against its own exposure set and is never applied to each EDM. The
rest of PR #57 remains reference material because its attach/detach implementation
is built around Package.

The approved previews preserve the current application shell and page components.
Implementation changes only the Package replacement, contextual breadcrumb, EDM
picker, and submission-scoped RDM section.

## Decisions

| ID | Status | Decision |
|---|---|---|
| P-01 | Approved | Remove Package as a user concept and database entity. |
| P-02 | Approved | Relate each EDM and RDM directly to zero or more submissions without copying the Risk Modeler resource. |
| P-03 | Approved | Show separate, always-visible EDM and RDM tables on submission detail. Both tables show Status and a Risk Modeler link for ready resources. The EDM table shows Portfolio count, and the RDM table shows Analysis count. Name, Status, and count are sortable independently in each table; the tables do not have search. The tables do not expand or collapse. Each table refreshes while any listed import or subsequent backfill is non-terminal. |
| P-04 | Approved | In submission context, an EDM page shows every RDM related to the same submission; no EDM-to-RDM relationship is inferred. |
| P-05 | Approved | The EDM context link names only the submission used to open the EDM. Other submission associations appear outside the navigation trail. |
| P-06 | Approved | A submission add action supports both importing a new EDM/RDM and relating an existing EDM/RDM. |
| P-07 | Approved | The add-existing action lists every live EDM/RDM not already related to the target submission. |
| P-08 | Approved | Removing an EDM/RDM from a submission deletes only the association. Physical deletion from Risk Modeler is deferred and is not part of this feature. |
| P-09 | Approved | Use the rendered submission-detail and contextual EDM-detail previews. Implementation reuses the current application shell and existing EDM detail disclosure markup and caret behavior exactly; the previews approve the changed concepts, not a broader redesign. |
| P-10 | Approved | Store one optional note on each EDM and RDM. The note describes the resource, is shared across submissions, and is editable from the detail page and submission tables by every authenticated analyst. |
| T-01 | Proposed | Replace `package`, `submission_package`, and member `package_id` columns with `submission_edm` and `submission_rdm`. |
| T-02 | Proposed | Use `/submissions/{submission_id}/edms/{edm_id}` and `/submissions/{submission_id}/rdms/{rdm_id}` as contextual URLs; retain `/edms/{edm_id}` and `/rdms/{rdm_id}` for library entry with no submission context. |
| T-03 | Proposed | Port only PR #57's standalone-RDM gateway, worker, poller, analysis-capture, fake, and test changes; reimplement association reads and UI against submission joins. |
| T-04 | Proposed | Remove `package_id` from jobs and analyses. Keep EDM/RDM as execution targets and add nullable `requested_from_submission_id` to `irp_job` as provenance only. |
| T-05 | Proposed | Rebuild the pre-go-live WORKBENCH database from the edited single Alembic revision; do not write a package-to-association data converter. |
| T-06 | Approved | Build and verify this feature against `irp-integration` 0.4.0 from TestPyPI. Its `submit_rdm_import_job` method accepts `exposure_set_name`. |

## User Scenarios & Testing

### User Story 1 - Work with submission data without packages (Priority: P1)

An analyst opens a submission and sees its EDMs and RDMs in separate tables.
Each table remains visible and links ready resources to Risk Modeler.

**Independent Test**: Create a submission with two EDMs and two RDMs, open the
submission, and confirm both tables show the four resources with their counts,
sorting controls, and ready-resource Risk Modeler links and with no Package label,
card, action, or route.

**Acceptance Scenarios**:

1. **Given** a submission with several EDMs and RDMs, **When** the analyst opens the submission, **Then** the EDM table shows each EDM's name and portfolio count, the RDM table shows each RDM's name and analysis count, and ready resources have a Risk Modeler link.
2. **Given** a submission with no EDMs or no RDMs, **When** the analyst opens the submission, **Then** the corresponding table shows a specific empty state and an add action.
3. **Given** one EDM related to two submissions, **When** either submission is opened, **Then** the same EDM appears without a copied EDM row or Risk Modeler import.
4. **Given** several resources in either table, **When** the analyst sorts Name, Status, or count, **Then** only that table changes order, the selected order remains during polling, and no table search is shown.
5. **Given** an EDM or RDM whose import is not ready, **When** the analyst opens the submission, **Then** its row has no Risk Modeler link.

### User Story 2 - Add and remove submission data (Priority: P1)

An analyst adds a new or existing EDM/RDM to an active submission. Removing it
from one submission does not affect another submission or Risk Modeler.

**Independent Test**: Import one EDM directly into a submission, relate it to a
second submission, detach it from the first, and confirm the EDM remains visible
under the second submission and in the EDM library.

**Acceptance Scenarios**:

1. **Given** an active submission, **When** the analyst imports a new EDM or RDM from its add action, **Then** the resource and submission association are saved before background import begins.
2. **Given** a live EDM/RDM not already related to the target submission, **When** the analyst opens the add-existing action and selects it, **Then** only the association is inserted and no Risk Modeler call occurs.
3. **Given** a resource related to several submissions, **When** it is removed from one submission, **Then** only that association is deleted.
4. **Given** a completed or cancelled submission, **When** an analyst attempts an add or remove action, **Then** the server rejects the change.
5. **Given** no EDMs/RDMs are available to add, **When** the analyst opens the add-existing tab without searching, **Then** the empty state says none are available to add; after a search, the empty state says no available EDMs/RDMs match the search.

### User Story 3 - Review an EDM in one submission context (Priority: P1)

An analyst opens an EDM from a submission, switches among the submission's EDMs,
and reviews all RDMs related to the selected submission. Analysis rows load only when the
analyst expands an RDM.

**Independent Test**: Relate one EDM to two submissions with different RDM sets.
Navigate to the EDM from each submission and confirm the context link and RDM list match
only the selected submission.

**Acceptance Scenarios**:

1. **Given** an EDM related to two submissions, **When** it is opened from submission A, **Then** the context link names only submission A and the page shows only submission A's RDMs.
2. **Given** several EDMs in the selected submission, **When** the analyst selects another EDM by name, **Then** the contextual EDM URL changes and the submission context remains fixed.
3. **Given** a collapsed RDM row, **When** the analyst expands it, **Then** stored analysis detail loads through an HTMX request and no Risk Modeler call runs in the web process.
4. **Given** an EDM library link with no submission context, **When** the analyst opens it, **Then** the page does not invent a source submission or show submission-scoped RDMs.
5. **Given** an EDM or RDM detail page or submission table, **When** an analyst saves, replaces, or clears its note, **Then** every submission table shows the same complete wrapped note.
6. **Given** two analysts editing the same note, **When** the second analyst saves from a stale editor, **Then** the editor preserves the second analyst's input, identifies the newer saved note, and requires another save to replace it.
7. **Given** an RDM opened from a submission, **When** the detail page renders, **Then** the context link names that submission and the RDM selector contains only RDMs related to that submission.
8. **Given** an RDM related to several submissions, **When** the analyst opens a contextual RDM URL, **Then** the page uses the submission named in the URL and does not choose another association.

### Edge Cases

- The same EDM/RDM is related to multiple submissions with different statuses.
- An association is removed while an entity import or backfill job is running.
- A contextual URL names an EDM or RDM with no association to the submission.
- An existing-resource candidate becomes ineligible before the form is submitted.
- A submission contains 15 EDMs and 15 RDMs, including RDMs with hundreds of analyses.
- An RDM import succeeds but analysis metadata capture is pending or fails.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST remove the `package` entity and every Package user action.
- **FR-002**: The system MUST relate EDMs to submissions through a many-to-many association.
- **FR-003**: The system MUST relate RDMs to submissions through a many-to-many association.
- **FR-004**: Relating an existing EDM/RDM to another submission MUST NOT copy or re-import it.
- **FR-005**: Submission detail MUST render separate EDM and RDM tables.
- **FR-006**: The EDM and RDM tables MUST remain visible, MUST NOT expand or collapse, and MUST include empty states. Both tables MUST show Name and Status. The EDM table MUST show Portfolio count, and the RDM table MUST show Analysis count. Name, Status, and count MUST be independently sortable in each table, and the selected order MUST remain during polling. The tables MUST NOT provide search. A Risk Modeler link MUST appear only when the EDM/RDM status is `ready`. Each table MUST refresh at an interval while any listed import or subsequent backfill is non-terminal and stop refreshing when every listed import and backfill is terminal.
- **FR-007**: An active submission MUST support importing a new EDM or RDM directly.
- **FR-008**: An active submission MUST support relating every live EDM or RDM not already related to that submission. The add-existing empty state MUST distinguish no available EDMs/RDMs before a search from no search matches.
- **FR-009**: Removing an EDM/RDM from a submission MUST delete only the association and MUST NOT delete the EDM/RDM from the workbench or Risk Modeler.
- **FR-010**: Completed and cancelled submissions MUST reject association changes.
- **FR-011**: A contextual EDM page MUST validate the EDM association to the URL's submission.
- **FR-012**: A contextual EDM page MUST show only the URL submission in its context link.
- **FR-013**: A contextual EDM page MUST offer the other EDMs related to the selected submission by name.
- **FR-014**: A contextual EDM page MUST list every RDM related to the selected submission.
- **FR-015**: Listing an RDM beside an EDM MUST NOT claim an EDM-to-RDM or portfolio-to-analysis relationship.
- **FR-016**: RDM analysis detail on the contextual EDM page MUST load only when expanded.
- **FR-017**: The direct library EDM route MUST remain usable without submission context.
- **FR-018**: An RDM import MUST run once against its own exposure set and MUST NOT fan out across EDMs.
- **FR-019**: Broker analysis capture MUST identify analyses by RDM and Risk Modeler analysis ID, with `edm_id` null.
- **FR-020**: Package-based routes, filters, templates, CSS, JavaScript, services, tests, and execution documentation MUST be removed or replaced.
- **FR-021**: All state-changing routes MUST retain CSRF validation.
- **FR-022**: No submission association may restrict row visibility for an authenticated analyst.
- **FR-023**: Each EDM and RDM MUST store one optional note of at most 250 characters; blank or whitespace-only input MUST be stored as null.
- **FR-024**: Every authenticated analyst MUST be able to edit an EDM or RDM note from its detail page or a submission table regardless of submission status.
- **FR-025**: Submission EDM and RDM tables MUST show the complete wrapped note. Double-clicking the note or selecting Edit MUST open a plain-text editor in the table cell; Enter MUST save, Shift+Enter MUST insert a line break, and Escape MUST cancel. Library tables MUST remain unchanged.
- **FR-026**: EDM and RDM note updates MUST validate CSRF and the 250-character limit on the server.
- **FR-027**: Detail polling MUST not replace the detail body while its note editor is open and MUST resume after Save or Cancel.
- **FR-028**: A note update MUST compare the submitted original note with the stored note. A conflict MUST return HTTP 409 with the analyst's input and the newer stored note; saving again MUST confirm replacement.
- **FR-029**: Status, import, and backfill writes MUST NOT create a note conflict unless the stored note changed.
- **FR-030**: A contextual RDM page MUST validate the RDM association to the URL's submission and show only that submission in its context link.
- **FR-031**: A contextual RDM page MUST offer the other RDMs related to the selected submission by name.
- **FR-032**: The direct library RDM route MUST remain usable without submission context.

### Key Entities

- **Submission**: The deal and the only user-facing container for EDMs and RDMs.
- **EDM**: One physical exposure resource that can be related to several submissions.
- **RDM**: One physical broker-results resource that can be related to several submissions and is imported independently of EDMs.
- **Submission EDM**: The association between one submission and one EDM.
- **Submission RDM**: The association between one submission and one RDM.

## Success Criteria

- **SC-001**: Repository search finds no live schema, Python, route, template, CSS, JavaScript, or current execution-document reference to the Package domain concept.
- **SC-002**: One EDM and one RDM can each appear under at least two submissions without duplicate entity rows or duplicate Risk Modeler imports.
- **SC-003**: Contextual EDM tests prove that two source submissions produce different context links and RDM lists for the same EDM.
- **SC-004**: Expanding one RDM fetches only that RDM's stored analysis detail; the initial EDM response does not render every analysis row.
- **SC-005**: The unit tier and SQL Server tier pass after the schema rebuild; the IRP tier verifies one standalone RDM import without an EDM name.

## Assumptions

- The database remains pre-go-live, so the approved schema process is drop-create-seed.
- Historical design notes and completed change-request records remain as historical evidence; current source documents and execution diagrams are updated.
- EDM/RDM association rows do not store a role, ordering value, or pairing metadata.
- The approved previews preserve existing components except where this feature changes content or context.
